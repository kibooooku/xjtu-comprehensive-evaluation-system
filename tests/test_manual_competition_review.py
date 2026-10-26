"""Duplicates are a reviewer decision; submitting never rewrites another award."""
import pytest

from app import db, workflow
from app.migrations import initialize_v2
from test_workflow import flow
from test_submissions_v2 import v2
from app.activity_matching import router as matching_router


@pytest.mark.parametrize('category', ['RESEARCH', 'ARTS'])
def test_approved_same_project_items_sum_even_with_legacy_group_and_max_setting(flow, category):
    activity = flow['activity'](category=category)
    facts = {'edition_key': '2026', 'track_key': 'same-track', 'work_key': 'same-work'}
    apps = [flow['submit'](flow['draft'](amount, category, title='同一竞赛', facts=facts, activity_id=activity['id']))
            for amount in ('1.50', '2.50')]
    with db.write() as conn:
        for app in apps:
            db.change(conn, 'submission_rounds', app['round_id'], competition_group_key='legacy-same-project')
        db.change(conn, 'score_categories', flow['cats'][category], aggregation_kind='MAX')
    for app in apps:
        assert flow['review'](app)['state'] == 'APPROVED'
    with db.read() as conn:
        result = workflow.class_scores(conn, flow['class']['id'], flow['year']['id'])
    mine = next(member for member in result['members'] if member['user_id'] == flow['users']['student']['id'])
    summary = next(item for item in mine['categories'] if item['code'] == category)
    assert summary['raw'] == summary['score'] == '4.00'
    assert len(summary['items']) == 2 and all(item['counted'] for item in summary['items'])


@pytest.mark.parametrize('category,amounts,raw,cap', [('RESEARCH', ('8', '9'), '17.00', '10.00'),
                                                   ('ARTS', ('4', '5'), '9.00', '6.00')])
def test_manual_competition_review_keeps_category_caps(flow, category, amounts, raw, cap):
    activity = flow['activity'](category=category)
    for amount in amounts:
        flow['review'](flow['submit'](flow['draft'](amount, category, title='同项目不同证明', activity_id=activity['id'])))
    with db.read() as conn:
        result = workflow.class_scores(conn, flow['class']['id'], flow['year']['id'])
    mine = next(member for member in result['members'] if member['user_id'] == flow['users']['student']['id'])
    summary = next(item for item in mine['categories'] if item['code'] == category)
    assert summary['raw'] == raw and summary['score'] == cap
    assert all(item['counted'] for item in summary['items'])


@pytest.mark.parametrize('action', ['WITHDRAW', 'REJECT'])
def test_legacy_supersession_history_is_not_automatically_restored(flow, action):
    activity = flow['activity']()
    old = flow['review'](flow['submit'](flow['draft']('1', 'RESEARCH', activity_id=activity['id'])))
    new = flow['submit'](flow['draft']('2', 'RESEARCH', activity_id=activity['id']))
    with db.write() as conn:
        old_app = db.one(conn, 'applications', old['id'])
        old_round = db.one(conn, 'submission_rounds', old['round_id'])
        old_version = old_round['lock_version']
        event = workflow._event(conn, old_app, 'AWARD_SUPERSEDED', round_=old_round, from_state='APPROVED',
                                to_state='REJECTED', detail={'new_round_id': new['round_id']})
        db.change(conn, 'submission_rounds', old_round['id'], is_current_candidate=False)
        legacy = db.add(conn, 'award_supersessions', new_round_id=new['round_id'], old_round_id=old_round['id'],
                        competition_group_key='legacy-key', old_state='APPROVED', old_lock_version=old_version,
                        superseded_event_id=event['id'], restored_event_id=None, status='ACTIVE',
                        comparison_snapshot_json={'old_score': '1', 'new_score': '2'}, created_at=db.now(), resolved_at=None)
        frozen = db.one(conn, 'application_revisions', old['revision_id'])
    if action == 'WITHDRAW':
        response = flow['client'].post(f"/api/applications/{new['id']}/withdraw", json={})
        assert response.status_code == 200
    else:
        flow['review'](new, action='REJECT', reason='DUPLICATE_COMPETITION')
    with db.read() as conn:
        assert db.one(conn, 'submission_rounds', old['round_id'])['state'] == 'REJECTED'
        assert db.one(conn, 'submission_rounds', old['round_id'])['last_event_id'] == event['id']
        assert db.one(conn, 'award_supersessions', legacy['id']) == legacy
        assert db.one(conn, 'application_revisions', old['revision_id']) == frozen
    flow['login']('counselor')
    assert flow['client'].post(f"/api/applications/{old['id']}/review", json={'action': 'REJECT', 'reason': 'OLD_HISTORY'}).status_code == 409


