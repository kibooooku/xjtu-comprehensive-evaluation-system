"""Literal policy examples and evidence-association boundaries, without cloud/DB."""
import pytest

from app import db, materials, outcome_evidence
from app.policy_suggestions import normalize_policy_suggestion
from test_materials import material_app, upload, _text_run


def suggest(category, text, *, grade="", assessment="", level="", matched=True, roster=False, extra=None, current="0.37", name="示例同学"):
    facts = {**({"award_grade": grade} if grade else {}), **({"assessment_result": assessment} if assessment else {}), **({"award_level": level} if level else {}), **(extra or {})}
    outcome = {"status": "MATCHED" if matched else "NOT_FOUND", "is_roster": roster, "facts": facts, "identity_count": 1}
    return normalize_policy_suggestion(category, [{"text": text}], {"requested_score": current}, {"title": "具体活动", "requested_score": "10.00", "payload_json": {"award_level": "全国二等奖", "award_grade": "FIRST", "assessment_result": "EXCELLENT"}}, outcome, student_name=name)


@pytest.mark.parametrize("level,grades,expected", [
    ("NATIONAL", ["FIRST", "SECOND", "THIRD"], ["10.00", "9.00", "8.00"]),
    ("PROVINCIAL", ["FIRST", "SECOND", "THIRD"], ["8.00", "6.00", "4.00"]),
    ("SCHOOL", ["FIRST", "SECOND", "THIRD", "EXCELLENT"], ["4.00", "3.00", "2.00", "1.00"]),
])
def test_research_competition_matrix_pdf_page_6(level, grades, expected):
    for grade, score in zip(grades, expected):
        result, metadata, warnings = suggest("RESEARCH", "学科竞赛获奖证明", grade=grade, level=level)
        assert result["requested_score"] == score and metadata["status"] == "RULE_APPLIED" and not warnings
        assert result["payload_json"]["award_level"] == level and result["payload_json"]["award_grade"] == grade


def test_lanqiao_national_second_is_nine_and_scope_is_not_grade():
    result, metadata, _ = suggest("RESEARCH", "第十七届蓝桥杯全国软件和信息技术专业人才大赛 软件赛全国总决赛二等奖", grade="SECOND")
    assert result["requested_score"] == "9.00"
    assert result["payload_json"]["award_level"] == "NATIONAL"
    assert result["payload_json"]["award_grade"] == "SECOND"
    assert metadata["rule"] == "PDF6_RESEARCH_COMPETITION"


def test_provincial_round_of_national_brand_is_not_national_award():
    result, _, _ = suggest("RESEARCH", "蓝桥杯全国软件和信息技术专业人才大赛省赛三等奖", grade="THIRD")
    assert result["requested_score"] == "4.00" and result["payload_json"]["award_level"] == "PROVINCIAL"


def test_special_research_prize_follows_first_but_unlisted_national_excellent_is_manual():
    assert suggest("RESEARCH", "学科竞赛", grade="SPECIAL", level="PROVINCIAL")[0]["requested_score"] == "8.00"
    result, metadata, _ = suggest("RESEARCH", "学科竞赛", grade="EXCELLENT", level="NATIONAL")
    assert result["requested_score"] == "0.37" and metadata["requires_confirmation"]


@pytest.mark.parametrize("level,grades,expected", [
    ("NATIONAL", ["FIRST", "SECOND", "THIRD", "EXCELLENT"], ["6.00", "5.00", "4.00", "3.00"]),
    ("PROVINCIAL", ["FIRST", "SECOND", "THIRD", "EXCELLENT"], ["4.00", "3.50", "3.00", "2.00"]),
    ("SCHOOL", ["FIRST", "SECOND", "THIRD"], ["2.00", "1.50", "1.00"]),
])
def test_art_competition_matrix_pdf_page_7(level, grades, expected):
    for grade, score in zip(grades, expected):
        result, _, _ = suggest("ARTS", "文艺比赛摄影项目", grade=grade, level=level)
        assert result["requested_score"] == score and result["payload_json"]["competition_kind"] == "ART"


