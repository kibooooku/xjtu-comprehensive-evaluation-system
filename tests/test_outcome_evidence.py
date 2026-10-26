"""Result words belong to a participant row/cell, not a whole document."""
import io

import pytest
from PIL import Image, ImageDraw

from app import db, materials, outcome_evidence, submissions
from test_materials import material_app, create_claim, png


def _input(client, pages=1):
    claim, _, _ = create_claim(client)
    if pages == 1:
        data, name, mime = png(1000, 1600), "虚构考评.png", "image/png"
    else:
        stream = io.BytesIO()
        Image.new("RGB", (500, 800), "white").save(stream, format="PDF", save_all=True, append_images=[Image.new("RGB", (500, 800), "white")])
        data, name, mime = stream.getvalue(), "虚构两页名单.pdf", "application/pdf"
    uploaded = client.post(f"/api/applications/{claim['id']}/files", files={"file": (name, data, mime)})
    assert uploaded.status_code == 200
    with db.read() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        records = sorted(db.rows(conn, "file_pages", file_id=uploaded.json()["id"]), key=lambda page: page["page_index"])
    return claim, context, revision, records


def _line(conn, page, text, x, y, height=18):
    runs = db.rows(conn, "recognition_runs", page_id=page["id"])
    run = runs[0] if runs else db.add(conn, "recognition_runs", page_id=page["id"], provider="TENCENT", api_name="GeneralAccurateOCR", parameters_hash="test-only", status="SUCCEEDED", result_json={}, started_at=None, finished_at=db.now())
    words, cursor = [], x
    for char in text:
        if char.isspace():
            cursor += 36
        else:
            words.append({"text": char, "polygon": materials._polygon([cursor, y, 11, height])})
            cursor += 12
    return db.add(conn, "recognition_regions", run_id=run["id"], region_no=len(db.rows(conn, "recognition_regions", run_id=run["id"])), kind="TEXT", text=text, confidence=1, polygon_json=materials._polygon([x, y, max(2, cursor-x), height]), extra_json={"words": words})


def _result(conn, context, pages):
    return outcome_evidence.linked_outcome(conn, pages, context["student"], context["user"], context["class"]["name"])


def _suggestions(conn, context, pages):
    return [item for item in materials._evidence_suggestions(conn, pages, context["student"], context["user"], context["class"]["name"]) if item["evidence_type"] == "PARTICIPATION"]


@pytest.mark.parametrize("text,value", [("第一名", "PLACE_1"), ("第八名", "PLACE_8")])
def test_chinese_place_numbers_remain_places_not_award_grades(text, value):
    tokens = outcome_evidence._grade_tokens({"id": "test-only", "text": text, "polygon_json": materials._polygon([10, 10, 80, 20]), "extra_json": {}})
    assert len(tokens) == 1 and tokens[0]["value"] == value and tokens[0]["text"] == text


@pytest.mark.parametrize("text", ["荣获优秀团干部", "被评为优秀志愿者", "入围优秀论文"])
def test_excellent_in_an_honor_or_paper_title_is_not_a_personal_assessment(text):
    assert not outcome_evidence._grade_tokens({"id": "test-only", "text": text, "polygon_json": materials._polygon([10, 10, 250, 20]), "extra_json": {}})


def test_explicit_assessment_label_can_ground_a_prose_result():
    tokens = outcome_evidence._grade_tokens({"id": "test-only", "text": "本年度考评结果为优秀。", "polygon_json": materials._polygon([10, 10, 250, 20]), "extra_json": {}})
    assert len(tokens) == 1 and tokens[0]["kind"] == "ASSESSMENT" and tokens[0]["value"] == "EXCELLENT"


@pytest.mark.parametrize("text,level", [("国赛一等奖", "NATIONAL"), ("高水平国际竞赛一等奖", "INTERNATIONAL"), ("国际总决赛二等奖", "INTERNATIONAL"), ("省一等奖", "PROVINCIAL"), ("省一等级奖", "PROVINCIAL"), ("学校一等奖", "SCHOOL"), ("行业级竞赛一等奖", "LOCAL_OR_INDUSTRY"), ("市级竞赛一等奖", "LOCAL_OR_INDUSTRY")])
def test_checked_certificate_scope_retains_supported_policy_levels(text, level):
    regions = [{"text": text, "polygon_json": materials._polygon([100, 100, 300, 30])}]
    assert outcome_evidence._certificate_levels(regions) == {level}


