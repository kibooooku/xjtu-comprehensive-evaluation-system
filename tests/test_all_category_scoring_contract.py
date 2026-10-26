"""Located text -> real person linkage -> analysis -> apply/detail/list, no cloud."""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import db, materials
from test_materials import material_app, png
from test_outcome_evidence import _line


CONFIG = {'enabled': True, 'secret_id': 'synthetic', 'secret_key': 'synthetic',
          'deepseek_key': 'synthetic', 'deepseek_model': 'no-network',
          'general_limit': 1000, 'seal_limit': 1000, 'max_analysis_pages': 12}
NORMAL = [
    ('COLLECTIVE', '春日登山集体活动', '{name} {number}参加本次春日登山集体活动1次。', '0.30'),
    ('IDEOLOGY', '青年理论学习专题培训', '{name} {number}参加校级党团组织理论学习培训1次，已完成。', '1.00'),
    ('SERVICE', '社区周末志愿服务', '{name} {number}参与社区志愿服务，认证工时16小时。', '0.50'),
    ('RESEARCH', '工程创新竞赛省级二等奖', '{name} {number}荣获工程创新竞赛省级二等奖。', '6.00'),
    ('ARTS', '校园摄影文艺竞赛', '{name} {number}荣获校级摄影比赛二等奖。', '1.50'),
    ('POST', '班级学生干部年度考评', '{name} {number}任职班长，任期12个月，考评结果良好。', '1.50'),
    ('REWARD', '校级精神文明先进个人表彰', '{name} {number}获学校表彰，授予校级精神文明先进个人荣誉称号。', '2.00'),
]


def prepare(material_app, monkeypatch, category_code, title, lines, *, score='99', manual=None):
    _, login = material_app
    client = login('student')
    category = next(item for item in client.get('/api/bootstrap').json()['categories'] if item['code'] == category_code)
    response = client.post('/api/applications', json={'category_id': category['id']})
    assert response.status_code == 200, response.text
    claim = response.json()
    attached = client.post(f"/api/applications/{claim['id']}/files", files={'file': ('neutral-fixture.png', png(1000, 1600), 'image/png')})
    assert attached.status_code == 200
    if manual is not None:
        assert client.put(f"/api/applications/{claim['id']}", json={'requested_score': manual}).status_code == 200
    with db.write() as conn:
        context = materials._app_context(conn, claim['id'])
        revision = db.one(conn, 'application_revisions', context['application']['working_revision_id'])
        page = db.one(conn, 'file_pages', attached.json()['pages'][0]['id'])
        profile = context['student']
        values = {'name': context['user']['display_name'], 'number': profile['student_number']}
        _line(conn, page, title, 80, 100, height=28)
        for index, item in enumerate(lines):
            text, x, y = item if isinstance(item, tuple) else (item, 80, 350 + 60 * index)
            _line(conn, page, text.format(**values), x, y, height=24)
        run = db.rows(conn, 'recognition_runs', page_id=page['id'])[0]
        ocr = {'status': 'SUCCEEDED', 'policy_version': materials.OCR_POLICY_VERSION,
               'application_id': claim['id'], 'input_revision_id': revision['id'],
               'input_fingerprint': materials.revision_fingerprint(conn, revision),
               'ocr_fingerprint': materials.ocr_input_fingerprint(conn, revision),
               'page_ids': [page['id']], 'run_ids': [run['id']], 'warnings': [], 'suggested_crops': []}
    monkeypatch.setattr(materials, 'configuration', lambda: CONFIG)
    monkeypatch.setattr(materials, 'tencent_ocr', lambda *args, **kwargs: pytest.fail('No OCR provider call permitted'))
    calls = []

    def model(prompt, unused):
        calls.append(prompt)
        return {'title': title, 'requested_score': score, 'payload_json': {}}

    monkeypatch.setattr(materials, 'deepseek_suggestion', model)
    return client, claim, context, ocr, calls