def test_submit_itself_confirms_zero_without_separate_confirmation(flow):
    app = flow['draft']('0', title='学生选择0分的条目')
    assert app['score_resolution']['status'] in ('UNANALYZED', 'NEEDS_REVIEW')
    assert app['score_resolution']['value'] is None
    submitted = flow['submit'](app)
    assert submitted['state'] == 'PENDING_PEER' and submitted['requested_score'] == '0.00'
    assert submitted['score_resolution']['value'] == '0.00'
    with db.read() as conn:
        revision = db.one(conn, 'application_revisions', submitted['revision_id'])
        assert revision['payload_json']['_score_confirmation']['via'] == 'SUBMIT'


def test_stale_submit_version_cannot_confirm_or_freeze_the_newer_draft(flow):
    app = flow['draft']('0', title='原草稿')
    changed = flow['client'].put(f"/api/applications/{app['id']}", json={'title': '另一个页面的新草稿'})
    assert changed.status_code == 200
    rejected = flow['client'].post(f"/api/applications/{app['id']}/submit", json={'expected_version': app['lock_version']})
    assert rejected.status_code == 409
    with db.read() as conn:
        revision = db.one(conn, 'application_revisions', app['revision_id'])
        assert not revision['content_frozen_at'] and '_score_confirmation' not in revision['payload_json']


def test_batch_submit_confirms_zero_and_respects_each_items_version(v2):
    parent = v2['group']()
    first, second = v2['item'](parent, title='零分可直接提交', score='0'), v2['item'](parent, title='旧页面的另一项', score='0')
    v2['confirm'](parent, [first, second])
    client = v2['client']
    before = {app['id']: client.get(f"/api/applications/{app['id']}").json()['lock_version'] for app in (first, second)}
    assert client.put(f"/api/applications/{second['id']}", json={'title': '新页面修改了标题'}).status_code == 200
    response = client.post(f"/api/submissions/{parent['id']}/submit", json={'item_ids': [first['id'], second['id']],
                            'score_confirmed': True, 'expected_versions': before})
    assert response.status_code == 200
    results = {item['id']: item for item in response.json()['results']}
    assert results[first['id']]['ok'] and results[second['id']]['status_code'] == 409
    assert client.get(f"/api/applications/{first['id']}").json()['state'] == 'PENDING_PEER'
    assert client.get(f"/api/applications/{second['id']}").json()['state'] == 'DRAFT'


def test_aggregation_upgrade_does_not_rewrite_old_rounds_or_supersessions(flow):
    app = flow['submit'](flow['draft']('2', 'RESEARCH'))
    with db.write() as conn:
        db.change(conn, 'score_categories', flow['cats']['RESEARCH'], aggregation_kind='PROJECT_MAX')
        db.change(conn, 'score_categories', flow['cats']['ARTS'], aggregation_kind='PROJECT_MAX')
        original_round = db.one(conn, 'submission_rounds', app['round_id'])
        original_revision = db.one(conn, 'application_revisions', app['revision_id'])
    initialize_v2(); initialize_v2()
    with db.read() as conn:
        assert db.one(conn, 'score_categories', flow['cats']['RESEARCH'])['aggregation_kind'] == 'SUM'
        assert db.one(conn, 'score_categories', flow['cats']['ARTS'])['aggregation_kind'] == 'SUM'
        assert db.one(conn, 'score_categories', flow['cats']['POST'])['aggregation_kind'] == 'MAX'
        assert db.one(conn, 'submission_rounds', app['round_id']) == original_round
        assert db.one(conn, 'application_revisions', app['revision_id']) == original_revision
        assert not db.rows(conn, 'award_supersessions')
    assert list((db.storage_root / 'backups').glob('pre-v2-*.db'))


def test_unmatched_post_needs_counselor_qualification(flow):
    application = flow['submit'](flow['draft']('1.50', 'POST', title='班长任职'))
    after_peer = flow['review'](application)
    assert after_peer['state'] == 'PENDING_COUNSELOR'
    assert flow['review'](after_peer, 'counselor')['state'] == 'APPROVED'


def test_known_valid_post_can_finish_at_peer_review(flow):
    activity = flow['activity']('VALID', category='POST')
    application = flow['submit'](flow['draft']('1.50', 'POST', title='已认定班长任职', activity_id=activity['id']))
    assert flow['review'](application)['state'] == 'APPROVED'


def test_reward_without_activity_match_does_not_require_qualification_stage(flow):
    application = flow['submit'](flow['draft']('2', 'REWARD', title='校级精神文明荣誉'))
    assert application['revision']['activity_id'] is None
    assert flow['review'](application)['state'] == 'APPROVED'