@pytest.mark.parametrize("text,expected", [("优", "EXCELLENT"), ("良", "GOOD"), ("优先安排", None), ("心地善良", None)])
def test_single_character_assessments_require_a_standalone_cell(text, expected):
    tokens = outcome_evidence._grade_tokens({"id": "test-only", "text": text, "polygon_json": materials._polygon([10, 10, 160, 20]), "extra_json": {}})
    if expected:
        assert len(tokens) == 1 and tokens[0]["value"] == expected
    else:
        assert not tokens


def test_multigrade_roster_uses_exact_person_row_and_one_image(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "获奖名单", 20, 30)
        _line(conn, pages[0], "一等奖 别的同学 2099999999", 30, 150)
        _line(conn, pages[0], f"三等奖 {context['user']['display_name']} {context['student']['student_number']}", 30, 220)
        _line(conn, pages[0], "二等奖 另一同学 2088888888", 30, 290)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["award_grade"] == "THIRD"
        crops = _suggestions(conn, context, pages)
        assert len(crops) == 1 and crops[0]["participation_role"] == "PRIMARY"
        assert any(frame["field"] == "OUTCOME" and frame["box"][1] == 220 for frame in crops[0]["highlights"])
        assert all(frame["box"][1] != 150 for frame in crops[0]["highlights"] if frame["field"] == "OUTCOME")


def test_neighbor_result_is_not_attached_to_an_empty_participant_row(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "考评名单", 20, 30)
        _line(conn, pages[0], "优秀 其他同学 2099999999", 20, 150)
        _line(conn, pages[0], f"{context['user']['display_name']} {context['student']['student_number']}", 20, 220)
        _line(conn, pages[0], "良好 另一同学 2088888888", 20, 290)
        result = _result(conn, context, pages)
        assert result["status"] == "NOT_FOUND" and not result["facts"]
        assert len(_suggestions(conn, context, pages)) == 1


def test_same_name_other_number_cannot_supply_the_rating(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "考评名单", 20, 30)
        _line(conn, pages[0], f"优秀 {context['user']['display_name']} 2099999999", 20, 150)
        _line(conn, pages[0], f"合格 {context['user']['display_name']} {context['student']['student_number']}", 20, 220)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["assessment_result"] == "PASS"


def test_same_name_different_student_number_does_not_count_as_validated_identity(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "志愿服务名单", 20, 30)
        _line(conn, pages[0], f"{context['user']['display_name']} 2099999999 志愿服务32小时", 20, 150)
        result = _result(conn, context, pages)
        assert result["status"] == "NOT_FOUND" and result["identity_count"] == 0 and not result["identity_matched"]


def test_roster_level_cannot_be_borrowed_from_someone_elses_record(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "获奖名单", 20, 30)
        _line(conn, pages[0], f"二等奖 {context['user']['display_name']} {context['student']['student_number']}", 20, 150)
        _line(conn, pages[0], "省级三等奖 别人 2099999999", 20, 250)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["award_grade"] == "SECOND"
        assert "award_level" not in result["facts"]


def test_distinct_grades_on_same_baseline_remain_ambiguous(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "获奖名单", 20, 30)
        _line(conn, pages[0], f"一等奖 别人 2099999999 三等奖 {context['user']['display_name']} {context['student']['student_number']}", 20, 220)
        result = _result(conn, context, pages)
        assert result["status"] != "MATCHED" and not result["facts"]


def test_only_neighbors_grade_on_same_baseline_is_not_owners_grade(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "考评名单", 20, 30)
        _line(conn, pages[0], f"{context['user']['display_name']} {context['student']['student_number']}", 20, 220)
        _line(conn, pages[0], "旁边同学 2099999999 优秀", 550, 220)
        result = _result(conn, context, pages)
        assert result["status"] != "MATCHED" and not result["facts"]


