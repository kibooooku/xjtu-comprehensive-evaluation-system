"""A genuine zero, pending estimate and student's confirmed zero remain distinct."""
import pytest

from app import db, jobs, materials, workflow
from app.score_resolution import current, from_policy
from test_materials import material_app, create_claim, upload


@pytest.mark.parametrize('value', ['0.00', '0.30', '9.00'])
def test_policy_amounts_including_zero_are_resolved(value):
    policy = {'status': 'RULE_APPLIED', 'requires_confirmation': False, 'rule': 'TEST_RULE'}
    result = from_policy(policy, '0.00', value)
    assert result['status'] == 'RULE_APPLIED' and result['value'] == value
    assert result['missing_facts'] == []


def test_unresolved_score_never_reports_a_zero_as_calculated():
    result = from_policy({'status': 'NEEDS_REVIEW', 'requires_confirmation': True,
                          'missing_facts': [{'code': 'AWARD_LEVEL', 'label': '赛事级别'}]}, '0', '0')
    assert result['status'] == 'NEEDS_REVIEW' and result['value'] is None
    assert result['retained_value'] == '0.00'
    assert result['missing_facts'] == [{'code': 'AWARD_LEVEL', 'label': '赛事级别'}]


@pytest.mark.parametrize('invalid', [None, 'NaN', 'Infinity', '-1', 'not-a-number'])
def test_invalid_computed_amount_is_not_silently_turned_into_a_legitimate_zero(invalid):
    result = from_policy({'status': 'RULE_APPLIED', 'requires_confirmation': False}, '0', invalid)
    assert result['status'] == 'NEEDS_REVIEW' and result['value'] is None


def test_legacy_unvalidated_model_score_does_not_pass_as_determined():
    revision = {'requested_score': '0', 'payload_json': {}, 'student_modified_score': False}
    analysis = {'status': 'SUCCEEDED', 'suggested_score': '0', 'result_json': {'score_policy': {'status': 'NOT_APPLICABLE'}, 'suggestion': {'requested_score': '0'}}}
    assert current(revision, analysis)['status'] == 'NEEDS_REVIEW'


@pytest.mark.parametrize('marker', ['modified', 'confirmed', 'frozen'])
def test_real_manual_or_frozen_zero_is_never_replaced_with_pending(marker):
    revision = {'requested_score': '0', 'payload_json': {}, 'student_modified_score': marker == 'modified',
                'content_frozen_at': '2026-10-26' if marker == 'frozen' else None}
    if marker == 'confirmed':
        revision['payload_json']['_score_confirmation'] = {'confirmed': True, 'value': '0.00'}
    result = current(revision, {'status': 'SUCCEEDED', 'result_json': {'score_policy': {'status': 'NEEDS_REVIEW'}}}, material_changed=True)
    assert result['status'] == 'MANUAL' and result['value'] == '0.00'


def test_editing_materials_invalidates_only_automatic_score():
    revision = {'requested_score': '1', 'payload_json': {}, 'student_modified_score': False}
    analysis = {'status': 'SUCCEEDED', 'result_json': {'score_policy': {'status': 'RULE_APPLIED'}, 'suggestion': {'requested_score': '1'}}}
    result = current(revision, analysis, material_changed=True)
    assert result['status'] == 'NEEDS_REVIEW' and result['retained_value'] == '1.00'
    assert result['value'] is None