def analyze_and_apply(prepared):
    client, claim, context, ocr, calls = prepared
    result = materials.run_ai(claim['id'], context['user'], ocr_result=ocr)
    assert result['status'] == 'SUCCEEDED', result
    assert len(calls) == 1
    response = client.post(f"/api/applications/{claim['id']}/apply-analysis", json={'analysis_id': result['analysis_id']})
    assert response.status_code == 200, response.text
    return result, response.json()


def assert_views(client, claim, *, status, value, retained):
    detail = client.get(f"/api/applications/{claim['id']}").json()
    groups = client.get('/api/submissions', params={'scope': 'mine'}).json()
    listed = next(item for group in groups for item in group['items'] if item['id'] == claim['id'])
    for view in (detail, listed):
        assert view['score_resolution']['status'] == status
        assert view['score_resolution']['value'] == value
        assert view['requested_score'] == retained
    return detail


@pytest.mark.parametrize('category,title,body,expected', NORMAL, ids=[item[0] for item in NORMAL])
def test_each_category_uses_bound_material_facts_and_policy_instead_of_model_99(material_app, monkeypatch, category, title, body, expected):
    prepared = prepare(material_app, monkeypatch, category, title, [body])
    client, claim, _, _, _ = prepared
    result, applied = analyze_and_apply(prepared)
    assert Decimal(result['suggestion']['requested_score']) == Decimal(expected)
    assert result['score_policy']['status'] == 'RULE_APPLIED'
    assert result['score_resolution']['status'] == applied['score_resolution']['status'] == 'RULE_APPLIED'
    assert result['score_resolution']['value'] == applied['score_resolution']['value'] == expected
    assert result['suggestion']['score_requires_confirmation'] is False
    assert_views(client, claim, status='RULE_APPLIED', value=expected, retained=expected)


@pytest.mark.parametrize('category,title,unused,expected', NORMAL, ids=[item[0] for item in NORMAL])
def test_each_category_without_personal_participation_keeps_pending_not_calculated_zero(material_app, monkeypatch, category, title, unused, expected):
    prepared = prepare(material_app, monkeypatch, category, title, ['未列出任何参与人员，尚需补充该项活动证明。'])
    client, claim, _, _, _ = prepared
    result, applied = analyze_and_apply(prepared)
    assert result['score_policy']['status'] == 'NEEDS_REVIEW'
    assert result['suggestion']['score_requires_confirmation'] is True
    for value in (result['score_resolution'], applied['score_resolution']):
        assert value['status'] == 'NEEDS_REVIEW' and value['value'] is None
        assert value['retained_value'] == '0.00' and value['missing_facts']
    assert_views(client, claim, status='NEEDS_REVIEW', value=None, retained='0.00')
    submit = client.post(f"/api/applications/{claim['id']}/submit", json={})
    assert submit.status_code == 200
    assert submit.json()['score_resolution']['status'] == 'MANUAL'
    assert submit.json()['score_resolution']['value'] == '0.00'


@pytest.mark.parametrize('category,title,body,expected', [
    ('COLLECTIVE', '夏日广场舞集体活动', '{name} {number}参加广场舞集体活动两次。', '0.60'),
    ('IDEOLOGY', '省级青年理论学习培训', '{name} {number}完成省级党团理论学习培训1次，获评优秀学员。', '3.00'),
    ('SERVICE', '社区志愿服务时长证明', '{name} {number}志愿服务累计32小时。', '1.00'),
    ('POST', '年度班级干部考评', '{name} {number}任职班长，任期12个月，考评结果不合格。', '0.00'),
    ('RESEARCH', '学术专著参编证明', '{name} {number}参编学术专著，第一署名单位西安交通大学。', '1.00'),
])
def test_other_supported_facts_and_real_zero_reach_all_public_views(material_app, monkeypatch, category, title, body, expected):
    prepared = prepare(material_app, monkeypatch, category, title, [body])
    client, claim, _, _, _ = prepared
    result, applied = analyze_and_apply(prepared)
    assert result['score_resolution']['status'] == applied['score_resolution']['status'] == 'RULE_APPLIED'
    assert result['score_resolution']['value'] == expected
    assert_views(client, claim, status='RULE_APPLIED', value=expected, retained=expected)