@pytest.mark.parametrize("level,expected", [("NATIONAL", ["6.00", "5.00", "4.00", "3.00", "3.00"]), ("PROVINCIAL", ["4.00", "3.50", "3.00", "2.00", "2.00"]), ("SCHOOL", ["2.00", "1.50", "1.00", "0.50", "0.50"])])
def test_sports_places_use_own_place_not_other_rows(level, expected):
    for place, score in zip([1, 2, 3, 4, 8], expected):
        result, _, _ = suggest("ARTS", "体育田径比赛名单，其他同学第一名，本人名次由关联行确定", grade=f"PLACE_{place}", level=level, roster=True)
        assert result["requested_score"] == score


def test_night_run_third_prize_is_scored_as_prize_band_without_inventing_third_place():
    result, meta, _ = suggest("ARTS", "星荧夜跑竞赛三等奖", grade="THIRD", level="SCHOOL")
    assert result["payload_json"]["competition_kind"] == "SPORT" and result["payload_json"]["award_grade"] == "THIRD"
    assert result['payload_json']['award_system'] == 'PRIZE'
    assert result["requested_score"] == "1.00" and not meta["requires_confirmation"]
    assert meta['rule'] == 'PDF7_8_PRIZE_BAND'


@pytest.mark.parametrize('level,scores', [('SCHOOL', ['2.00', '1.50', '1.00']), ('PROVINCIAL', ['4.00', '3.50', '3.00']), ('NATIONAL', ['6.00', '5.00', '4.00'])])
def test_bound_sports_prizes_use_award_bands_across_levels(level, scores):
    for grade, expected in zip(['FIRST', 'SECOND', 'THIRD'], scores):
        result, meta, _ = suggest('ARTS', '体育跑步比赛获奖证明', grade=grade, level=level)
        assert result['requested_score'] == expected and meta['status'] == 'RULE_APPLIED'
        assert result['payload_json']['award_grade'] == grade
        assert result['payload_json']['award_system'] == 'PRIZE'


@pytest.mark.parametrize('grade,level,matched', [('THIRD', '', True), ('THIRD', 'SCHOOL', False), ('SPECIAL', 'SCHOOL', True), ('EXCELLENT', 'SCHOOL', True)])
def test_incomplete_or_unlisted_sports_awards_remain_unresolved(grade, level, matched):
    result, meta, _ = suggest('ARTS', '校运会跑步比赛', grade=grade, level=level, matched=matched)
    assert result['requested_score'] == '0.37' and meta['status'] == 'NEEDS_REVIEW'
    assert meta['value'] is None and meta['missing_facts']


def test_known_award_grade_is_not_reported_as_missing_when_only_scope_is_unknown():
    result, meta, _ = suggest('RESEARCH', '工程设计竞赛一等奖', grade='FIRST')
    assert result['payload_json']['award_grade'] == 'FIRST'
    assert meta['missing_facts'] == [{'code': 'AWARD_LEVEL', 'label': '赛事级别'}]
    assert meta['value'] is None


@pytest.mark.parametrize('category,text', [
    ('RESEARCH', '示例同学未参编学术专著，第一署名单位西安交通大学。'),
    ('POST', '示例同学未担任班长，任期12个月，考评结果良好。'),
    ('SERVICE', '示例同学志愿服务工时未认证16小时。'),
    ('REWARD', '示例同学校级精神文明荣誉称号未被表彰。'),
])
def test_negated_personal_authorship_role_hours_and_honor_are_not_positive_points(category, text):
    linked = {'status': 'NOT_FOUND', 'identity_matched': True, 'identity_count': 1,
              'personal_contexts': [{'text': text}], 'common_contexts': [], 'facts': {}}
    result, meta, _ = normalize_policy_suggestion(category, [{'text': text}], {'requested_score': '0'},
                                                 {'requested_score': '99', 'payload_json': {}}, linked, '示例同学')
    assert result['requested_score'] == '0.00' and meta['status'] == 'NEEDS_REVIEW'
    assert meta['value'] is None and 'NEGATED_BASIS' in meta['reason_codes']


