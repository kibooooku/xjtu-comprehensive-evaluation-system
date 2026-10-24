"""Real application integration: authentication, CSRF and business object scopes."""
import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import create_app
from app.seed import DEMO_PASSWORD


@pytest.fixture
def http_app(tmp_path):
    app = create_app("sqlite:///" + (tmp_path / "real.db").as_posix(), tmp_path / "assets")
    with TestClient(app) as client:
        def login(username):
            client.cookies.clear()
            client.headers.pop("X-CSRF-Token", None)
            response = client.post("/api/login", json={"username": username, "password": DEMO_PASSWORD})
            assert response.status_code == 200, response.text
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            return response.json()

        login("student")
        bootstrap = client.get("/api/bootstrap").json()
        year = bootstrap["years"][0]
        classroom = next(c for c in bootstrap["classes"] if c["name"] == "软件2401")
        category = next(c for c in bootstrap["categories"] if c["code"] == "COLLECTIVE")

        def draft():
            login("student")
            application = client.post("/api/applications", json={"year_id": year["id"], "category_id": category["id"]}).json()
            response = client.put(f"/api/applications/{application['id']}", json={"title": "第一次提交的内容", "requested_score": "0.30"})
            assert response.status_code == 200, response.text
            image = io.BytesIO()
            Image.new("RGB", (240, 120), "white").save(image, format="PNG")
            upload = client.post(f"/api/applications/{application['id']}/files", files={"file": ("证明.png", image.getvalue(), "image/png")})
            assert upload.status_code == 200, upload.text
            return client.get(f"/api/applications/{application['id']}").json()

        yield client, login, draft, year, classroom, category


def test_auth_csrf_origin_and_public_fields(http_app):
    client, login, draft, year, classroom, category = http_app
    session = client.get("/api/session").json()
    assert session["user"]["roles"][0]["code"] == "STUDENT"
    assert "password_hash" not in str(session)
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/applications", json={"year_id": year["id"], "category_id": category["id"]}).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/applications", headers={"Origin": "https://evil.invalid"}, json={"year_id": year["id"], "category_id": category["id"]}).status_code == 403
    assert client.get("/api/scores", params={"class_id": classroom["id"], "year_id": year["id"]}).status_code == 403
    assert client.get("/api/applications/not-an-id").status_code == 404
    assert client.post("/api/applications", json={}).status_code == 400
    application = draft()
    assert "storage_uri" not in str(application) and "private_asset_uri" not in str(application)
    login("student2")
    assert client.get(f"/api/applications/{application['id']}").status_code == 403
    assert client.get("/api/applications", params={"scope": "class"}).json() == []
    login("monitor")
    assert client.get(f"/api/applications/{application['id']}").status_code == 200
    login("admin")
    assert client.get(f"/api/applications/{application['id']}").status_code == 403
    assert client.get(application["files"][0]["url"]).status_code == 403


def test_readonly_historical_revision_and_correct_frozen_files(http_app):
    client, login, draft, _, _, _ = http_app
    application = draft()
    original_revision = application["revision"]["id"]
    assert client.post(f"/api/applications/{application['id']}/submit", json={}).status_code == 200
    login("reviewer1")
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "REJECT", "reason": "WRONG_TITLE"}).status_code == 200
    login("student")
    edited = client.put(f"/api/applications/{application['id']}", json={"title": "已修正的新工作版本", "requested_score": "0.60"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"]["id"] != original_revision
    historical = client.get(f"/api/applications/{application['id']}", params={"revision_id": original_revision})
    assert historical.status_code == 200
    history = historical.json()
    assert history["revision"]["title"] == "第一次提交的内容"
    assert history["requested_score"] == "0.30" and history["files"]
    assert history["can_edit"] is False and history["can_review"] is False and history["viewing_history"] is True
    assert len(history["versions"]) == 2
    assert client.get(f"/api/applications/{application['id']}", params={"revision_id": "unknown"}).status_code == 404
    login("student2")
    assert client.get(f"/api/applications/{application['id']}", params={"revision_id": original_revision}).status_code == 403


def test_setup_or_understaffed_class_cannot_submit_but_can_save(http_app):
    client, login, draft, _, classroom, _ = http_app
    application = draft()
    with db.write() as conn:
        db.change(conn, "classes", classroom["id"], status="SETUP")
    assert client.put(f"/api/applications/{application['id']}", json={"title": "配置期间的草稿"}).status_code == 200
    assert client.post(f"/api/applications/{application['id']}/submit", json={}).status_code == 409
    with db.write() as conn:
        db.change(conn, "classes", classroom["id"], status="ACTIVE")
        user = db.one(conn, "users", username="reviewer2")
        db.change(conn, "users", user["id"], status="DISABLED")
    assert client.post(f"/api/applications/{application['id']}/submit", json={}).status_code == 409
    with db.write() as conn:
        db.change(conn, "users", user["id"], status="ACTIVE")
    assert client.post(f"/api/applications/{application['id']}/submit", json={}).status_code == 200


def _other_client(app, username):
    client = TestClient(app)
    login = client.post("/api/login", json={"username": username, "password": DEMO_PASSWORD})
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return client


def test_two_reviewers_cannot_both_decide_same_version(http_app):
    client, _, draft, _, _, _ = http_app
    application = draft()
    submitted = client.post(f"/api/applications/{application['id']}/submit", json={}).json()
    first, second = _other_client(client.app, "reviewer1"), _other_client(client.app, "reviewer2")
    try:
        def decide(actor):
            return actor.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE", "expected_version": submitted["lock_version"]}).status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(decide, (first, second)))
        assert sorted(results) == [200, 409]
        with db.read() as conn:
            assert len(db.rows(conn, "review_decisions", round_id=submitted["round_id"])) == 1
    finally:
        first.close()
        second.close()


def test_publication_and_formal_submission_are_one_serial_boundary(http_app):
    client, _, draft, year, classroom, _ = http_app
    application = draft()
    counselor = _other_client(client.app, "counselor")
    try:
        def publish():
            return counselor.put("/api/publicity", json={"class_id": classroom["id"], "year_id": year["id"], "is_open": True}).status_code
        def submit():
            return client.post(f"/api/applications/{application['id']}/submit", json={}).status_code
        with ThreadPoolExecutor(max_workers=2) as workers:
            p, s = workers.submit(publish), workers.submit(submit)
            assert sorted([p.result(), s.result()]) == [200, 409]
    finally:
        counselor.close()
