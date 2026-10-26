"""Real material analysis and application use ranked recognition metadata, no cloud."""
import pytest

from app import db, materials
from test_activity_matching import _activity
from test_all_category_scoring_contract import NORMAL, analyze_and_apply, prepare
from test_materials import material_app


def _model(monkeypatch, prepared, activity_id=None, *, title=None):
    calls = prepared[-1]

    def answer(prompt, unused):
        calls.append(prompt)
        selected = activity_id(prompt) if callable(activity_id) else activity_id
        return {'title': title or prompt['untrusted_materials'][0]['text'],
                'requested_score': '99', 'activity_id': selected, 'payload_json': {}}

    monkeypatch.setattr(materials, 'deepseek_suggestion', answer)


def _persisted_activity(prepared):
    with db.read() as conn:
        application = db.one(conn, 'applications', prepared[1]['id'])
        return db.one(conn, 'application_revisions', application['working_revision_id'])['activity_id']


def test_full_analysis_finds_an_exact_alias_after_three_hundred_library_entries(material_app, monkeypatch):
    title = '青禾智能视觉挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾智能视觉挑战省级二等奖。'])
    context = prepared[2]
    with db.write() as conn:
        for index in range(305):
            _activity(conn, f'虚构无关科技项目{index}', context['category']['id'], context['year']['id'])
        target = _activity(conn, '青禾机器人视觉创新竞赛', context['category']['id'], context['year']['id'],
                           aliases=[title])
    _model(monkeypatch, prepared, title=title)
    result, applied = analyze_and_apply(prepared)
    candidates = prepared[-1][0]['activity_catalog']
    assert candidates[0]['id'] == target['id']
    assert candidates[0]['match_kind'] == 'EXACT_ALIAS'
    assert result['activity_match']['source'] == 'TEXT_EXACT'
    assert result['suggestion']['activity_id'] == applied['revision']['activity_id'] == _persisted_activity(prepared) == target['id']


@pytest.mark.parametrize('model_selects_nearby', [False, True], ids=['no-model-match', 'wrong-valid-model-match'])
def test_exact_invalid_activity_outranks_a_valid_nearby_activity(material_app, monkeypatch, model_selects_nearby):
    title = '星荧学院体验夜跑'
    prepared = prepare(material_app, monkeypatch, 'ARTS', title,
                       ['{name} {number}参加星荧学院体验夜跑，获校级三等奖。'])
    context = prepared[2]
    with db.write() as conn:
        valid = _activity(conn, '星荧学院体验欢乐夜跑', context['category']['id'], context['year']['id'])
        invalid = _activity(conn, title, context['category']['id'], context['year']['id'], 'INVALID')
    _model(monkeypatch, prepared, valid['id'] if model_selects_nearby else None, title=title)
    result, applied = analyze_and_apply(prepared)
    candidates = prepared[-1][0]['activity_catalog']
    assert {item['id'] for item in candidates} == {valid['id'], invalid['id']}
    assert candidates[0]['id'] == invalid['id'] and candidates[0]['decision'] == 'INVALID'
    assert candidates[0]['match_score'] > candidates[1]['match_score']
    assert result['activity_match']['source'] == 'TEXT_EXACT'
    assert applied['revision']['activity_id'] == _persisted_activity(prepared) == invalid['id']


def test_invalid_model_id_is_rejected_but_unique_exact_text_can_match(material_app, monkeypatch):
    title = '青禾机器人创新挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾机器人创新挑战省级二等奖。'])
    context = prepared[2]
    with db.write() as conn:
        target = _activity(conn, title, context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, 'invented-library-id', title=title)
    result, applied = analyze_and_apply(prepared)
    assert result['activity_match']['source'] == 'TEXT_EXACT'
    assert applied['revision']['activity_id'] == _persisted_activity(prepared) == target['id']


@pytest.mark.parametrize('model_id', ['invented-library-id', ['invented-library-id'], {'id': 'invented-library-id'}],
                         ids=['string', 'array', 'object'])
def test_model_ids_outside_candidates_do_not_persist_or_break_analysis(material_app, monkeypatch, model_id):
    title = '青禾机器人创新挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾机器人创新挑战省级二等奖。'])
    context = prepared[2]
    with db.write() as conn:
        _activity(conn, '青禾机器人设计挑战', context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, model_id, title=title)
    result, applied = analyze_and_apply(prepared)
    assert prepared[-1][0]['activity_catalog']
    assert not any(item['exact_match'] for item in prepared[-1][0]['activity_catalog'])
    assert result['activity_match']['source'] is None
    assert result['suggestion']['activity_id'] is applied['revision']['activity_id'] is _persisted_activity(prepared) is None


def test_an_unrelated_library_never_forces_a_match(material_app, monkeypatch):
    title = '秋日摄影艺术竞赛'
    prepared = prepare(material_app, monkeypatch, 'ARTS', title,
                       ['{name} {number}荣获秋日摄影艺术竞赛校级二等奖。'])
    context = prepared[2]
    with db.write() as conn:
        target = _activity(conn, '青禾机器人智能开发', context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, target['id'], title=title)
    result, applied = analyze_and_apply(prepared)
    assert prepared[-1][0]['activity_catalog'] == []
    assert result['activity_match']['candidate_count'] == 0
    assert applied['revision']['activity_id'] is _persisted_activity(prepared) is None


