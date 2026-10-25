"""Synthetic policy regressions: no credentials, personal fixtures or cloud calls."""
import io

import pytest
from PIL import Image, ImageDraw

from app import db, materials, pdf_text
from app.integrations import ProviderError
from test_materials import material_app, create_claim, png, upload, _text_run


def _pdf(client, claim_id, count, name="scan.pdf", shade=0):
    pages = []
    for index in range(count):
        image = Image.new("RGB", (180, 240), (255 - (index + shade) % 80, 240, 230))
        ImageDraw.Draw(image).text((10, 20), f"synthetic image page {index + 1}/{count} shade {shade}", fill="black")
        pages.append(image)
    stream = io.BytesIO()
    pages[0].save(stream, format="PDF", save_all=True, append_images=pages[1:])
    response = client.post(f"/api/applications/{claim_id}/files", files={"file": (name, stream.getvalue(), "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()


def _user():
    with db.read() as conn:
        return db.one(conn, "users", username="student")


def _cloud(monkeypatch, fail=None, text="周末广场舞，2026年10月24日"):
    config = {"enabled": True, "secret_id": "synthetic", "secret_key": "synthetic", "deepseek_key": "synthetic", "deepseek_model": "test", "general_limit": 200, "seal_limit": 200, "max_analysis_pages": 12}
    monkeypatch.setattr(materials, "configuration", lambda: config)
    calls = []

    def request(action, image, config):
        calls.append(action)
        if fail:
            fail(action, len(calls))
        return {"RequestId": "synthetic", "TextDetections": [{"DetectedText": text, "Confidence": 100, "Polygon": [{"X": 5, "Y": 5}, {"X": 250, "Y": 5}, {"X": 250, "Y": 30}, {"X": 5, "Y": 30}]}]} if action == "GeneralAccurateOCR" else {"RequestId": "synthetic", "SealInfos": []}

    monkeypatch.setattr(materials, "tencent_ocr", request)
    return calls


def test_thirteen_page_scan_ocr_only_endpoints_for_validity_and_keeps_manual_participation(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 13)
    cropped = client.post(f"/api/applications/{claim['id']}/crops", json={"page_id": file["pages"][6]["id"], "evidence_type": "VALIDITY", "validity_kind": "SEAL", "box": [10, 10, 70, 70]})
    assert cropped.status_code == 200
    user = _user()
    calls = _cloud(monkeypatch, text=f"{user['display_name']}参加周末广场舞，2026年10月24日")
    progress = []
    result = materials.run_ocr(claim["id"], _user(), lambda stage, done, total: progress.append((done, total)))
    assert calls == ["GeneralAccurateOCR", "SealOCR", "GeneralAccurateOCR", "SealOCR"]
    assert result["status"] == "SUCCEEDED" and result["partial"]
    assert result["planned_tasks"] == result["completed_tasks"] == 4
    assert result["manual_required_files"][0]["page_numbers"] == list(range(2, 13))
    assert result["manual_required_files"][0]["code"] == "MIDDLE_SCAN_PAGES_MANUAL"
    assert result["file_results"][0]["seal_status"] == "NOT_FOUND"
    coverage = result["file_results"][0]
    assert coverage["cloud_ocr_scope"] == "ENDPOINTS" and coverage["participation_mode"] == "MANUAL"
    assert coverage["ocr_page_numbers"] == [1, 13] and coverage["text_covered_page_count"] == 2
    assert progress[-1] == (4, 4)
    assert {item["validity_kind"] for item in result["suggested_crops"]} == {"NAME", "TIME"}
    assert not any(item["evidence_type"] == "PARTICIPATION" for item in result["suggested_crops"])
    with db.read() as conn:
        assert {db.one(conn, "file_pages", run["page_id"])["page_index"] for run in db.rows(conn, "recognition_runs", provider="TENCENT")} == {1, 13}
        general = next(run for run in db.rows(conn, "recognition_runs", api_name="GeneralAccurateOCR") if run["status"] == "SUCCEEDED")
        region = db.rows(conn, "recognition_regions", run_id=general["id"])[0]
        old_participation = {"page_id": general["page_id"], "evidence_type": "PARTICIPATION", "box": [0, 0, 360, 120], "region_id": region["id"]}
        assert materials._last_seal_suggestions(conn, [old_participation]) == []
    detail = client.get(f"/api/applications/{claim['id']}").json()
    assert detail["crops"][0]["id"] == cropped.json()["id"]
    assert detail["crops"][0]["automatic_policy_allowed"]
    assert detail["files"][0]["ocr_page_numbers"] == [1, 13]
    assert client.get(file["url"]).status_code == 200


def test_two_short_files_total_over_twelve_are_both_recognized(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    _pdf(client, claim["id"], 7, "a.pdf", 1)
    _pdf(client, claim["id"], 7, "b.pdf", 30)
    calls = _cloud(monkeypatch)
    progress = []
    result = materials.run_ocr(claim["id"], _user(), lambda stage, done, total: progress.append((done, total)))
    assert result["status"] == "SUCCEEDED" and not result["partial"]
    assert calls.count("GeneralAccurateOCR") == 14 and calls.count("SealOCR") == 4
    assert result["planned_tasks"] == result["completed_tasks"] == 18
    assert progress[0] == (0, 0)  # Local text-layer detection has no cloud tasks.
    assert (0, 18) in progress and progress[-1] == (18, 18)
    assert all(item["seal_pages"] == [1, 7] and item["seal_status"] == "NOT_FOUND" for item in result["file_results"])
    assert not [item for item in result["suggested_crops"] if item.get("validity_kind") == "SEAL"]
    detail = client.get(f"/api/applications/{claim['id']}").json()
    assert all(file["seal_status"] == "NOT_FOUND" and "自行寻找" in file["seal_message"] for file in detail["files"])


def test_only_first_last_seals_single_page_once_and_old_middle_seal_excluded(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 3)
    single = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][1]["id"])
        data = {"SealInfos": [{"SealBody": "synthetic obsolete stamp", "Location": {"X": 10, "Y": 100, "Width": 50, "Height": 50}}]}
        run = db.add(conn, "recognition_runs", page_id=page["id"], provider="TENCENT", api_name="SealOCR", parameters_hash="obsolete", status="SUCCEEDED", result_json=data, started_at=db.now(), finished_at=db.now())
        materials._save_regions(conn, run, data)
        region = db.rows(conn, "recognition_regions", run_id=run["id"])[0]
        ai = materials.create_crop(conn, revision, page, "VALIDITY", [10, 100, 50, 50], source="AI", region_id=region["id"], metadata={"validity_kind": "SEAL"})
        assert not materials.automatic_crop_policy(conn, ai)[0]
        manual = dict(ai, source="STUDENT")
        assert materials.automatic_crop_policy(conn, manual)[0]
        assert materials.automatic_crop_policy(conn, ai, {**revision, "content_frozen_at": db.now()})[0]
        old = {"page_id": page["id"], "evidence_type": "VALIDITY", "validity_kind": "SEAL", "box": [10, 100, 50, 50], "region_id": region["id"]}
        assert materials._last_seal_suggestions(conn, [old]) == []
    calls = _cloud(monkeypatch)
    result = materials.run_ocr(claim["id"], _user())
    assert result["planned_tasks"] == 7 and calls.count("SealOCR") == 3
    assert next(item for item in result["file_results"] if item["file_id"] == single["id"])["seal_pages"] == [1]
    assert not [item for item in result["suggested_crops"] if item.get("validity_kind") == "SEAL"]
    with db.write() as conn:
        materials._apply_evidence(conn, revision, [])
        assert not db.one(conn, "evidence_crops", ai["id"])


@pytest.mark.parametrize("code", ["LocalBudgetExceeded", "ResourcesSoldOut.ResourcePackageRunOut", "LimitExceeded.Quota"])
def test_quota_failure_stops_both_apis_and_retains_partial_text(material_app, monkeypatch, code):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    _pdf(client, claim["id"], 3)

    def fail(action, count):
        if action == "SealOCR":
            raise ProviderError(code, "合成额度用尽，停止调用。")

    calls = _cloud(monkeypatch, fail)
    progress = []
    result = materials.run_ocr(claim["id"], _user(), lambda stage, done, total: progress.append((done, total)))
    assert calls == ["GeneralAccurateOCR", "SealOCR"]
    assert result["status"] == "SUCCEEDED" and result["partial"] and result["stopped"]
    assert result["stop_reason"] == code and result["run_ids"]
    assert result["completed_tasks"] == 2 < result["planned_tasks"] == 5
    assert progress[-1] == (2, 5) and any("部分" in warning for warning in result["warnings"])


def _native_stub(monkeypatch, native_count, user_name, student_number, large=False):
    calls = []

    def extract(path, records):
        calls.append(len(records))
        result = {}
        for page in records:
            usable = page["page_index"] <= native_count
            text = "活动名称：周末广场舞，2026年10月24日。" + ("其他同学的材料。" * 2000 if large else "")
            if page["page_index"] == 7:
                text += f"\n{user_name} {student_number} 本人参与活动。\n"
            result[page["id"]] = {"usable": usable, "text": text if usable else "", "reason": "TEXT_LAYER" if usable else "NO_TEXT", "regions": [{"text": text, "polygon_json": materials._polygon([5, 5, 300, 30]), "extra_json": {"words": []}}] if usable else []}
        return result

    monkeypatch.setattr(pdf_text, "extract_pages_text", extract)
    return calls


def test_long_searchable_pdf_uses_local_text_cache_and_bounded_grounded_ai_excerpt(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 13)
    user = _user()
    with db.read() as conn:
        student = db.one(conn, "student_profiles", user_id=user["id"])
    local_calls = _native_stub(monkeypatch, 13, user["display_name"], student["student_number"], large=True)
    cloud_calls = _cloud(monkeypatch)
    result = materials.run_ocr(claim["id"], user)
    assert result["status"] == "SUCCEEDED" and not result["partial"]
    assert result["native_text_page_count"] == 13 and result["planned_tasks"] == 2
    assert cloud_calls == ["SealOCR", "SealOCR"] and result["manual_required_files"] == []
    assert result["file_results"][0]["seal_status"] == "NOT_FOUND"
    assert result["file_results"][0]["participation_mode"] == "AUTO"
    assert result["file_results"][0]["ocr_page_numbers"] == []
    assert result["file_results"][0]["text_covered_page_count"] == 13
    participation = [item for item in result["suggested_crops"] if item["evidence_type"] == "PARTICIPATION"]
    assert participation and participation[0]["origin"] == "NATIVE_PDF_TEXT"
    with db.read() as conn:
        assert materials._usage(conn, "GeneralAccurateOCR") == 0 and materials._usage(conn, "SealOCR") == 2
        assert all(run["provider"] == "LOCAL_PDF" for run in db.rows(conn, "recognition_runs", api_name=materials.NATIVE_TEXT_API))
        assert all(region["confidence"] is None for run_id in result["run_ids"] for region in db.rows(conn, "recognition_regions", run_id=run_id))
    captured = []

    def suggest(context, config):
        captured.append(context)
        return {"title": "周末广场舞", "requested_score": "0.30", "activity_id": None, "payload_json": {}}

    monkeypatch.setattr(materials, "deepseek_suggestion", suggest)
    analyzed = materials.run_ai(claim["id"], user, ocr_result=result)
    assert analyzed["status"] == "SUCCEEDED" and analyzed["policy_version"] == materials.OCR_POLICY_VERSION
    excerpt = captured[0]["context_excerpt"]
    texts = captured[0]["untrusted_materials"]
    assert sum(len(item["text"]) for item in texts) <= 48000
    assert not excerpt["coverage_complete"] and excerpt["omitted_character_count"] > 0
    assert any(student["student_number"] in item["text"] for item in texts)
    assert {1, 7, 13} <= {item["page_number"] for item in texts}
    assert any(item.get("excerpt_reason") == "本人命中页的相邻页" for item in texts)
    with db.read() as conn:
        analysis = db.one(conn, "ai_analyses", analyzed["analysis_id"])
        assert analysis["result_json"]["context_excerpt"] == excerpt
    again = materials.run_ocr(claim["id"], user)
    assert again["native_text_page_count"] == 13 and local_calls == [13] and cloud_calls == ["SealOCR", "SealOCR"]
    detail = client.get(f"/api/applications/{claim['id']}").json()
    assert detail["files"][0]["native_text_status"] == "USED"
    assert detail["files"][0]["manual_page_numbers"] == []
    assert detail["files"][0]["seal_status"] == "NOT_FOUND"


def test_long_mixed_pdf_keeps_native_pages_and_ignores_old_cloud_text_evidence(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 13)
    user = _user()
    _native_stub(monkeypatch, 5, user["display_name"], "SYNTHETIC")
    calls = _cloud(monkeypatch)
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][10]["id"])
        run = _text_run(conn, page, [(user["display_name"], [10, 100, 100, 30]), ("旧云夜跑活动", [10, 20, 150, 30])])
        region = db.rows(conn, "recognition_regions", run_id=run["id"])[0]
        crop = materials.create_crop(conn, revision, page, "PARTICIPATION", [0, 80, 360, 120], source="AI", region_id=region["id"])
        assert not materials.automatic_crop_policy(conn, crop)[0]
    result = materials.run_ocr(claim["id"], user)
    assert result["status"] == "SUCCEEDED" and result["partial"] and calls == ["SealOCR", "GeneralAccurateOCR", "SealOCR"]
    assert result["native_text_page_count"] == 5
    assert result["file_results"][0]["manual_page_numbers"] == list(range(6, 13))
    assert all(item["page_id"] != page["id"] for item in result["suggested_crops"])
    assert run["id"] not in result["run_ids"]
    detail = client.get(f"/api/applications/{claim['id']}").json()
    assert detail["files"][0]["native_text_status"] == "PARTIAL"
    assert not next(item for item in detail["crops"] if item["id"] == crop["id"])["automatic_policy_allowed"]