def test_merged_rating_cell_uses_real_table_borders(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    page = pages[0]
    image = Image.new("RGB", (1000, 1600), "white")
    draw = ImageDraw.Draw(image)
    for x in (100, 700, 900):
        draw.line((x, 90, x, 190), fill="black", width=2)
    for y in (90, 190):
        draw.line((100, y, 900, y), fill="black", width=2)
    for y in (120, 150):
        draw.line((100, y, 700, y), fill="black", width=2)
    image.save(materials.asset_path(page["image_uri"]))
    with db.write() as conn:
        _line(conn, page, "考评名单", 20, 30)
        _line(conn, page, "优秀", 760, 128)
        _line(conn, page, f"{context['user']['display_name']} {context['student']['student_number']}", 200, 162)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["assessment_result"] == "EXCELLENT"
        assert result["anchors"][0]["association"] == "TABLE_CELL"


def test_two_separate_tables_at_same_height_do_not_share_a_rating():
    image = Image.new("L", (1000, 1000), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 100, 400, 200), outline=0, width=2)
    draw.rectangle((600, 100, 950, 200), outline=0, width=2)
    assert not outcome_evidence._same_table_record(image, [100, 130, 100, 20], [600, 100, 350, 100])


def test_certificate_result_far_from_name_gets_second_context(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], context["user"]["display_name"], 120, 160)
        _line(conn, pages[0], "荣获二等奖", 120, 1000)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["award_grade"] == "SECOND"
        crops = _suggestions(conn, context, pages)
        assert [crop["participation_role"] for crop in crops] == ["PRIMARY", "OUTCOME"]
        assert all(crop["box"][0] == 0 and crop["box"][2:] == [1000, 400] for crop in crops)


@pytest.mark.parametrize("reverse_order", [False, True])
def test_wrapped_provincial_certificate_uses_geometry_not_ocr_order(material_app, reverse_order):
    from app.policy_suggestions import normalize_policy_suggestion
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    lines = [("荣获第十七届蓝桥杯全国大学生软件和信息", 130, 700),
             ("技术大赛软件赛Python程序设计大学A组陕西", 100, 750),
             ("赛区一等奖。", 100, 800)]
    if reverse_order:
        lines.reverse()
    with db.write() as conn:
        _line(conn, pages[0], context["user"]["display_name"], 100, 200)
        for text, x, y in lines:
            _line(conn, pages[0], text, x, y, height=24)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and not result["is_roster"]
        assert result["facts"] == {"award_grade": "FIRST", "award_level": "PROVINCIAL"}
        suggestion, policy, warnings = normalize_policy_suggestion("RESEARCH", [{"text": text} for text, _, _ in lines], {"requested_score": "0"}, {"requested_score": "0", "payload_json": {}}, linked_outcome=result)
        assert suggestion["requested_score"] == "8.00" and policy["status"] == "RULE_APPLIED" and not warnings


@pytest.mark.parametrize("reverse_order", [False, True])
@pytest.mark.parametrize("event,region,prize,grade,category,score", [
    ("大学生电子设计竞赛", "浙江省", "一等奖", "FIRST", "RESEARCH", "8.00"),
    ("大学生创新设计大赛", "宁夏回族自治区", "二等奖", "SECOND", "RESEARCH", "6.00"),
    ("大学生机械创新设计大赛", "新疆维吾尔自治区", "三等奖", "THIRD", "RESEARCH", "4.00"),
    ("高校摄影艺术比赛", "广西壮族自治区", "二等奖", "SECOND", "ARTS", "3.50"),
    ("大学生书法艺术大赛", "广东省", "一等奖", "FIRST", "ARTS", "4.00"),
    ("大学生绘画艺术比赛", "香港特别行政区", "三等奖", "THIRD", "ARTS", "3.00"),
])
def test_generic_event_scope_grade_and_score_do_not_depend_on_certificate_filename(
        material_app, reverse_order, event, region, prize, grade, category, score):
    from app.policy_suggestions import normalize_policy_suggestion
    _, login = material_app
    # The same neutral fixture filename is used for every unrelated event.
    _, context, _, pages = _input(login("student"))
    lines = [("荣获" + event + "项目" + region, 100, 700),
             ("赛区" + prize + "。", 100, 750)]
    if reverse_order:
        lines.reverse()
    with db.write() as conn:
        _line(conn, pages[0], context["user"]["display_name"], 100, 200)
        for text, x, y in lines:
            _line(conn, pages[0], text, x, y, height=24)
        linked = _result(conn, context, pages)
        assert linked["status"] == "MATCHED" and not linked["is_roster"]
        assert linked["facts"] == {"award_grade": grade, "award_level": "PROVINCIAL"}
        suggestion, policy, warnings = normalize_policy_suggestion(
            category, [{"text": text} for text, _, _ in lines], {"requested_score": "0"},
            {"requested_score": "10", "payload_json": {"award_level": "NATIONAL", "award_grade": "FIRST"}},
            linked_outcome=linked)
        assert policy["status"] == "RULE_APPLIED" and not warnings
        assert suggestion["requested_score"] == score
        assert suggestion["payload_json"]["award_level"] == "PROVINCIAL"
        assert suggestion["payload_json"]["award_grade"] == grade


