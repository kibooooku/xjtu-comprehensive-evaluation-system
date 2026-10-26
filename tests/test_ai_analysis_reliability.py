"""Synthetic failures only: model text and credentials never enter diagnostics."""
import json

import httpx
import pytest

from app import db, integrations, materials, pdf_text
from app.integrations import ProviderError
from test_materials import material_app, create_claim, upload, png, _text_run
from test_ocr_page_policy import _pdf, _cloud, _user


CONFIG = {"enabled": True, "secret_id": "synthetic", "secret_key": "synthetic", "deepseek_key": "synthetic-private-key", "deepseek_model": "test", "general_limit": 100, "seal_limit": 100, "max_analysis_pages": 12}


def _model_response(monkeypatch, content, inspect=None):
    requests = []
    client_type = httpx.Client

    def respond(request):
        requests.append(request)
        if inspect:
            inspect(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    monkeypatch.setattr(integrations.httpx, "Client", lambda *args, **kwargs: client_type(transport=httpx.MockTransport(respond)))
    return requests


@pytest.mark.parametrize("wrapper,mode", [("{}", "JSON"), ("```json\n{}\n```", "JSON"), ("建议如下：\n{}\n请按原件核对。", "SINGLE_OBJECT")])
def test_single_model_object_allows_harmless_wrappers_without_extra_request(monkeypatch, wrapper, mode):
    value = {"title": "周末广场舞", "requested_score": "0.30", "payload_json": {"subtype": "集体活动"}}
    requests = _model_response(monkeypatch, wrapper.format(json.dumps(value, ensure_ascii=False)))
    suggestion = integrations.deepseek_suggestion({"untrusted_materials": []}, CONFIG)
    assert suggestion["title"] == value["title"] and suggestion["requested_score"] == "0.30"
    assert suggestion["_provider_diagnostics"]["parse_mode"] == mode
    assert suggestion["_provider_diagnostics"]["finish_reason"] == "stop"
    assert len(requests) == 1


@pytest.mark.parametrize("content", ['{"title":"private-proof", "requested_score":', '{"title":"A","requested_score":".3"} {"title":"B","requested_score":".6"}', '示例：{"title":"A", "requested_score":".3",}'])
def test_malformed_or_ambiguous_model_output_is_not_repaired_or_retried(monkeypatch, content):
    requests = _model_response(monkeypatch, content)
    with pytest.raises(ProviderError) as caught:
        integrations.deepseek_suggestion({}, CONFIG)
    error = caught.value
    assert error.code == "OutputJSONError" and len(requests) == 1
    assert error.diagnostics["content_characters"] == len(content)
    assert isinstance(error.diagnostics["json_error_position"], int)
    serialized = json.dumps(error.diagnostics, ensure_ascii=False) + error.message
    assert "private-proof" not in serialized and CONFIG["deepseek_key"] not in serialized
    assert error.diagnostics["json_error_kind"] in {"EXPECTING_VALUE", "EXTRA_DATA", "EXPECTING_PROPERTY"}
    assert set(error.diagnostics) <= {"content_characters", "content_lines", "content_shape", "json_error_position", "json_error_line", "json_error_column", "json_error_kind", "finish_reason"}


def _input(client, monkeypatch):
    monkeypatch.setattr(materials, "configuration", lambda: CONFIG)
    claim, _, _ = create_claim(client)
    file = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        user = context["user"]
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        run = _text_run(conn, page, [("周末广场舞，2026年10月24日", [10, 10, 200, 25]), (user["display_name"], [10, 60, 100, 20])])
        ocr = {"status": "SUCCEEDED", "policy_version": materials.OCR_POLICY_VERSION, "application_id": claim["id"], "input_revision_id": revision["id"], "input_fingerprint": materials.revision_fingerprint(conn, revision), "ocr_fingerprint": materials.ocr_input_fingerprint(conn, revision), "page_ids": [page["id"]], "run_ids": [run["id"]], "warnings": [], "suggested_crops": []}
    return claim, user, ocr


def test_analyze_real_adapter_serializes_material_context_and_persists_safe_shape(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, user, ocr = _input(client, monkeypatch)
    contexts = []

    def inspect(payload):
        context = json.loads(payload["messages"][1]["content"])
        contexts.append(context)
        assert context["untrusted_materials"] and context["context_excerpt"]["coverage_complete"]
        assert "活动名称与本人参与信息分开判断" in payload["messages"][0]["content"]

    content = "建议：\n" + json.dumps({"title": "周末广场舞", "requested_score": "0.30", "payload_json": {}}, ensure_ascii=False)
    requests = _model_response(monkeypatch, content, inspect)
    result = materials.run_ai(claim["id"], user, ocr_result=ocr)
    assert result["status"] == "SUCCEEDED" and len(requests) == 1 and contexts
    assert result["provider_diagnostics"]["source_title_supported"]
    assert result["provider_diagnostics"]["parse_mode"] == "SINGLE_OBJECT"
    with db.read() as conn:
        analysis = db.one(conn, "ai_analyses", result["analysis_id"])
        assert analysis["prompt_version"] == "moral-score-v2.1"
        assert analysis["result_json"]["provider_diagnostics"] == result["provider_diagnostics"]
        revision = db.one(conn, "application_revisions", ocr["input_revision_id"])
        assert materials.revision_fingerprint(conn, revision) == ocr["input_fingerprint"]
    assert "_provider_diagnostics" not in result["suggestion"]


def test_analyze_bad_json_keeps_input_and_stores_only_diagnostic_counts(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, user, ocr = _input(client, monkeypatch)
    requests = _model_response(monkeypatch, '{"title":"private-proof", "requested_score":')
    result = materials.run_ai(claim["id"], user, ocr_result=ocr)
    assert result["error_code"] == "OutputJSONError" and len(requests) == 1
    assert result["provider_diagnostics"]["json_error_line"] == 1
    with db.read() as conn:
        analysis = db.one(conn, "ai_analyses", result["analysis_id"])
        assert analysis["status"] == "FAILED"
        assert "private-proof" not in json.dumps(analysis["result_json"])
        assert CONFIG["deepseek_key"] not in json.dumps(analysis["result_json"])
        revision = db.one(conn, "application_revisions", ocr["input_revision_id"])
        assert materials.revision_fingerprint(conn, revision) == ocr["input_fingerprint"]


@pytest.mark.parametrize("title,status", [(None, "INVALID_TYPE"), ("", "EMPTY"), ("活动证明", "GENERIC")])
def test_missing_titles_remain_manual_with_safe_distinct_diagnostics(material_app, monkeypatch, title, status):
    _, login = material_app
    client = login("student")
    claim, user, ocr = _input(client, monkeypatch)
    monkeypatch.setattr(materials, "deepseek_suggestion", lambda *args, **kwargs: {"title": title, "requested_score": "0.30", "_provider_diagnostics": {"content_shape": "OBJECT", "content_characters": 100, "raw_output": "private-proof", "Authorization": "private-key"}})
    result = materials.run_ai(claim["id"], user, ocr_result=ocr)
    assert result["status"] == "MANUAL_REQUIRED" and result["error_code"] == "ActivityNameMissing"
    assert result["provider_diagnostics"]["title_status"] == status
    assert "private-proof" not in json.dumps(result) and "private-key" not in json.dumps(result)


def test_eight_rope_title_variants_have_a_specific_material_anchor():
    texts = [{"text": "书院体育节八字绕绳项目"}]
    assert materials.activity_title_supported("2026年８字绕绳比赛", texts, [])
    assert materials.activity_title_supported("八字绕绳比赛", [{"text": "书院8 字 绕 绳项目"}], [])
    assert materials._name_region("八字绕绳项目参与记录", [])
    assert not materials.activity_title_supported("2026年８字绕绳比赛", [{"text": "其他项目"}], [])
    assert not materials.specific_activity_title("活动证明")


def test_personal_match_on_first_page_keeps_activity_heading_outside_match_window(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    _pdf(client, claim["id"], 3)
    user = _user()
    with db.read() as conn:
        number = db.one(conn, "student_profiles", user_id=user["id"])["student_number"]
    heading = "启明星夜跑活动"

    def extract(path, pages):
        result = {}
        for page in pages:
            text = ((heading + "\n") if page["page_index"] == 1 else "") + "其他材料正文。" * 3200
            if page["page_index"] == 1:
                text += f"\n{user['display_name']} {number} 本人参与。"
            result[page["id"]] = {"usable": True, "text": text, "reason": "TEXT_LAYER", "regions": [{"text": text, "polygon_json": materials._polygon([5, 5, 300, 30]), "extra_json": {"words": []}}]}
        return result

    monkeypatch.setattr(pdf_text, "extract_pages_text", extract)
    calls = _cloud(monkeypatch)
    ocr = materials.run_ocr(claim["id"], user)
    captured = []

    def model(context, config):
        captured.append(context)
        return {"title": heading, "requested_score": "0.30", "payload_json": {}}

    monkeypatch.setattr(materials, "deepseek_suggestion", model)
    result = materials.run_ai(claim["id"], user, ocr_result=ocr)
    assert result["status"] == "SUCCEEDED" and calls == ["SealOCR", "SealOCR"]
    texts = captured[0]["untrusted_materials"]
    assert sum(len(item["text"]) for item in texts) <= 48000
    assert any(number in item["text"] for item in texts) and any(heading in item["text"] for item in texts)
    personal = next(item for item in texts if item.get("excerpt_reason") == "账户姓名或学号命中上下文")
    boundary = next(item for item in texts if heading in item["text"])
    assert personal["page_id"] == boundary["page_id"] and personal is not boundary
    assert not captured[0]["context_excerpt"]["coverage_complete"]


def _named_input(client, monkeypatch, filename, source):
    monkeypatch.setattr(materials, "configuration", lambda: CONFIG)
    claim, _, _ = create_claim(client)
    assert client.put(f"/api/applications/{claim['id']}", json={"title": "学生当前填写的活动", "requested_score": "0.90", "payload_json": {"edition_key": "current-draft"}}).status_code == 200
    response = client.post(f"/api/applications/{claim['id']}/files", files={"file": (filename, png(), "image/png")})
    assert response.status_code == 200
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", response.json()["pages"][0]["id"])
        run = _text_run(conn, page, [(source, [10, 10, 250, 30])])
        ocr = {"status": "SUCCEEDED", "policy_version": materials.OCR_POLICY_VERSION, "application_id": claim["id"], "input_revision_id": revision["id"], "input_fingerprint": materials.revision_fingerprint(conn, revision), "ocr_fingerprint": materials.ocr_input_fingerprint(conn, revision), "page_ids": [page["id"]], "run_ids": [run["id"]], "warnings": [], "suggested_crops": []}
    return claim, context, revision, ocr


def test_empty_model_title_gets_correlated_specific_name_but_never_model_score(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, context, revision, ocr = _named_input(client, monkeypatch, "26春星荧夜跑集体活动.png", "本次星 荧 夜 跑活动参与情况需核对。")
    monkeypatch.setattr(materials, "deepseek_suggestion", lambda *args, **kwargs: {"title": "", "requested_score": "99.00", "activity_id": "model-guessed-activity", "payload_json": {"edition_key": "model-guess"}, "explanation": "无法确认本人参与，需要核对原件。"})
    result = materials.run_ai(claim["id"], context["user"], ocr_result=ocr)
    assert result["status"] == "SUCCEEDED" and result["needs_review"]
    assert result["title_resolution"] == {"status": "GROUNDED_REVIEW", "source": "FILENAME_AND_TEXT", "candidate_count": 1, "score_policy": "PRESERVE_CURRENT_DRAFT"}
    suggestion = result["suggestion"]
    assert suggestion["title"] == "星荧夜跑" and suggestion["title_source"] == "FILENAME_AND_TEXT"
    assert suggestion["title_requires_confirmation"] and suggestion["score_requires_confirmation"] and suggestion["match_uncertain"]
    assert suggestion["requested_score"] == "0.90" and suggestion["payload_json"]["edition_key"] == "current-draft"
    assert suggestion["activity_id"] is None
    assert result["provider_diagnostics"]["title_status"] == "EMPTY"
    with db.read() as conn:
        assert materials.revision_fingerprint(conn, db.one(conn, "application_revisions", revision["id"])) == ocr["input_fingerprint"]
    applied = client.post(f"/api/applications/{claim['id']}/apply-analysis", json={"analysis_id": result["analysis_id"]})
    assert applied.status_code == 200
    assert applied.json()["title"] == "星荧夜跑" and applied.json()["requested_score"] == "0.90"
    assert applied.json()["student_modified_score"]
    assert applied.json()["revision"]["payload_json"]["edition_key"] == "current-draft"
    assert applied.json()["latest_analysis"]["result_json"]["needs_review"]


@pytest.mark.parametrize("filename,source,expected_origin", [
    ("毫无依据夜跑.png", "星荧夜跑活动", None),
    ("星交夜跑集体活动.png", "星荧夜跑活动", None),
    ("夜跑.png", "星荧夜跑活动", None),
    ("证明材料.png", "活动名称：星荧夜跑活动", "EXPLICIT_BODY"),
    ("普通材料.png", "本次《星荧夜跑》活动记录", "EXPLICIT_BODY"),
])
def test_title_resolution_needs_exact_qualified_evidence_and_not_just_nightrun(material_app, monkeypatch, filename, source, expected_origin):
    _, login = material_app
    client = login("student")
    _, context, revision, ocr = _named_input(client, monkeypatch, filename, source)
    with db.read() as conn:
        texts, _ = materials._ai_material_excerpt(conn, ocr, context["student"], context["user"])
        resolved, resolution = materials._resolve_activity_title(conn, revision, texts, context["student"], context["user"], {"title": "", "requested_score": "9.99"})
        if expected_origin:
            assert resolved["title"] == "星荧夜跑" and resolved["requested_score"] == "0.90"
            assert resolution["source"] == expected_origin and resolution["candidate_count"] == 1
        else:
            assert resolved["title"] == "" and resolution["candidate_count"] == 0
            assert resolution["status"] == "MISSING_OR_AMBIGUOUS"
        assert materials.revision_fingerprint(conn, db.one(conn, "application_revisions", revision["id"])) == ocr["input_fingerprint"]


def test_two_different_named_activities_never_merge_into_one_fallback(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, context, revision, ocr = _named_input(client, monkeypatch, "26春星荧夜跑集体活动.png", "星荧夜跑活动和星光夜跑活动分别记录。")
    second = client.post(f"/api/applications/{claim['id']}/files", files={"file": ("26春星光夜跑集体活动.png", png(color="blue"), "image/png")})
    assert second.status_code == 200
    with db.read() as conn:
        candidates = materials._grounded_title_candidates(conn, revision, [{"text": "星荧夜跑与星光夜跑分别开展"}], context["student"], context["user"])
        assert len(candidates) == 2
        resolved, resolution = materials._resolve_activity_title(conn, revision, [{"text": "星荧夜跑与星光夜跑分别开展"}], context["student"], context["user"], {"title": "", "requested_score": "9.99"})
        assert not resolved["title"] and resolution["status"] == "MISSING_OR_AMBIGUOUS"


def test_same_specific_core_with_academic_year_and_proof_suffix_is_one_candidate(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, context, revision, _ = _named_input(client, monkeypatch, "26春星荧夜跑集体活动.png", "2025—2026学年星荧夜跑活动记录")
    response = client.post(f"/api/applications/{claim['id']}/files", files={"file": ("2025-2026学年星荧夜跑参与证明.png", png(color="blue"), "image/png")})
    assert response.status_code == 200
    with db.read() as conn:
        resolved, resolution = materials._resolve_activity_title(conn, revision, [{"text": "2025—2026学年星荧夜跑活动记录"}], context["student"], context["user"], {"title": "", "requested_score": "9.99"})
    assert resolution["candidate_count"] == 1 and resolved["title"] == "星荧夜跑"
    assert resolved["requested_score"] == "0.90"


@pytest.mark.parametrize("limit", [0, 80])
def test_excerpt_honors_smaller_custom_budget_even_when_person_matches(material_app, monkeypatch, limit):
    _, login = material_app
    client = login("student")
    _, context, revision, ocr = _named_input(client, monkeypatch, "星荧夜跑.png", "其他材料正文。" * 200)
    with db.write() as conn:
        region = db.rows(conn, "recognition_regions", run_id=ocr["run_ids"][0])[0]
        db.change(conn, "recognition_regions", region["id"], text=region["text"] + context["user"]["display_name"])
    with db.read() as conn:
        texts, metadata = materials._ai_material_excerpt(conn, ocr, context["student"], context["user"], max_chars=limit)
    assert sum(len(item["text"]) for item in texts) <= limit
    assert metadata["selected_character_count"] <= limit and not metadata["coverage_complete"]