def test_policy_version_invalidates_both_fingerprints(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    upload(client, claim["id"])
    with db.read() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        before = (materials.ocr_input_fingerprint(conn, revision), materials.revision_fingerprint(conn, revision))
        monkeypatch.setattr(materials, "OCR_POLICY_VERSION", "SYNTHETIC_NEW_POLICY")
        after = (materials.ocr_input_fingerprint(conn, revision), materials.revision_fingerprint(conn, revision))
    assert all(old != new for old, new in zip(before, after))


def test_lower_cloud_page_limit_invalidates_cache_and_old_evidence(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 8)
    calls = _cloud(monkeypatch)
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][2]["id"])
        _text_run(conn, page, [("周末广场舞", [10, 20, 150, 30])])
        before = (materials.ocr_input_fingerprint(conn, revision), materials.revision_fingerprint(conn, revision))
        assert materials._evidence_suggestions(conn, [page], context["student"], context["user"], context["class"]["name"])
    config = materials.configuration()
    monkeypatch.setattr(materials, "configuration", lambda: {**config, "max_analysis_pages": 7})
    result = materials.run_ocr(claim["id"], _user())
    assert calls == ["GeneralAccurateOCR", "SealOCR", "GeneralAccurateOCR", "SealOCR"] and result["status"] == "SUCCEEDED"
    assert result["skipped_files"][0]["max_pages"] == 7
    assert "超过7页" in result["skipped_files"][0]["reason"]
    assert all(item["page_id"] != page["id"] for item in result["suggested_crops"])
    with db.read() as conn:
        after = (materials.ocr_input_fingerprint(conn, revision), materials.revision_fingerprint(conn, revision))
        assert all(old != new for old, new in zip(before, after))
        view = materials.file_view(conn, db.one(conn, "file_objects", file["id"]))
        assert view["max_pages"] == 7 and view["cloud_ocr_eligible"] and not view["full_page_cloud_ocr_eligible"]