@pytest.mark.parametrize("province_position,award_position", [((100, 650), (700, 650)), ((100, 650), (700, 690)), ((100, 650), (100, 1000))])
def test_certificate_does_not_join_columns_or_distant_labels_into_provincial_scope(material_app, province_position, award_position):
    from app.policy_suggestions import normalize_policy_suggestion
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], context["user"]["display_name"], 100, 200)
        _line(conn, pages[0], "蓝桥杯软件大赛陕西", *province_position)
        _line(conn, pages[0], "赛区一等奖", *award_position)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["award_grade"] == "FIRST"
        assert "award_level" not in result["facts"] and result["award_level_checked"]
        # Even a misleading serialized order must not bypass the checked layout.
        suggestion, policy, _ = normalize_policy_suggestion("RESEARCH", [{"text": "蓝桥杯软件大赛陕西\n赛区一等奖"}], {"requested_score": "0"}, {"requested_score": "8", "payload_json": {}}, linked_outcome=result)
        assert suggestion["requested_score"] == "0.00" and policy["requires_confirmation"]


def test_wrapped_province_conflicting_with_national_final_requires_review(material_app):
    from app.policy_suggestions import normalize_policy_suggestion
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], context["user"]["display_name"], 100, 200)
        _line(conn, pages[0], "赛区一等奖", 100, 800, height=24)
        _line(conn, pages[0], "蓝桥杯全国总决赛陕西", 100, 750, height=24)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and "award_level" not in result["facts"]
        suggestion, policy, _ = normalize_policy_suggestion("RESEARCH", [{"text": "赛区一等奖\n蓝桥杯全国总决赛陕西"}], {"requested_score": "0"}, {"requested_score": "10", "payload_json": {}}, linked_outcome=result)
        assert suggestion["requested_score"] == "0.00" and policy["requires_confirmation"]