def test_two_explicit_library_names_require_the_model_to_resolve_which_event(material_app, monkeypatch):
    title = '青禾机器人创新挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾机器人创新挑战省级二等奖。',
                        '本证明另附青禾机器视觉设计赛说明，请核对所报活动。'])
    context = prepared[2]
    with db.write() as conn:
        _activity(conn, title, context['category']['id'], context['year']['id'])
        _activity(conn, '青禾机器视觉设计赛', context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, title=title)
    result, applied = analyze_and_apply(prepared)
    assert len([item for item in prepared[-1][0]['activity_catalog'] if item['exact_match']]) == 2
    assert result['activity_match']['source'] is None
    assert applied['revision']['activity_id'] is _persisted_activity(prepared) is None


@pytest.mark.parametrize('category,title,body,unused', NORMAL, ids=[item[0] for item in NORMAL])
def test_matching_is_only_enabled_for_research_arts_and_post(material_app, monkeypatch, category, title, body, unused):
    prepared = prepare(material_app, monkeypatch, category, title, [body])
    context = prepared[2]
    with db.write() as conn:
        target = _activity(conn, title, context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, target['id'], title=title)
    result, applied = analyze_and_apply(prepared)
    if category in {'RESEARCH', 'ARTS', 'POST'}:
        assert prepared[-1][0]['activity_catalog'][0]['id'] == target['id']
        assert applied['revision']['activity_id'] == _persisted_activity(prepared) == target['id']
        assert result['activity_match']['source'] == 'MODEL'
    else:
        assert prepared[-1][0]['activity_catalog'] == []
        assert applied['revision']['activity_id'] is _persisted_activity(prepared) is None


@pytest.mark.parametrize('category,title,body,unused',
                         [item for item in NORMAL if item[0] not in {'RESEARCH', 'ARTS', 'POST'}],
                         ids=['COLLECTIVE', 'IDEOLOGY', 'SERVICE', 'REWARD'])
def test_analysis_does_not_clear_hidden_legacy_matches_for_other_categories(material_app, monkeypatch, category, title, body, unused):
    prepared = prepare(material_app, monkeypatch, category, title, [body])
    context, ocr = prepared[2:4]
    with db.write() as conn:
        previous = _activity(conn, title, context['category']['id'], context['year']['id'])
        revision = db.change(conn, 'application_revisions', ocr['input_revision_id'], activity_id=previous['id'])
        ocr['input_fingerprint'] = materials.revision_fingerprint(conn, revision)
    _model(monkeypatch, prepared, title=title)
    result, applied = analyze_and_apply(prepared)
    assert prepared[-1][0]['activity_catalog'] == []
    assert result['activity_match']['source'] is None
    assert applied['revision']['activity_id'] == _persisted_activity(prepared) == previous['id']


def test_inactive_previously_selected_activity_is_never_automatically_rematched(material_app, monkeypatch):
    title = '青禾机器人创新挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾机器人创新挑战省级二等奖。'])
    client, claim, context, ocr, _ = prepared
    with db.write() as conn:
        target = _activity(conn, title, context['category']['id'], context['year']['id'])
        revision = db.change(conn, 'application_revisions', ocr['input_revision_id'], activity_id=target['id'])
        db.change(conn, 'activities', target['id'], status='INACTIVE')
        ocr['input_fingerprint'] = materials.revision_fingerprint(conn, revision)
    old = client.get('/api/activity-matches/' + target['id'],
                     params={'category_id': context['category']['id'], 'year_id': context['year']['id']}).json()
    assert old['id'] == target['id'] and old['status_label'] == '已停用'
    _model(monkeypatch, prepared, target['id'], title=title)
    result, applied = analyze_and_apply(prepared)
    assert prepared[-1][0]['activity_catalog'] == []
    assert result['activity_match']['source'] is None
    assert applied['revision']['activity_id'] is _persisted_activity(prepared) is None


def test_activity_disabled_after_analysis_cannot_be_applied_as_a_new_match(material_app, monkeypatch):
    title = '青禾机器人创新挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾机器人创新挑战省级二等奖。'])
    client, claim, context, ocr, _ = prepared
    with db.write() as conn:
        target = _activity(conn, title, context['category']['id'], context['year']['id'])
    _model(monkeypatch, prepared, target['id'], title=title)
    result = materials.run_ai(claim['id'], context['user'], ocr_result=ocr)
    assert result['status'] == 'SUCCEEDED' and result['suggestion']['activity_id'] == target['id']
    with db.write() as conn:
        db.change(conn, 'activities', target['id'], status='INACTIVE')
    response = client.post(f"/api/applications/{claim['id']}/apply-analysis", json={'analysis_id': result['analysis_id']})
    assert response.status_code == 200, response.text
    assert response.json()['revision']['activity_id'] is _persisted_activity(prepared) is None


def test_an_alias_only_proof_supports_its_matching_catalog_title(material_app, monkeypatch):
    title = '青禾智能视觉挑战'
    prepared = prepare(material_app, monkeypatch, 'RESEARCH', title,
                       ['{name} {number}荣获青禾智能视觉挑战省级二等奖。'])
    context = prepared[2]
    with db.write() as conn:
        target = _activity(conn, '青禾机器人视觉创新竞赛', context['category']['id'], context['year']['id'], aliases=[title])
    _model(monkeypatch, prepared, target['id'], title=target['canonical_name'])
    result, applied = analyze_and_apply(prepared)
    assert result['suggestion']['title'] in {title, target['canonical_name']}
    assert applied['revision']['activity_id'] == _persisted_activity(prepared) == target['id']
