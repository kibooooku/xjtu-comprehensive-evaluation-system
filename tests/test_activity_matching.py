"""Whole-library matching uses public metadata, local text and normal RBAC."""
import json

import pytest

from app import db
from app.activity_matching import retrieve_activity_candidates
from app.workflow import _norm, activity_eligibility
from test_materials import material_app


def _catalog(client):
    boot = client.get('/api/bootstrap').json()
    return {item['code']: item['id'] for item in boot['categories']}, boot['active_year']['id'], [year['id'] for year in boot['years']]


def _activity(conn, name, category, year, decision='VALID', aliases=(), **scope):
    user = db.one(conn, 'users', username='counselor')
    activity = db.add(conn, 'activities', canonical_name=name, normalized_name=_norm(name), maintained_by=user['id'])
    for alias in aliases:
        db.add(conn, 'activity_aliases', activity_id=activity['id'], alias=alias, normalized_alias=_norm(alias), created_by=user['id'])
    if decision:
        fields = {'academic_year_id': year, 'edition_key': None, 'track_key': None, 'group_key': None, **scope}
        db.add(conn, 'activity_eligibility_versions', activity_id=activity['id'], category_id=category,
               scope_key=json.dumps(fields, sort_keys=True), decision=decision, decided_by=user['id'],
               reason='public-recognition-only', **fields)
    return activity


def test_students_search_valid_and_invalid_aliases_without_private_materials(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        good = _activity(conn, '星荧夜跑校级文体竞赛', categories['ARTS'], year, aliases=['星荧跑步'])
        bad = _activity(conn, '星荧夜跑学院体验赛', categories['ARTS'], year, 'INVALID', aliases=['学院星荧跑步'])
    result = student.get('/api/activities/search', params={'q': '星 荧 跑 步', 'category_id': categories['ARTS'], 'year_id': year})
    assert result.status_code == 200
    items = result.json()['items']
    assert {item['id'] for item in items} == {good['id'], bad['id']}
    assert {item['decision'] for item in items} == {'VALID', 'INVALID'}
    assert items[0]['match_kind'] == 'EXACT_ALIAS' and items[0]['exact_match']
    assert all('crops' not in item and 'files' not in item and 'maintained_by' not in item for item in items)
    assert all(0 <= item['match_score'] <= 1 for item in items)


def test_matching_ranks_past_the_first_three_hundred_entries(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        for number in range(305):
            _activity(conn, '虚构无关科技项目' + str(number), categories['RESEARCH'], year)
        expected = _activity(conn, '青禾机器人视觉创新竞赛', categories['RESEARCH'], year, aliases=['青禾智能视觉挑战'])
        candidates = retrieve_activity_candidates(conn, [{'text': '本人荣获青禾智能视觉挑战省级二等奖。'}], categories['RESEARCH'], year, limit=3)
        assert candidates[0]['id'] == expected['id']
        assert candidates[0]['match_kind'] == 'EXACT_ALIAS'
        assert len(candidates) <= 3
    result = student.get('/api/activities/search', params={'q': '机器人视觉', 'category_id': categories['RESEARCH'], 'year_id': year})
    assert result.status_code == 200 and result.json()['items'][0]['id'] == expected['id']


def test_empty_query_pages_and_scopes_are_stable(material_app):
    _, login = material_app
    student = login('student')
    categories, year, years = _catalog(student)
    other_year = next(value for value in years if value != year)
    with db.write() as conn:
        valid = _activity(conn, '青禾工程设计大赛', categories['RESEARCH'], year)
        invalid = _activity(conn, '青禾数学模型竞赛', categories['RESEARCH'], year, 'INVALID')
        _activity(conn, '旧年历史活动', categories['RESEARCH'], other_year)
        _activity(conn, '班级任职评定', categories['POST'], year)
        _activity(conn, '尚未认定活动', categories['RESEARCH'], year, decision=None)
    params = {'category_id': categories['RESEARCH'], 'year_id': year, 'limit': 1}
    first = student.get('/api/activities/search', params=params).json()
    second = student.get('/api/activities/search', params={**params, 'offset': 1}).json()
    assert first['total'] == second['total'] == 2
    assert first['items'][0]['id'] == valid['id'] and first['items'][0]['match_score'] == 0
    assert second['items'][0]['id'] == invalid['id']


def test_selected_old_cross_category_and_inactive_activity_keeps_identity(material_app):
    _, login = material_app
    student = login('student')
    categories, year, years = _catalog(student)
    older = next(value for value in years if value != year)
    with db.write() as conn:
        previous = _activity(conn, '历史班委任职评定', categories['POST'], older)
    path = '/api/activity-matches/' + previous['id']
    old = student.get(path, params={'category_id': categories['POST'], 'year_id': older}).json()
    mismatch = student.get(path, params={'category_id': categories['RESEARCH'], 'year_id': year}).json()
    assert old['id'] == mismatch['id'] == previous['id']
    assert old['decision'] == 'VALID' and mismatch['decision'] == 'UNKNOWN'
    with db.write() as conn:
        db.change(conn, 'activities', previous['id'], status='INACTIVE')
    inactive = student.get(path, params={'category_id': categories['POST'], 'year_id': older}).json()
    assert inactive['id'] == previous['id'] and inactive['status_label'] == '已停用'


def test_specific_recognition_is_not_claimed_applicable_without_hidden_scope(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        activity = _activity(conn, '春季程序设计竞赛', categories['RESEARCH'], year, track_key='软件赛')
        expected = activity_eligibility(conn, activity['id'], categories['RESEARCH'], year, {})
    found = student.get('/api/activities/search', params={'q': '春季程序设计', 'category_id': categories['RESEARCH'], 'year_id': year}).json()['items'][0]
    assert found['decision'] == expected['decision'] == 'UNKNOWN'
    assert found['status_label'] == '需核对适用范围'


def test_weak_or_unrelated_material_does_not_force_a_recognition(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        _activity(conn, '青禾机器人竞赛', categories['RESEARCH'], year)
        _activity(conn, '夜跑', categories['RESEARCH'], year, 'INVALID', aliases=['活动'])
        assert retrieve_activity_candidates(conn, [{'text': '参加绘画艺术节。'}], categories['RESEARCH'], year) == []
        generic = retrieve_activity_candidates(conn, [{'text': '同学参加活动，夜跑后准备返校。'}], categories['RESEARCH'], year)
        assert not any(item['exact_match'] for item in generic)


def test_post_categories_are_supported_and_current_match_can_stay_visible(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        activity = _activity(conn, '班级干部年度考评', categories['POST'], year)
        found = retrieve_activity_candidates(conn, [{'text': '本人参加班级干部年度考评，良好。'}], categories['POST'], year)
        assert found[0]['id'] == activity['id'] and found[0]['match_kind'] == 'EXACT_NAME'
        selected = retrieve_activity_candidates(conn, [{'text': '另一份未知文字'}], categories['POST'], year, limit=1, current_activity_id=activity['id'])
        assert selected[0]['id'] == activity['id']


def test_matching_routes_follow_activity_rbac_and_validate_query(material_app):
    _, login = material_app
    student = login('student')
    categories, year, _ = _catalog(student)
    with db.write() as conn:
        activity = _activity(conn, '校园合唱艺术比赛', categories['ARTS'], year)
    assert login('admin').get('/api/activities/search').status_code == 403
    assert student.get('/api/activities/search', params={'q': 'x'*251}).status_code == 422
    assert student.get('/api/activities/search', params={'category_id': 'missing'}).status_code == 404
    assert student.get('/api/activity-matches/' + activity['id'], params={'year_id': 'missing'}).status_code == 404