def test_reward_ignores_an_old_hidden_invalid_activity_link(flow):
    activity = flow['activity']('INVALID', category='REWARD')
    application = flow['submit'](flow['draft']('2', 'REWARD', title='校级精神文明荣誉', activity_id=activity['id']))
    assert application['round']['eligibility_version_id'] is None
    assert flow['review'](application)['state'] == 'APPROVED'


@pytest.mark.parametrize('category', ['RESEARCH', 'ARTS', 'POST'])
def test_visible_matching_categories_still_block_a_known_invalid_activity(flow, category):
    activity = flow['activity']('INVALID', category=category)
    application = flow['draft']('1', category, title='无效资格条目', activity_id=activity['id'])
    response = flow['client'].post(f"/api/applications/{application['id']}/submit", json={})
    assert response.status_code == 400 and '无加分资格' in response.json()['detail']


def test_qualification_routing_upgrade_preserves_rounds_and_only_changes_three_categories(flow):
    application = flow['submit'](flow['draft']('2', 'REWARD'))
    with db.write() as conn:
        db.change(conn, 'score_categories', flow['cats']['POST'], requires_eligibility_review=False)
        db.change(conn, 'score_categories', flow['cats']['REWARD'], requires_eligibility_review=True)
        original_round = db.one(conn, 'submission_rounds', application['round_id'])
        original_revision = db.one(conn, 'application_revisions', application['revision_id'])
    initialize_v2(); initialize_v2()
    with db.read() as conn:
        for category in db.rows(conn, 'score_categories'):
            assert bool(category['requires_eligibility_review']) == (category['code'] in {'RESEARCH', 'ARTS', 'POST'})
        assert db.one(conn, 'submission_rounds', application['round_id']) == original_round
        assert db.one(conn, 'application_revisions', application['revision_id']) == original_revision


@pytest.mark.parametrize('category', ['RESEARCH', 'ARTS', 'POST'])
def test_hidden_scope_conflict_is_unknown_in_search_submission_and_peer_review(flow, category):
    flow['client'].app.include_router(matching_router)
    activity = flow['activity']('VALID', category=category)
    flow['login']('counselor')
    response = flow['client'].put('/api/activities/' + activity['id'], json={
        'category_id': flow['cats'][category], 'decision': 'INVALID', 'track_key': 'legacy-hidden-track'})
    assert response.status_code == 200
    with db.read() as conn:
        versions = db.rows(conn, 'activity_eligibility_versions', activity_id=activity['id'])
    app = flow['draft']('1', category, activity_id=activity['id'], facts={'track_key': 'legacy-hidden-track'})
    assert app['eligibility']['decision'] == 'UNKNOWN'
    search = flow['client'].get('/api/activities/search', params={
        'q': activity['canonical_name'], 'year_id': flow['year']['id'], 'category_id': flow['cats'][category]})
    assert search.status_code == 200
    found = next(item for item in search.json()['items'] if item['id'] == activity['id'])
    assert found['decision'] == 'UNKNOWN' and found['eligibility']['version_id'] is None
    submitted = flow['submit'](app)
    assert submitted['round']['eligibility_version_id'] is None
    assert submitted['revision']['payload_json']['track_key'] == 'legacy-hidden-track'
    assert flow['review'](submitted)['state'] == 'PENDING_COUNSELOR'
    with db.read() as conn:
        assert db.rows(conn, 'activity_eligibility_versions', activity_id=activity['id']) == versions


@pytest.mark.parametrize('decision', ['VALID', 'INVALID'])
def test_matching_broad_and_specific_recognition_can_share_the_same_decision(flow, decision):
    activity = flow['activity'](decision)
    flow['login']('counselor')
    response = flow['client'].put('/api/activities/' + activity['id'], json={
        'category_id': flow['cats']['RESEARCH'], 'decision': decision, 'edition_key': '2026'})
    assert response.status_code == 200
    with db.read() as conn:
        result = workflow.activity_eligibility(conn, activity['id'], flow['cats']['RESEARCH'], flow['year']['id'], {})
        assert result['decision'] == decision
        selected = db.one(conn, 'activity_eligibility_versions', result['version_id'])
        assert selected['edition_key'] is None