@pytest.mark.parametrize("tier,expected", [("TIER_1", ["4.00", "3.50", "3.00"]), ("TIER_2", ["3.50", "3.00", "2.50"]), ("TIER_3", ["2.00", "1.50", "1.00"]), ("TIER_4", ["1.00", "0.75", "0.50"])])
def test_position_assessment_matrix_requires_own_tier_and_full_tenure(tier, expected):
    for assessment, score in zip(["EXCELLENT", "GOOD", "PASS"], expected):
        result, _, _ = suggest("POST", "任职考评表，任期满一年", assessment=assessment, extra={"position_tier": tier}, roster=True)
        assert result["requested_score"] == score
    assert suggest("POST", "任期满一年", assessment="FAIL", extra={"position_tier": tier})[0]["requested_score"] == "0.00"


def test_position_excellent_does_not_default_to_four_without_level_and_year():
    for text in ("学生工作考评优秀", "担任班长，任期6个月，考核优秀", "担任班长，任期不满一年，考核优秀"):
        result, meta, _ = suggest("POST", text, assessment="EXCELLENT")
        assert result["requested_score"] == "0.37" and meta["requires_confirmation"]
    assert suggest("POST", "担任班长，任期满一年，考核良好", assessment="GOOD")[0]["requested_score"] == "1.50"


def test_roster_position_is_not_inferred_from_someone_elses_job():
    result, metadata, _ = suggest("POST", "甲担任院学生会主席，乙为文艺委员，任期满一年", assessment="PASS", roster=True)
    assert result["requested_score"] == "0.37" and metadata["requires_confirmation"]


@pytest.mark.parametrize("position", ["副班长", "副团支书", "班长助理", "院学生会主席团成员"])
def test_deputy_assistant_or_committee_title_is_not_automatically_promoted(position):
    result, meta, _ = suggest("POST", f"担任{position}，任期满一年，考评优秀", assessment="EXCELLENT")
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_own_practice_assessment_is_used_instead_of_first_excellent_row():
    result, _, _ = suggest("SERVICE", "校级社会实践团队考核结果：甲优秀、乙良好、示例同学合格", assessment="PASS", level="SCHOOL", roster=True)
    assert result["requested_score"] == "1.00" and result["payload_json"]["assessment_result"] == "PASS"
    result, _, _ = suggest("SERVICE", "校级社会实践团队考核结果：示例同学不合格", assessment="FAIL", roster=True)
    assert result["requested_score"] == "0.00"


def test_unlinked_results_and_model_guessed_grade_never_determine_a_score():
    result, metadata, _ = suggest("RESEARCH", "蓝桥杯全国总决赛，其他同学一等奖，本人行未取得", matched=False)
    assert result["requested_score"] == "0.37" and metadata["requires_confirmation"]
    assert "award_grade" not in result["payload_json"] and "assessment_result" not in result["payload_json"]


def test_project_grade_is_not_a_competition_prize_or_category_cap():
    result, metadata, _ = suggest("RESEARCH", "国家级大学生创新训练项目结项，考核优秀", assessment="EXCELLENT")
    assert result["requested_score"] == "0.37" and metadata["requires_confirmation"]
    assert result["payload_json"]["research_type"] == "PROJECT" and result["payload_json"]["project_level"] == "NATIONAL"


@pytest.mark.parametrize("hours,score", [("16", "0.50"), ("28", "0.88"), ("32", "1.00"), ("96", "1.00")])
def test_volunteer_hours_have_one_annual_point_not_repeated_32_hour_blocks(hours, score):
    result, _, _ = suggest("SERVICE", f"志愿服务累计工时{hours}小时")
    assert result["requested_score"] == score


