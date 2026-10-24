"""Regression checks from the final organization / scope security review."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.migrations import active_year
from app.seed import DEMO_PASSWORD


@pytest.fixture
def audit_api(tmp_path):
    app = create_app("sqlite:///" + (tmp_path / "audit.db").as_posix(), tmp_path / "assets")
    with TestClient(app) as client:
        def login(name):
            client.cookies.clear()
            client.headers.pop("X-CSRF-Token", None)
            response = client.post("/api/login", json={"username": name, "password": DEMO_PASSWORD})
            assert response.status_code == 200
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield client, login


def test_organization_metadata_scope_cannot_expand_counselor_class_boundary(audit_api):
    client, login = audit_api
    with db.write() as conn:
        counselor = db.one(conn, "users", username="counselor")
        classroom = db.one(conn, "classes", name="软件2402")
        year = db.rows(conn, "academic_years")[0]
        for assignment in db.rows(conn, "counselor_classes", counselor_id=counselor["id"], class_id=classroom["id"]):
            db.change(conn, "counselor_classes", assignment["id"], status="ENDED", valid_to=db.now())
        role = db.one(conn, "roles", code="COUNSELOR")
        permission = db.one(conn, "permissions", code="organization.read")
        matrix = db.one(conn, "role_permissions", role_id=role["id"], permission_id=permission["id"])
        db.change(conn, "role_permissions", matrix["id"], data_scope="SYSTEM_METADATA", is_allowed=True)
    login("counselor")
    response = client.get(f"/api/classes/{classroom['id']}/members", params={"year_id": year["id"]})
    assert response.status_code == 403
    assert "student_number" not in response.text


def test_transferring_this_year_preserves_prior_year_approved_score(audit_api):
    client, login = audit_api
    with db.read() as conn:
        years = sorted(db.rows(conn, "academic_years"), key=lambda y: y["sort_order"])
        old_class = db.one(conn, "classes", name="软件2401")
        new_class = db.one(conn, "classes", name="软件2402")
        category = db.one(conn, "score_categories", code="COLLECTIVE")
        student = db.one(conn, "users", username="student2")
    login("student2")
    application = client.post("/api/applications", json={"year_id": years[0]["id"], "category_id": category["id"]}).json()
    with db.write() as conn:
        file = db.add(conn, "file_objects", uploaded_by=student["id"], original_name="历史证明.png", mime_type="image/png",
                      byte_size=1, sha256="a" * 64, storage_uri="unused-in-workflow-test.png")
    assert client.put(f"/api/applications/{application['id']}", json={"title": "历史学年的已批准活动", "requested_score": "0.30", "file_ids": [file["id"]]}).status_code == 200
    assert client.post(f"/api/applications/{application['id']}/submit", json={}).status_code == 200
    login("reviewer1")
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE"}).status_code == 200
    login("admin")
    transfer = client.put(f"/api/admin/users/{student['id']}", json={"class_id": new_class["id"], "academic_year_id": years[-1]["id"]})
    assert transfer.status_code == 200, transfer.text
    login("monitor")
    historical = client.get("/api/scores", params={"class_id": old_class["id"], "year_id": years[0]["id"]})
    assert historical.status_code == 200
    mine = next((m for m in historical.json()["members"] if m["user_id"] == student["id"]), None)
    assert mine is not None and mine["total"] == "70.30"
    current = client.get("/api/scores", params={"class_id": old_class["id"], "year_id": years[-1]["id"]}).json()
    assert all(m["user_id"] != student["id"] for m in current["members"])
    login("student2")
    old_detail = client.get(f"/api/applications/{application['id']}")
    assert old_detail.status_code == 200 and old_detail.json()["state"] == "APPROVED"
    assert client.get(f"/api/classes/{old_class['id']}/members", params={"year_id": years[0]["id"]}).status_code == 200
    assert client.get(f"/api/classes/{old_class['id']}/members", params={"year_id": years[-1]["id"]}).status_code == 403


def test_existing_application_year_cannot_silently_move_its_enrollment(audit_api):
    client, login = audit_api
    with db.read() as conn:
        year = active_year(conn)
        old_class = db.one(conn, "classes", name="软件2401")
        new_class = db.one(conn, "classes", name="软件2402")
        category = db.one(conn, "score_categories", code="COLLECTIVE")
        student = db.one(conn, "users", username="student2")
    login("student2")
    application = client.post("/api/applications", json={"year_id": year["id"], "category_id": category["id"]})
    assert application.status_code == 200
    login("admin")
    response = client.put(f"/api/admin/users/{student['id']}", json={"class_id": new_class["id"], "academic_year_id": year["id"]})
    assert response.status_code == 409
    with db.read() as conn:
        profile = db.one(conn, "student_profiles", user_id=student["id"])
        cy = db.one(conn, "class_years", class_id=old_class["id"], academic_year_id=year["id"])
        old_enrollment = db.one(conn, "enrollments", student_id=profile["id"], class_year_id=cy["id"])
        assert old_enrollment["status"] == "ACTIVE"
        assert db.one(conn, "applications", application.json()["id"])["enrollment_id"] == old_enrollment["id"]