def test_qualification_update_does_not_rewrite_existing_frozen_snapshot(flow):
    activity = flow['activity']('VALID')
    app = flow['submit'](flow['draft']('1', 'RESEARCH', activity_id=activity['id']))
    with db.read() as conn:
        frozen_revision = db.one(conn, 'application_revisions', app['revision_id'])
        frozen_version = db.one(conn, 'submission_rounds', app['round_id'])['eligibility_version_id']
    flow['login']('counselor')
    assert flow['client'].put('/api/activities/' + activity['id'], json={
        'category_id': flow['cats']['RESEARCH'], 'decision': 'INVALID', 'group_key': 'legacy-group'}).status_code == 200
    assert flow['review'](app)['state'] == 'PENDING_COUNSELOR'
    with db.read() as conn:
        assert db.one(conn, 'application_revisions', app['revision_id']) == frozen_revision
        assert db.one(conn, 'submission_rounds', app['round_id'])['eligibility_version_id'] == frozen_version


@pytest.mark.parametrize('category', ['RESEARCH', 'ARTS', 'POST'])
def test_counselor_recognition_uses_visible_scope_and_is_reusable_by_later_students(flow, category):
    flow['client'].app.include_router(matching_router)
    hidden = {'edition_key': '2026', 'track_key': '历史软件赛道', 'group_key': '历史本科组'}
    app = flow['submit'](flow['draft']('1', category, title='新认定活动' + category, facts=hidden))
    pending = flow['review'](app)
    assert pending['state'] == 'PENDING_COUNSELOR'
    with db.read() as conn:
        frozen = db.one(conn, 'application_revisions', app['revision_id'])
    approved = flow['review'](pending, 'counselor')
    assert approved['state'] == 'APPROVED'
    activity_id = approved['activity']['id']
    with db.read() as conn:
        recognition = db.one(conn, 'activity_eligibility_versions', approved['round']['eligibility_version_id'])
        assert recognition['academic_year_id'] == flow['year']['id']
        assert all(recognition[key] is None for key in ('edition_key', 'track_key', 'group_key'))
        assert db.one(conn, 'application_revisions', app['revision_id']) == frozen
    flow['login']('student')
    search = flow['client'].get('/api/activities/search', params={
        'q': approved['title'], 'year_id': flow['year']['id'], 'category_id': flow['cats'][category]})
    assert search.status_code == 200
    found = next(item for item in search.json()['items'] if item['id'] == activity_id)
    assert found['decision'] == 'VALID'
    flow['login']('student2')
    client = flow['client']
    later = client.post('/api/applications', json={'year_id': flow['year']['id'], 'category_id': flow['cats'][category]})
    assert later.status_code == 200
    later_id = later.json()['id']
    with db.write() as conn:
        file = db.add(conn, 'file_objects', uploaded_by=flow['users']['student2']['id'], original_name='后续同学证明.png',
            mime_type='image/png', byte_size=1, sha256=db.uid().replace('-', '') * 2, storage_uri='fixture-student2.png')
    assert client.put('/api/applications/' + later_id, json={'title': approved['title'], 'requested_score': '1',
        'activity_id': activity_id, 'file_ids': [file['id']]}).status_code == 200
    later = client.post('/api/applications/' + later_id + '/submit', json={})
    assert later.status_code == 200
    assert flow['review'](later.json())['state'] == 'APPROVED'


def test_new_counselor_recognition_keeps_conflicting_historical_scope_unknown(flow):
    flow['client'].app.include_router(matching_router)
    activity = flow['activity']('INVALID', category='RESEARCH', track_key='保留旧限定赛道')
    with db.read() as conn:
        historical = db.rows(conn, 'activity_eligibility_versions', activity_id=activity['id'])
    app = flow['submit'](flow['draft']('1', 'RESEARCH', title=activity['canonical_name'],
        facts={'track_key': '另一旧赛道'}, activity_id=activity['id']))
    approved = flow['review'](flow['review'](app), 'counselor')
    assert approved['state'] == 'APPROVED'
    with db.read() as conn:
        recognition = db.one(conn, 'activity_eligibility_versions', approved['round']['eligibility_version_id'])
        assert recognition['track_key'] is None and recognition['decision'] == 'VALID'
        for version in historical:
            assert db.one(conn, 'activity_eligibility_versions', version['id']) == version
    flow['login']('student')
    search = flow['client'].get('/api/activities/search', params={
        'q': activity['canonical_name'], 'year_id': flow['year']['id'], 'category_id': flow['cats']['RESEARCH']})
    found = next(item for item in search.json()['items'] if item['id'] == activity['id'])
    assert found['decision'] == 'UNKNOWN' and found['eligibility']['version_id'] is None
    later = flow['submit'](flow['draft']('1', 'RESEARCH', title=activity['canonical_name'], activity_id=activity['id']))
    assert flow['review'](later)['state'] == 'PENDING_COUNSELOR'