def test_native_cache_key_includes_original_pdf_hash(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 13)
    user = _user()
    local_calls = _native_stub(monkeypatch, 13, user["display_name"], "SYNTHETIC")
    cloud_calls = _cloud(monkeypatch)
    first = materials.run_ocr(claim["id"], user)
    assert local_calls == [13]
    # Identical rendered pages do not establish identical hidden text layers.
    with db.write() as conn:
        db.change(conn, "file_objects", file["id"], sha256="changed-synthetic-original-hash")
    second = materials.run_ocr(claim["id"], user)
    assert local_calls == [13, 13] and cloud_calls == ["SealOCR", "SealOCR"]
    with db.read() as conn:
        native_first = {run_id for run_id in first["run_ids"] if db.one(conn, "recognition_runs", run_id)["api_name"] == materials.NATIVE_TEXT_API}
        native_second = {run_id for run_id in second["run_ids"] if db.one(conn, "recognition_runs", run_id)["api_name"] == materials.NATIVE_TEXT_API}
    assert native_first.isdisjoint(native_second)


def test_native_public_redaction_fails_closed_for_unpositioned_identifier(material_app):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        run = db.add(conn, "recognition_runs", page_id=page["id"], provider="LOCAL_PDF", api_name=materials.NATIVE_TEXT_API, parameters_hash="synthetic", status="SUCCEEDED", result_json={"usable": True, "text": "学生编号 1234567890"}, started_at=None, finished_at=db.now())
        region = db.add(conn, "recognition_regions", run_id=run["id"], region_no=0, kind="TEXT", text="学生编号", confidence=None, polygon_json=materials._polygon([10, 20, 100, 30]), extra_json={"words": []})
        blocked = materials.create_crop(conn, revision, page, "VALIDITY", [0, 0, 200, 90], source="STUDENT", metadata={"validity_kind": "NAME"})
        assert blocked["public_asset_status"] == "BLOCKED" and not blocked["public_asset_uri"]
        db.change(conn, "recognition_regions", region["id"], text="学生编号1234567890")
        ready = materials.create_crop(conn, revision, page, "VALIDITY", [0, 0, 200, 90], source="STUDENT", metadata={"validity_kind": "NAME"})
        assert ready["public_asset_status"] == "READY"
        with Image.open(materials.asset_path(ready["public_asset_uri"])) as image:
            assert image.getpixel((25, 30)) == (37, 50, 68)
            assert image.getpixel((180, 80)) == (255, 255, 255)