@pytest.mark.parametrize('category,title,lines,expected', [
    ('POST', '班级学生干部年度考评名单', ['其他同学 202499991任职班长，任期12个月，考评结果优秀。', '{name} {number}任职班长，任期12个月，考评结果良好。'], '1.50'),
    ('SERVICE', '校级社会实践团队成员考核结果名单', ['其他同学 202499991考核结果优秀。', '{name} {number}考核结果合格。'], '1.00'),
])
def test_roster_personal_fields_do_not_use_another_students_result(material_app, monkeypatch, category, title, lines, expected):
    prepared = prepare(material_app, monkeypatch, category, title, lines)
    client, claim, _, _, calls = prepared
    result, _ = analyze_and_apply(prepared)
    assert calls[0]['linked_outcome']['is_roster'] is True
    assert result['score_resolution']['status'] == 'RULE_APPLIED' and result['score_resolution']['value'] == expected
    assert_views(client, claim, status='RULE_APPLIED', value=expected, retained=expected)


def test_new_analysis_does_not_change_students_existing_manual_score(material_app, monkeypatch):
    prepared = prepare(material_app, monkeypatch, 'COLLECTIVE', '春日登山集体活动', ['{name} {number}参加集体活动1次。'], manual='1.37')
    client, claim, context, ocr, _ = prepared
    result = materials.run_ai(claim['id'], context['user'], ocr_result=ocr)
    assert result['score_resolution']['value'] == '0.30'
    assert_views(client, claim, status='MANUAL', value='1.37', retained='1.37')


def test_frozen_revision_is_rejected_before_any_new_model_analysis(material_app, monkeypatch):
    prepared = prepare(material_app, monkeypatch, 'COLLECTIVE', '春日登山集体活动', ['{name} {number}参加集体活动1次。'])
    _, claim, context, ocr, calls = prepared
    with db.write() as conn:
        db.change(conn, 'application_revisions', ocr['input_revision_id'], content_frozen_at=db.now())
    with pytest.raises(HTTPException) as caught:
        materials.run_ai(claim['id'], context['user'], ocr_result=ocr)
    assert caught.value.status_code == 409 and not calls


@pytest.mark.parametrize('category,title,body', [
    ('COLLECTIVE', '春日登山集体活动', '{name} {number}未参加本次春日登山集体活动。'),
    ('IDEOLOGY', '青年理论学习专题培训', '{name} {number}未参加本次党团理论学习培训。'),
    ('COLLECTIVE', '春日登山集体活动', '{name} {number}参加集体活动-2次。'),
    ('COLLECTIVE', '春日登山集体活动', '{name} {number}参加集体活动1.5次。'),
    ('RESEARCH', '工程创新竞赛省级二等奖', '{name} {number}未获得工程创新竞赛省级二等奖。'),
])
def test_negated_or_invalid_personal_facts_do_not_generate_positive_points(material_app, monkeypatch, category, title, body):
    prepared = prepare(material_app, monkeypatch, category, title, [body])
    client, claim, _, _, _ = prepared
    result, _ = analyze_and_apply(prepared)
    assert result['score_resolution']['status'] == 'NEEDS_REVIEW'
    assert result['score_resolution']['value'] is None
    assert_views(client, claim, status='NEEDS_REVIEW', value=None, retained='0.00')


def test_not_selected_excellent_trainee_does_not_remove_completed_training_or_add_bonus(material_app, monkeypatch):
    prepared = prepare(material_app, monkeypatch, 'IDEOLOGY', '青年理论学习专题培训', ['{name} {number}完成校级党团理论学习培训1次，未获优秀学员称号。'])
    client, claim, _, _, _ = prepared
    result, _ = analyze_and_apply(prepared)
    assert result['score_resolution']['status'] == 'RULE_APPLIED'
    assert result['suggestion']['payload_json']['excellent_trainee'] == 'NO'
    assert_views(client, claim, status='RULE_APPLIED', value='1.00', retained='1.00')