def test_note_save_does_not_confirm_pending_zero_but_explicit_zero_confirmation_does(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    assert client.put('/api/applications/' + claim['id'], json={'title': '虚构竞赛奖项'}).status_code == 200
    upload(client, claim['id'])
    with db.write() as conn:
        app = db.one(conn, 'applications', claim['id'])
        rev = db.one(conn, 'application_revisions', app['working_revision_id'])
        context = workflow.app_context(conn, app['id'])
        analysis = db.add(conn, 'ai_analyses', application_id=app['id'], input_revision_id=rev['id'], input_fingerprint='synthetic',
                          score_rule_version_id=context['class_year']['score_rule_version_id'], model_name='mock', prompt_version='test', status='SUCCEEDED',
                          suggested_title=rev['title'], suggested_score=db.money(0), suggested_activity_id=None,
                          result_json={'score_policy': {'status': 'NEEDS_REVIEW', 'requires_confirmation': True,
                                                       'missing_facts': [{'code': 'AWARD_LEVEL', 'label': '赛事级别'}]},
                                       'suggestion': {'requested_score': '0'}})
        db.change(conn, 'application_revisions', rev['id'], requested_score=db.money(0), student_modified_score=False,
                  ai_analysis_id=analysis['id'], payload_json={})
    detail = client.get('/api/applications/' + claim['id']).json()
    assert detail['score_resolution']['status'] == 'NEEDS_REVIEW'
    note = client.put('/api/applications/' + claim['id'], json={'payload_json': {'note': '学生补充说明'}})
    assert note.status_code == 200 and note.json()['score_resolution']['status'] == 'NEEDS_REVIEW'
    confirmed = client.put('/api/applications/' + claim['id'], json={'requested_score': '0', 'score_confirmed': True})
    assert confirmed.status_code == 200
    assert confirmed.json()['score_resolution']['status'] == 'MANUAL' and confirmed.json()['score_resolution']['value'] == '0.00'
    assert confirmed.json()['student_modified_score'] is False
    assert client.get('/api/applications/' + claim['id']).json()['score_resolution']['status'] == 'MANUAL'
    submitted = client.post('/api/applications/' + claim['id'] + '/submit', json={})
    assert submitted.status_code == 200
    assert submitted.json()['score_resolution']['value'] == '0.00'


def test_client_cannot_implicitly_mark_pending_zero_confirmed_inside_generic_payload(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    response = client.put('/api/applications/' + claim['id'], json={'payload_json': {'_score_confirmation': {'confirmed': True, 'value': '0'}}})
    assert response.status_code == 200
    assert response.json()['score_resolution']['status'] == 'UNANALYZED'
    assert '_score_confirmation' not in response.json()['revision']['payload_json']


def test_direct_submission_itself_confirms_the_students_current_zero(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    assert client.put('/api/applications/' + claim['id'], json={'title': '虚构活动'}).status_code == 200
    upload(client, claim['id'])
    submitted = client.post('/api/applications/' + claim['id'] + '/submit', json={})
    assert submitted.status_code == 200
    assert submitted.json()['requested_score'] == submitted.json()['score_resolution']['value'] == '0.00'
    assert submitted.json()['revision']['payload_json']['_score_confirmation']['via'] == 'SUBMIT'


def test_stale_form_cannot_overwrite_a_newer_confirmed_score(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    original_version = claim['lock_version']
    changed = client.put('/api/applications/' + claim['id'], json={'requested_score': '1.50', 'score_confirmed': True,
                                                                 'expected_version': original_version})
    assert changed.status_code == 200
    stale = client.put('/api/applications/' + claim['id'], json={'title': '旧标签页改的标题', 'requested_score': '0',
                                                               'expected_version': original_version})
    assert stale.status_code == 409
    current_value = client.get('/api/applications/' + claim['id']).json()
    assert current_value['score_resolution']['status'] == 'MANUAL'
    assert current_value['requested_score'] == current_value['score_resolution']['value'] == '1.50'


def test_applying_partial_analysis_preserves_an_explicitly_confirmed_zero(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    upload(client, claim['id'])
    assert client.put('/api/applications/' + claim['id'], json={'title': '虚构活动名称', 'requested_score': '0', 'score_confirmed': True}).status_code == 200
    with db.write() as conn:
        context = workflow.app_context(conn, claim['id'])
        rev = db.one(conn, 'application_revisions', context['application']['working_revision_id'])
        analysis = db.add(conn, 'ai_analyses', application_id=claim['id'], input_revision_id=rev['id'],
                          input_fingerprint=materials.revision_fingerprint(conn, rev),
                          score_rule_version_id=context['class_year']['score_rule_version_id'], model_name='mock', prompt_version='test',
                          status='SUCCEEDED', suggested_title='虚构活动名称', suggested_score=db.money(0), suggested_activity_id=None,
                          result_json={'score_policy': {'status': 'NEEDS_REVIEW', 'requires_confirmation': True},
                                       'suggestion': {'title': '虚构活动名称', 'requested_score': '0', 'payload_json': {}, 'suggested_crops': []}})
    applied = client.post('/api/applications/' + claim['id'] + '/apply-analysis', json={'analysis_id': analysis['id']})
    assert applied.status_code == 200
    assert applied.json()['score_resolution']['status'] == 'MANUAL'
    assert applied.json()['score_resolution']['value'] == '0.00'
    assert client.get('/api/applications/' + claim['id']).json()['score_resolution']['status'] == 'MANUAL'


def test_saving_semantically_empty_fields_does_not_claim_a_manual_edit(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    saved = client.put('/api/applications/' + claim['id'], json={'payload_json': {'note': '', 'award_level': None, 'activity_count': ''}})
    assert saved.status_code == 200
    assert not saved.json()['revision']['payload_json'].get('content_manually_edited')
    with db.read() as conn:
        revision = db.one(conn, 'application_revisions', saved.json()['revision']['id'])
        assert jobs._pristine(conn, revision)