def test_volunteer_plus_honor_does_not_silently_defeat_existing_annual_aggregation():
    result, meta, _ = suggest("SERVICE", "志愿服务工时32小时，获先进个人", extra={"advanced_individual": True})
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]
    result, meta, _ = suggest("SERVICE", "志愿服务工时32小时，获先进个人")
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_roster_never_uses_other_peoples_scope_even_when_own_grade_is_linked():
    result, meta, _ = suggest("RESEARCH", "竞赛名单，甲省级二等奖，示例同学二等奖但级别栏未对应", grade="SECOND", roster=True)
    assert result["payload_json"]["award_grade"] == "SECOND" and result["requested_score"] == "0.37" and meta["requires_confirmation"]
    result, meta, _ = suggest("SERVICE", "考评名册，甲参加校级社会实践团队，示例同学合格但项目不明", assessment="PASS", roster=True)
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_papers_need_personal_author_rank_first_affiliation_and_documented_publication_type():
    result, _, _ = suggest("RESEARCH", "学校认定的国际期刊论文，第一署名单位西安交通大学，示例同学为第二作者", matched=False)
    assert result["requested_score"] == "8.00" and result["payload_json"]["author_rank"] == "2"
    result, meta, _ = suggest("RESEARCH", "学校认定的国际期刊论文，第一署名单位西安交通大学，其他人为第一作者")
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]
    result, meta, _ = suggest("RESEARCH", "学校认定的国际期刊论文，示例同学为第一作者，西安交通大学")
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_volunteer_does_not_need_an_award_but_needs_a_personal_identity():
    result, meta, _ = suggest("SERVICE", "志愿服务工时32小时", matched=False)
    assert result["requested_score"] == "1.00" and meta["status"] == "RULE_APPLIED"
    outcome = {"status": "NOT_FOUND", "facts": {}, "identity_count": 0, "is_roster": False}
    result, meta, _ = normalize_policy_suggestion("SERVICE", [{"text": "志愿服务工时32小时"}], {"requested_score": ".37"}, {"requested_score": "10", "payload_json": {}}, outcome)
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


@pytest.mark.parametrize('hours', ['-16小时', '−16小时', '-16小时，另有32小时'])
def test_negative_volunteer_hours_are_not_silently_converted_to_positive(hours):
    result, policy, _ = suggest('SERVICE', '本人志愿服务认证工时' + hours, matched=False)
    assert policy['status'] == 'NEEDS_REVIEW' and policy['value'] is None
    assert result['requested_score'] == '0.37'


@pytest.mark.parametrize('months', ['-12个月', '−12个月', '12.5个月'])
def test_invalid_personal_tenure_cannot_fall_back_to_common_full_year(months):
    outcome = {'status': 'MATCHED', 'identity_matched': True, 'identity_count': 1, 'is_roster': True,
               'facts': {'assessment_result': 'GOOD'},
               'personal_contexts': [{'text': '本人担任班长，任期' + months + '，考评良好。'}],
               'common_contexts': [{'text': '班级干部任职考核，统一任期满一年。'}]}
    result, policy, _ = normalize_policy_suggestion('POST', [], {'requested_score': '0'},
        {'requested_score': '99', 'payload_json': {}}, outcome)
    assert policy['status'] == 'NEEDS_REVIEW' and policy['value'] is None
    assert result['requested_score'] == '0.00'
    outcome.update(identity_count=1, is_roster=True)
    result, meta, _ = normalize_policy_suggestion("SERVICE", [{"text": "多人名单，其他人志愿服务工时32小时"}], {"requested_score": ".37"}, {"requested_score": "10", "payload_json": {}}, outcome)
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_personal_placement_does_not_require_an_unrelated_award_or_assessment():
    result, meta, _ = suggest("SERVICE", "示例同学参加省级挂职锻炼，出具个人见习证明", matched=False)
    assert result["requested_score"] == "2.00" and meta["status"] == "RULE_APPLIED"


def test_ninth_monograph_author_is_not_given_an_invented_minimum_one():
    result, meta, _ = suggest("RESEARCH", "出版专著，第一署名单位西安交通大学，示例同学为第9作者", matched=False)
    assert result["requested_score"] == "0.37" and meta["requires_confirmation"]