@pytest.mark.parametrize("native_count,final_status", [(13, "USED"), (0, "NONE")])
def test_long_pdf_detail_waits_for_every_text_layer_check_before_manual_decision(material_app, monkeypatch, native_count, final_status):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = _pdf(client, claim["id"], 13)
    assert file["automatic_ocr_status"] == "CHECKING_TEXT_LAYER"
    assert file["native_text_status"] == "PENDING"
    assert not file["text_layer_check_complete"] and file["text_layer_checked_page_count"] == 0
    assert file["ocr_skip_reason"] is None and file["manual_page_numbers"] == []
    user = _user()
    _native_stub(monkeypatch, native_count, user["display_name"], "SYNTHETIC")
    cloud_calls = _cloud(monkeypatch)
    with db.read() as conn:
        record = db.one(conn, "file_objects", file["id"])
        first_page = db.one(conn, "file_pages", file["pages"][0]["id"])
    # Both a positive local page and a checked negative page are still pending
    # while the rest of the document has not yet been inspected.
    materials._native_pages(record, [first_page])
    partial = client.get(f"/api/applications/{claim['id']}").json()["files"][0]
    assert partial["text_layer_checked_page_count"] == 1 and not partial["text_layer_check_complete"]
    assert partial["automatic_ocr_status"] == "CHECKING_TEXT_LAYER" and partial["native_text_status"] == "PENDING"
    assert partial["ocr_skip_reason"] is None and partial["manual_page_numbers"] == []
    materials.run_ocr(claim["id"], user)
    final = client.get(f"/api/applications/{claim['id']}").json()["files"][0]
    assert cloud_calls == (["SealOCR", "SealOCR"] if native_count else ["GeneralAccurateOCR", "SealOCR", "GeneralAccurateOCR", "SealOCR"])
    assert final["text_layer_check_complete"] and final["text_layer_checked_page_count"] == 13
    assert final["native_text_status"] == final_status
    assert final["automatic_ocr_status"] == ("COMPLETED" if native_count else "PARTIAL")
    assert bool(final["ocr_skip_reason"]) == (native_count == 0)
    assert len(final["manual_page_numbers"]) == (0 if native_count else 11)