def test_single_person_same_row_award_keeps_its_common_certificate_level(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"))
    with db.write() as conn:
        _line(conn, pages[0], "蓝桥杯省赛", 100, 100)
        _line(conn, pages[0], context["user"]["display_name"] + "一等奖", 100, 300)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["anchors"][0]["association"] == "SAME_ROW"
        assert result["facts"] == {"award_grade": "FIRST", "award_level": "PROVINCIAL"}


def test_one_clear_certificate_does_not_erase_another_certificates_scope_conflict(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"), 2)
    with db.write() as conn:
        for page in pages:
            _line(conn, page, context["user"]["display_name"], 100, 200)
        _line(conn, pages[0], "蓝桥杯省赛一等奖", 100, 650)
        _line(conn, pages[1], "蓝桥杯全国总决赛陕西", 100, 700, height=24)
        _line(conn, pages[1], "赛区一等奖", 100, 750, height=24)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["award_grade"] == "FIRST"
        assert "award_level" not in result["facts"] and result["warnings"]
        assert any(anchor["award_level_conflict"] for anchor in result["anchors"])


def test_continued_roster_section_on_previous_page_is_a_second_real_image(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"), 2)
    with db.write() as conn:
        _line(conn, pages[0], "一等奖获奖名单", 280, 90, 25)
        _line(conn, pages[1], "获奖名单（续表）", 280, 70)
        _line(conn, pages[1], f"{context['user']['display_name']} {context['student']['student_number']}", 100, 220)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["anchors"][0]["association"] == "SECTION"
        crops = _suggestions(conn, context, pages)
        assert len(crops) == 2 and crops[0]["page_id"] == pages[1]["id"] and crops[1]["page_id"] == pages[0]["id"]


def test_team_result_requires_exact_team_identifier_and_subject_label(material_app):
    _, login = material_app
    _, context, _, pages = _input(login("student"), 2)
    with db.write() as conn:
        _line(conn, pages[0], "团队人员名单", 20, 30)
        _line(conn, pages[0], f"XJTU2025012345 {context['user']['display_name']} {context['student']['student_number']}", 20, 220)
        _line(conn, pages[1], "团队编号:XJTU2025012345 团队考核结果:良好", 20, 300)
        _line(conn, pages[1], "团队编号:XJTU2025999999 团队考核结果:优秀", 20, 500)
        result = _result(conn, context, pages)
        assert result["status"] == "MATCHED" and result["facts"]["assessment_result"] == "GOOD"
        assert result["anchors"][0]["association"] == "TEAM" and result["anchors"][0]["page_id"] == pages[1]["id"]


def test_manual_roles_replace_independently_and_outcome_alone_is_not_identity(material_app):
    _, login = material_app
    client = login("student")
    claim, _, revision, pages = _input(client)
    body = {"page_id": pages[0]["id"], "evidence_type": "PARTICIPATION", "participation_role": "OUTCOME", "box": [0, 600, 1000, 400], "highlights": [{"field": "OUTCOME", "box": [80, 700, 70, 25]}]}
    secondary = client.post(f"/api/applications/{claim['id']}/crops", json=body)
    assert secondary.status_code == 200 and secondary.json()["participation_role"] == "OUTCOME"
    with db.read() as conn:
        assert not submissions._evidence_summary(conn, revision)["has_participation"]
    primary = client.post(f"/api/applications/{claim['id']}/crops", json={**body, "participation_role": "PRIMARY", "box": [0, 100, 1000, 400]})
    assert primary.status_code == 200
    replaced = client.post(f"/api/applications/{claim['id']}/crops", json={**body, "participation_role": "PRIMARY", "box": [0, 200, 1000, 400]})
    assert replaced.status_code == 200
    with db.read() as conn:
        crops = db.rows(conn, "evidence_crops", revision_id=revision["id"])
        assert len(crops) == 2 and any(crop["id"] == secondary.json()["id"] for crop in crops)
        assert submissions._evidence_summary(conn, revision)["has_participation"]


def test_new_outcome_role_obeys_total_crop_limit_but_existing_role_can_replace(material_app):
    _, login = material_app
    client = login("student")
    claim, _, revision, pages = _input(client)
    body = {"page_id": pages[0]["id"], "evidence_type": "PARTICIPATION", "box": [0, 100, 1000, 400]}
    assert client.post(f"/api/applications/{claim['id']}/crops", json=body).status_code == 200
    with db.write() as conn:
        original = db.rows(conn, "evidence_crops", revision_id=revision["id"])[0]
        for index in range(29):
            copy = {key: value for key, value in original.items() if key != "id"}
            copy.update(evidence_type="VALIDITY", masks_json={"evidence": {"validity_kind": "TIME"}}, sort_order=index+1)
            db.add(conn, "evidence_crops", **copy)
    assert client.post(f"/api/applications/{claim['id']}/crops", json={**body, "participation_role": "OUTCOME"}).status_code == 400
    assert client.post(f"/api/applications/{claim['id']}/crops", json={**body, "participation_role": "PRIMARY", "box": [0, 200, 1000, 400]}).status_code == 200
    with db.read() as conn:
        assert len(db.rows(conn, "evidence_crops", revision_id=revision["id"])) == 30


@pytest.mark.parametrize("protection", ["manual", "frozen"])
def test_automatic_outcome_does_not_rewrite_protected_crops(material_app, protection):
    _, login = material_app
    client = login("student")
    claim, context, revision, pages = _input(client)
    manual = client.post(f"/api/applications/{claim['id']}/crops", json={"page_id": pages[0]["id"], "evidence_type": "PARTICIPATION", "box": [0, 100, 1000, 400]})
    assert manual.status_code == 200
    with db.write() as conn:
        revision = db.one(conn, "application_revisions", revision["id"])
        if protection == "frozen":
            db.change(conn, "evidence_crops", manual.json()["id"], source="AI")
            revision = db.change(conn, "application_revisions", revision["id"], payload_json={}, content_frozen_at=db.now())
        before = db.rows(conn, "evidence_crops", revision_id=revision["id"])
        materials._apply_evidence(conn, revision, [{"page_id": pages[0]["id"], "evidence_type": "PARTICIPATION", "participation_role": "OUTCOME", "box": [0, 800, 1000, 400], "highlights": [{"box": [100, 900, 100, 20], "field": "OUTCOME"}]}])
        assert db.rows(conn, "evidence_crops", revision_id=revision["id"]) == before
