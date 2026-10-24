"""Material boundary tests: no real cloud calls and no personal source fixtures."""
import io
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image

from app import db, materials
from app.integrations import ProviderError, tencent_headers
from app.main import create_app


@pytest.fixture
def material_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_ANALYSIS_ENABLED", "false")
    application = create_app(storage_dir=tmp_path / "storage", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    clients = []

    def login(username):
        client = TestClient(application)
        response = client.post("/api/login", json={"username": username, "password": "Demo@2026"})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        clients.append(client)
        return client

    yield application, login
    for client in clients:
        client.close()


def png(width=300, height=200, color="white"):
    image = Image.new("RGB", (width, height), color)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def create_claim(client):
    bootstrap = client.get("/api/bootstrap").json()
    year = bootstrap["years"][0]["id"]
    category = next(c["id"] for c in bootstrap["categories"] if c["code"] == "COLLECTIVE")
    response = client.post("/api/applications", json={"year_id": year, "category_id": category})
    assert response.status_code == 200, response.text
    claim = response.json()
    return claim, year, bootstrap["classes"][0]["id"]


def upload(client, claim_id):
    response = client.post(f"/api/applications/{claim_id}/files", files={"file": ("proof.png", png(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()


def test_image_signatures_coordinate_bounds_and_tc3():
    with pytest.raises(HTTPException):
        materials.render_upload(b"<script>not an image</script>", "fake.png")
    with pytest.raises(HTTPException):
        materials.render_upload(png(), "fake.pdf")
    with pytest.raises(HTTPException):
        materials.checked_box([0, 0, float("nan"), 4], 100, 100)
    with pytest.raises(HTTPException):
        materials.checked_box([99, 0, 10, 10], 100, 100)
    assert materials.checked_box([1.2, 2.1, 3.3, 4.1], 100, 100) == [1, 2, 4, 5]
    config = {"secret_id": "EXAMPLE-ID", "secret_key": "EXAMPLE-KEY", "region": "ap-shanghai"}
    a = tencent_headers("GeneralAccurateOCR", b'{"x":1}', config, 1551113065)
    b = tencent_headers("GeneralAccurateOCR", b'{"x":2}', config, 1551113065)
    assert "/2019-02-25/ocr/tc3_request" in a["Authorization"]
    assert a["Authorization"] != b["Authorization"]
    assert a["X-TC-Action"] == "GeneralAccurateOCR"
    assert a["X-TC-Version"] == "2018-11-19"


def test_upload_private_access_is_scoped_and_admin_has_no_materials(material_app):
    _, login = material_app
    student = login("student")
    claim, _, _ = create_claim(student)
    record = upload(student, claim["id"])
    private_url = record["url"]
    assert student.get(private_url).status_code == 200
    assert login("student2").get(private_url).status_code == 403
    assert login("reviewer1").get(private_url).status_code == 200
    assert login("class2_reviewer1").get(private_url).status_code == 403
    assert login("admin").get(private_url).status_code == 403
    assert login("counselor").get(record["pages"][0]["url"]).status_code == 200
    response = student.post(f"/api/applications/{claim['id']}/files", files={"file": ("bad.png", b"not an image", "image/png")})
    assert response.status_code == 400


def test_public_crop_is_blocked_without_ocr_and_numeric_rows_are_redacted(material_app):
    _, login = material_app
    student = login("student")
    claim, year, class_id = create_claim(student)
    record = upload(student, claim["id"])
    page_id = record["pages"][0]["id"]
    body = {"page_id": page_id, "evidence_type": "PARTICIPATION", "box": [0, 0, 200, 120]}
    blocked = student.post(f"/api/applications/{claim['id']}/crops", json=body)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["public_asset_status"] == "BLOCKED"
    assert blocked.json()["public_url"] is None
    with db.write() as conn:
        run = db.add(conn, "recognition_runs", page_id=page_id, provider="TENCENT", api_name="GeneralAccurateOCR", parameters_hash="test-only", status="SUCCEEDED", result_json={}, started_at=db.now(), finished_at=db.now())
        db.add(conn, "recognition_regions", run_id=run["id"], region_no=0, kind="TEXT", text="学号 DEMO202401004", confidence=Decimal("1"), polygon_json=[[20, 20], [160, 20], [160, 45], [20, 45]], extra_json={})
    ready = student.post(f"/api/applications/{claim['id']}/crops", json=body)
    assert ready.status_code == 200, ready.text
    assert ready.json()["public_asset_status"] == "READY"
    with db.read() as conn:
        crop = db.one(conn, "evidence_crops", ready.json()["id"])
        public_image = Image.open(materials.asset_path(crop["public_asset_uri"]))
        assert public_image.getpixel((40, 30)) == (37, 50, 68)
        assert public_image.getpixel((180, 90)) == (255, 255, 255)
    # Even a fully prepared image has no public access before a completed review and open publicity.
    assert login("student2").get(ready.json()["public_url"]).status_code == 403
    updated = student.put(f"/api/applications/{claim['id']}", json={"title": "测试集体活动", "requested_score": "0.30", "payload_json": {}})
    assert updated.status_code == 200, updated.text
    submitted = student.post(f"/api/applications/{claim['id']}/submit", json={})
    assert submitted.status_code == 200, submitted.text
    reviewer = login("reviewer1")
    decision = reviewer.post(f"/api/applications/{claim['id']}/review", json={"action": "APPROVE"})
    assert decision.status_code == 200, decision.text
    counselor = login("counselor")
    opened = counselor.put("/api/publicity", json={"class_id": class_id, "year_id": year, "is_open": True})
    assert opened.status_code == 200, opened.text
    mate = login("student2")
    assert mate.get(ready.json()["public_url"]).status_code == 200
    assert mate.get(record["url"]).status_code == 403
    assert login("class2_reviewer1").get(ready.json()["public_url"]).status_code == 403


def test_crop_cannot_point_at_another_claim_file_and_late_analysis_is_rejected(material_app):
    _, login = material_app
    student = login("student")
    first, _, _ = create_claim(student)
    second, _, _ = create_claim(student)
    record = upload(student, first["id"])
    wrong = student.post(f"/api/applications/{second['id']}/crops", json={"page_id": record["pages"][0]["id"], "evidence_type": "VALIDITY", "box": [0, 0, 80, 80]})
    assert wrong.status_code == 400
    with db.write() as conn:
        app = db.one(conn, "applications", first["id"])
        revision = db.one(conn, "application_revisions", app["working_revision_id"])
        context = materials._app_context(conn, first["id"])
        analysis = db.add(conn, "ai_analyses", application_id=first["id"], input_revision_id=revision["id"], input_fingerprint=materials.revision_fingerprint(conn, revision), score_rule_version_id=context["class_year"]["score_rule_version_id"], model_name="test-no-network", prompt_version="test", status="SUCCEEDED", result_json={"suggestion": {"title": "old suggestion", "requested_score": "0.30", "payload_json": {}}})
    change_response = student.put(f"/api/applications/{first['id']}", json={"title": "学生已自行修订", "requested_score": "0.60", "payload_json": {}})
    assert change_response.status_code == 200
    applied = student.post(f"/api/applications/{first['id']}/apply-analysis", json={"analysis_id": analysis["id"]})
    assert applied.status_code == 409, applied.text
    assert student.get(f"/api/applications/{first['id']}").json()["revision"]["title"] == "学生已自行修订"


def test_ocr_budget_reserves_failures_and_cache_avoids_second_request(material_app, monkeypatch):
    _, login = material_app
    student = login("student")
    claim, _, _ = create_claim(student)
    record = upload(student, claim["id"])
    with db.read() as conn:
        page = db.one(conn, "file_pages", record["pages"][0]["id"])
    calls = []

    def fake_success(action, image, config):
        calls.append(action)
        return {"RequestId": "test-no-network", "TextDetections": []}

    monkeypatch.setattr(materials, "tencent_ocr", fake_success)
    config = {"general_limit": 1, "seal_limit": 1}
    first = materials._ocr_page(page, "GeneralAccurateOCR", config)
    second = materials._ocr_page(page, "GeneralAccurateOCR", config)
    assert first["id"] == second["id"]
    assert calls == ["GeneralAccurateOCR"]

    def fake_failure(action, image, config):
        calls.append(action)
        raise ProviderError("ResourceUnavailable.ResourcePackageRunOut", "已耗尽")

    monkeypatch.setattr(materials, "tencent_ocr", fake_failure)
    with pytest.raises(ProviderError):
        materials._ocr_page(page, "SealOCR", config)
    with pytest.raises(ProviderError, match="本月本地 OCR"):
        materials._ocr_page(page, "SealOCR", config)
    assert calls.count("SealOCR") == 1


def test_first_crop_delete_after_rejection_preserves_the_frozen_evidence(material_app):
    _, login = material_app
    student = login("student")
    claim, _, _ = create_claim(student)
    record = upload(student, claim["id"])
    crop_response = student.post(f"/api/applications/{claim['id']}/crops", json={"page_id": record["pages"][0]["id"], "evidence_type": "VALIDITY", "box": [0, 0, 100, 100]})
    assert crop_response.status_code == 200
    crop_id = crop_response.json()["id"]
    saved = student.put(f"/api/applications/{claim['id']}", json={"title": "待修改证明", "requested_score": "0.30", "payload_json": {}})
    assert saved.status_code == 200
    assert student.post(f"/api/applications/{claim['id']}/submit", json={}).status_code == 200
    reviewer = login("reviewer1")
    rejection = reviewer.post(f"/api/applications/{claim['id']}/review", json={"action": "REJECT", "reason": "MISSING_VALIDITY"})
    assert rejection.status_code == 200, rejection.text
    response = student.delete(f"/api/applications/{claim['id']}/crops/{crop_id}")
    assert response.status_code == 200, response.text
    detail = student.get(f"/api/applications/{claim['id']}").json()
    assert detail["crops"] == []
    with db.read() as conn:
        historical_crop = db.one(conn, "evidence_crops", crop_id)
        assert historical_crop is not None
        frozen_revision = db.one(conn, "application_revisions", historical_crop["revision_id"])
        assert frozen_revision["content_frozen_at"]


def test_successful_analysis_requires_explicit_apply_and_keeps_manual_override(material_app, monkeypatch):
    _, login = material_app
    student = login("student")
    claim, _, _ = create_claim(student)
    record = upload(student, claim["id"])
    page_id = record["pages"][0]["id"]
    initial_crop = student.post(f"/api/applications/{claim['id']}/crops", json={"page_id": page_id, "evidence_type": "PARTICIPATION", "box": [0, 0, 200, 100]})
    assert initial_crop.status_code == 200
    config = {"enabled": True, "secret_id": "test", "secret_key": "test", "region": "ap-shanghai", "deepseek_key": "test", "deepseek_model": "test-no-network", "general_limit": 10, "seal_limit": 10, "max_analysis_pages": 12}
    monkeypatch.setattr(materials, "configuration", lambda: config)

    def fake_ocr(action, image, config):
        if action == "SealOCR":
            return {"RequestId": "test-no-network-seal", "SealInfos": []}
        return {"RequestId": "test-no-network-text", "TextDetections": [{"DetectedText": "王欣然参加2026年两次集体活动，学号202401004", "Confidence": 100, "Polygon": [{"X": 15, "Y": 20}, {"X": 190, "Y": 20}, {"X": 190, "Y": 45}, {"X": 15, "Y": 45}]}]}

    monkeypatch.setattr(materials, "tencent_ocr", fake_ocr)
    monkeypatch.setattr(materials, "deepseek_suggestion", lambda context, config: {"title": "两次集体活动", "requested_score": "0.60", "activity_id": None, "payload_json": {}, "explanation": "每次0.3分，两次合并一个条目。"})
    result = student.post(f"/api/applications/{claim['id']}/analyze", json={})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "SUCCEEDED"
    assert result.json()["analysis"]["id"]
    before_apply = student.get(f"/api/applications/{claim['id']}").json()
    assert before_apply["revision"]["requested_score"] == "0.00"
    applied = student.post(f"/api/applications/{claim['id']}/apply-analysis", json={"analysis_id": result.json()["analysis_id"]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["revision"]["requested_score"] == "0.60"
    assert applied.json()["revision"]["student_modified_score"] is False
    manual = student.put(f"/api/applications/{claim['id']}", json={"title": "学生核对后修订", "requested_score": "0.90", "payload_json": {}})
    assert manual.status_code == 200, manual.text
    assert manual.json()["revision"]["student_modified_score"] is True
    reverted = student.put(f"/api/applications/{claim['id']}", json={"title": "学生核对后修订", "requested_score": "0.60", "payload_json": {}})
    assert reverted.status_code == 200
    assert reverted.json()["revision"]["student_modified_score"] is True


def test_export_has_original_fifteen_columns_all_students_and_no_formulas(material_app):
    _, login = material_app
    monitor = login("monitor")
    bootstrap = monitor.get("/api/bootstrap").json()
    class_id, year_id = bootstrap["classes"][0]["id"], bootstrap["years"][0]["id"]
    response = monitor.get("/api/export", params={"class_id": class_id, "year_id": year_id})
    assert response.status_code == 200, response.text
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet.max_column == 15
    assert "E1:I1" in [str(r) for r in sheet.merged_cells.ranges]
    assert "J1:L1" in [str(r) for r in sheet.merged_cells.ranges]
    assert sheet.max_row == 7  # All five demo classmates, including no-claim students.
    assert all(sheet.cell(row, 5).value == 70 and sheet.cell(row, 9).value == 0 and sheet.cell(row, 14).value == 70 for row in range(3, 8))
    assert all(sheet.cell(row, 15).value is None for row in range(3, 8))
    assert login("student").get("/api/export", params={"class_id": class_id, "year_id": year_id}).status_code == 403
    assert materials.excel_text("=HYPERLINK(\"https://example.invalid\")").startswith("'=")