def test_long_file_endpoint_seals_still_choose_last_page_last_geometric_stamp(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    _pdf(client, claim["id"], 13)
    calls = _cloud(monkeypatch)

    def recognize(action, image, config):
        calls.append(action)
        if action == "GeneralAccurateOCR":
            return {"TextDetections": [{"DetectedText": "周末广场舞，2026年10月24日", "Confidence": 100, "Polygon": materials._polygon([5, 5, 250, 30])}]}
        stamps = [("first-page-bottom", 50, 350)] if calls.count("SealOCR") == 1 else [("last-right", 160, 120), ("same-y-left", 30, 120), ("last-array-earlier", 200, 20)]
        return {"SealInfos": [{"SealBody": text, "Location": {"X": x, "Y": y, "Width": 45, "Height": 40}} for text, x, y in stamps]}

    monkeypatch.setattr(materials, "tencent_ocr", recognize)
    result = materials.run_ocr(claim["id"], _user())
    assert calls == ["GeneralAccurateOCR", "SealOCR", "GeneralAccurateOCR", "SealOCR"]
    seals = [item for item in result["suggested_crops"] if item.get("validity_kind") == "SEAL"]
    assert len(seals) == 1 and seals[0]["page_number"] == 13
    with db.read() as conn:
        assert db.one(conn, "recognition_regions", seals[0]["region_id"])["text"] == "last-right"
    assert result["file_results"][0]["seal_status"] == "FOUND"
