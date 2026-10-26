"""The simple result field preserves both user wording and legacy AI facts."""
import pytest

from app import db
from app.result_labels import result_label
from app.score_resolution import from_policy
from test_materials import material_app, create_claim


@pytest.mark.parametrize('payload,expected', [
    ({'award_level': 'PROVINCIAL', 'award_grade': 'SECOND'}, '省级二等奖'),
    ({'award_level': 'SCHOOL', 'award_grade': 'THIRD'}, '校级三等奖'),
    ({'award_grade': 'PLACE_4'}, '第4名'),
    ({'assessment_result': 'GOOD'}, '良好'),
    ({'award_level': '全国二等奖', 'award_grade': 'SECOND'}, '全国二等奖'),
    ({'result_label': '省级优秀作品', 'award_grade': 'THIRD'}, '省级优秀作品'),
    ({'result_label': '', 'award_grade': 'THIRD'}, ''),
])
def test_single_label_handles_legacy_and_manual_values(payload, expected):
    assert result_label(payload) == expected


def test_cached_old_score_message_does_not_reintroduce_removed_reason_copy():
    resolution = from_policy({'status': 'NEEDS_REVIEW', 'message': '分值待核定；也可手动填写并确认，无需理由。'}, '0')
    assert '无需' not in resolution['message']


def test_simple_label_save_retains_hidden_ai_facts_and_free_text(material_app):
    _, login = material_app
    client = login('student')
    claim, _, _ = create_claim(client)
    with db.write() as conn:
        app = db.one(conn, 'applications', claim['id'])
        db.change(conn, 'application_revisions', app['working_revision_id'], payload_json={
            'award_level': 'PROVINCIAL', 'award_grade': 'SECOND', 'record_level': 'SCHOOL'})
    saved = client.put('/api/applications/' + claim['id'], json={
        'requested_score': '2.75', 'score_confirmed': True,
        'payload_json': {'result_label': '学生核对后的优秀等级', 'note': '补充说明'}})
    assert saved.status_code == 200
    assert saved.json()['result_label'] == '学生核对后的优秀等级'
    facts = saved.json()['revision']['payload_json']
    assert facts['award_level'] == 'PROVINCIAL' and facts['award_grade'] == 'SECOND' and facts['record_level'] == 'SCHOOL'
    assert saved.json()['score_resolution']['value'] == '2.75'
    cleared = client.put('/api/applications/' + claim['id'], json={'payload_json': {'result_label': ''}})
    assert cleared.status_code == 200 and cleared.json()['result_label'] == ''