def test_unlisted_patent_reference_is_not_guessed_and_student_score_is_never_mutated():
    revision = {"requested_score": "2.70"}
    result, meta, _ = normalize_policy_suggestion("RESEARCH", [{"text": "发明专利，第一署名单位西安交通大学，示例同学为第一作者"}], revision, {"requested_score": "10", "payload_json": {}}, {"status": "MATCHED", "facts": {}}, "示例同学")
    assert revision == {"requested_score": "2.70"} and result["requested_score"] == "2.70" and meta["requires_confirmation"]


def test_unresolved_source_preserves_current_human_fact_fields_without_using_them_as_proof():
    revision = {"requested_score": "2.70", "payload_json": {"award_level": "PROVINCIAL", "award_grade": "THIRD"}}
    result, meta, _ = normalize_policy_suggestion("RESEARCH", [{"text": "材料需要人工补充"}], revision, {"requested_score": "10", "payload_json": {"award_level": "NATIONAL", "award_grade": "FIRST"}}, {"status": "NOT_FOUND", "facts": {}})
    assert result["requested_score"] == "2.70" and result["payload_json"]["award_grade"] == "THIRD"
    assert result["payload_json"]["award_level"] == "PROVINCIAL" and meta["requires_confirmation"]


@pytest.mark.parametrize("missing_model_title", [False, True])
def test_analysis_persists_correct_national_second_grade_and_does_not_enforce_manual_score(material_app, monkeypatch, missing_model_title):
    _, login = material_app
    client = login("student")
    config = {"enabled": True, "secret_id": "synthetic", "secret_key": "synthetic", "deepseek_key": "synthetic", "deepseek_model": "test", "general_limit": 100, "seal_limit": 100, "max_analysis_pages": 12}
    monkeypatch.setattr(materials, "configuration", lambda: config)
    category = next(category["id"] for category in client.get("/api/bootstrap").json()["categories"] if category["code"] == "RESEARCH")
    claim = client.post("/api/applications", json={"category_id": category}).json()
    file = upload(client, claim["id"])
    title = "蓝桥杯全国总决赛二等奖"
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        run = _text_run(conn, page, [("活动名称：" + title, [10, 10, 260, 25]), (context["user"]["display_name"], [10, 90, 100, 20])])
        ocr = {"status": "SUCCEEDED", "policy_version": materials.OCR_POLICY_VERSION, "application_id": claim["id"], "input_revision_id": revision["id"], "input_fingerprint": materials.revision_fingerprint(conn, revision), "ocr_fingerprint": materials.ocr_input_fingerprint(conn, revision), "page_ids": [page["id"]], "run_ids": [run["id"]], "warnings": [], "suggested_crops": []}
    monkeypatch.setattr(outcome_evidence, "linked_outcome", lambda *args, **kwargs: {"status": "MATCHED", "facts": {"award_grade": "SECOND", "award_level": "NATIONAL"}, "anchors": [], "is_roster": False, "identity_count": 1})

    def model(prompt, unused):
        assert prompt["linked_outcome"]["facts"]["award_grade"] == "SECOND"
        return {"title": "" if missing_model_title else title, "requested_score": "10", "payload_json": {"award_level": "全国总决赛二等奖", "award_grade": "FIRST"}}

    monkeypatch.setattr(materials, "deepseek_suggestion", model)
    result = materials.run_ai(claim["id"], context["user"], ocr_result=ocr)
    assert result["status"] == "SUCCEEDED" and result["score_policy"]["status"] == "RULE_APPLIED"
    assert result["suggestion"]["requested_score"] == "9.00"
    assert result["suggestion"]["title_requires_confirmation"] == missing_model_title
    assert result["suggestion"]["score_requires_confirmation"] is False
    applied = client.post(f"/api/applications/{claim['id']}/apply-analysis", json={"analysis_id": result["analysis_id"]})
    assert applied.status_code == 200
    assert applied.json()["requested_score"] == "9.00"
    assert applied.json()["revision"]["payload_json"]["award_level"] == "NATIONAL"
    assert applied.json()["revision"]["payload_json"]["award_grade"] == "SECOND"
    manual = client.put(f"/api/applications/{claim['id']}", json={"requested_score": "7.50"})
    assert manual.status_code == 200 and manual.json()["requested_score"] == "7.50"
