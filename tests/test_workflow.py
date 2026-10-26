"""Business acceptance tests against real SQLite transactions, without cloud calls."""
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, seed, workflow
from app.security import current_user


@pytest.fixture
def flow(tmp_path):
    db.configure("sqlite:///" + (tmp_path / "test.db").as_posix(), tmp_path / "assets")
    seed.initialize()
    app = FastAPI()
    app.include_router(workflow.router)
    selected = {}
    app.dependency_overrides[current_user] = lambda: selected["user"]
    client = TestClient(app)
    with db.read() as conn:
        users = {u["username"]: u for u in db.rows(conn, "users")}
        cats = {c["code"]: c["id"] for c in db.rows(conn, "score_categories")}
        year = db.rows(conn, "academic_years")[0]
        classroom = db.one(conn, "classes", name="软件2401")

    def login(username):
        selected["user"] = users[username]

    def draft(score="0.30", category="COLLECTIVE", title="测试活动", facts=None, activity_id=None):
        login("student")
        created = client.post("/api/applications", json={"year_id": year["id"], "category_id": cats[category]})
        assert created.status_code == 200, created.text
        application = created.json()
        with db.write() as conn:
            file = db.add(conn, "file_objects", uploaded_by=users["student"]["id"], original_name="证明.png",
                          mime_type="image/png", byte_size=1, sha256=db.uid().replace("-", "") * 2, storage_uri="test-only.png")
        response = client.put("/api/applications/" + application["id"], json={"title": title, "requested_score": score,
                              "payload_json": facts or {}, "activity_id": activity_id, "file_ids": [file["id"]]})
        assert response.status_code == 200, response.text
        return response.json()

    def submit(application):
        login("student")
        response = client.post(f"/api/applications/{application['id']}/submit", json={})
        assert response.status_code == 200, response.text
        return response.json()

    def review(application, username="reviewer1", action="APPROVE", **extra):
        login(username)
        response = client.post(f"/api/applications/{application['id']}/review", json={"action": action, **extra})
        assert response.status_code == 200, response.text
        return response.json()

    def activity(decision="VALID", category="RESEARCH", **scope):
        login("counselor")
        response = client.post("/api/activities", json={"canonical_name": "测试竞赛" + db.uid()[:6], "aliases": [],
                              "category_id": cats[category], "decision": decision, **scope})
        assert response.status_code == 200, response.text
        return response.json()

    yield {"client": client, "login": login, "users": users, "cats": cats, "year": year,
           "class": classroom, "draft": draft, "submit": submit, "review": review, "activity": activity}
    client.close()


def test_rounding_manual_marker_and_four_category_direct_approval(flow):
    application = flow["draft"]("0.375")
    assert application["requested_score"] == "0.38"
    assert application["student_modified_score"] is True
    client = flow["client"]
    client.put(f"/api/applications/{application['id']}", json={"requested_score": "0.00"})
    reverted = client.put(f"/api/applications/{application['id']}", json={"requested_score": "0.38"}).json()
    assert reverted["student_modified_score"] is True
    application = flow["submit"](application)
    approved = flow["review"](application)
    assert approved["state"] == "APPROVED"
    flow["login"]("monitor")
    scores = client.get("/api/scores", params={"class_id": flow["class"]["id"], "year_id": flow["year"]["id"]}).json()
    mine = next(m for m in scores["members"] if m["user_id"] == flow["users"]["student"]["id"])
    assert mine["total"] == "70.38"
    flow["login"]("student")
    assert client.put(f"/api/applications/{application['id']}", json={"title": "不能覆盖审核内容"}).status_code == 409


def test_unknown_qualification_routes_to_counselor_and_personal_rejection_is_separate(flow):
    application = flow["submit"](flow["draft"]("2", "RESEARCH"))
    after_peer = flow["review"](application)
    assert after_peer["state"] == "PENDING_COUNSELOR"
    rejected = flow["review"](application, "counselor", "REJECT", reason="MISSING_PARTICIPATION",
                              activity_decision="VALID", comment="比赛有效，但个人参与证明不足")
    assert rejected["state"] == "REJECTED"
    with db.read() as conn:
        versions = db.rows(conn, "activity_eligibility_versions")
        assert len(versions) == 1 and versions[0]["decision"] == "VALID"
        revision = db.one(conn, "application_revisions", rejected["round"]["revision_id"])
        assert revision["activity_id"] is None  # submitted content remained immutable


def test_known_invalid_only_blocks_formal_submission(flow):
    activity = flow["activity"]("INVALID")
    application = flow["draft"]("2", "RESEARCH", activity_id=activity["id"])
    result = flow["client"].post(f"/api/applications/{application['id']}/submit", json={})
    assert result.status_code == 400
    assert "无加分资格" in result.json()["detail"]
    with db.read() as conn:
        assert not db.rows(conn, "submission_rounds", application_id=application["id"])


def test_self_review_cross_class_and_admin_are_forbidden(flow):
    application = flow["submit"](flow["draft"]())
    flow["login"]("student")
    client = flow["client"]
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE"}).status_code == 403
    flow["login"]("class2_reviewer1")
    assert client.get(f"/api/applications/{application['id']}").status_code == 403
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE"}).status_code == 403
    flow["login"]("admin")
    assert client.get(f"/api/applications/{application['id']}").status_code == 403


def test_publication_gate_submission_lock_and_live_revocation(flow):
    application = flow["submit"](flow["draft"]())
    client = flow["client"]
    body = {"class_id": flow["class"]["id"], "year_id": flow["year"]["id"], "is_open": True}
    flow["login"]("counselor")
    assert client.put("/api/publicity", json=body).status_code == 409
    flow["review"](application)
    flow["login"]("counselor")
    assert client.put("/api/publicity", json=body).status_code == 200
    draft = flow["draft"]("0.60", title="公示期间可以保存")
    assert client.post(f"/api/applications/{draft['id']}/submit", json={}).status_code == 409
    flow["login"]("student2")
    result = client.get("/api/publicity", params={"class_id": body["class_id"], "year_id": body["year_id"]})
    assert result.status_code == 200
    assert "student_number" not in result.text
    assert len(result.json()["members"]) == 5
    flow["review"](application, "counselor", "REJECT", reason="WRONG_SCORE")
    flow["login"]("student2")
    assert all(m["total"] == "70.00" for m in client.get("/api/publicity", params={"class_id": body["class_id"], "year_id": body["year_id"]}).json()["members"])


def test_same_competition_new_item_does_not_reject_an_existing_approval(flow):
    activity = flow["activity"]()
    facts = {"edition_key": "2026", "track_key": "软件赛道", "group_key": "本科组", "work_key": "作品甲", "award_level": "PROVINCIAL"}
    old = flow["review"](flow["submit"](flow["draft"]("2", "RESEARCH", "省赛原件", facts, activity["id"])))
    new = flow["submit"](flow["draft"]("4", "RESEARCH", "国赛原件", {**facts, "award_level": "NATIONAL"}, activity["id"]))
    client = flow["client"]
    before = client.get(f"/api/applications/{old['id']}").json()
    assert before["state"] == "APPROVED" and before["revision_id"] == old["revision_id"]
    assert client.post(f"/api/applications/{new['id']}/withdraw", json={}).status_code == 200
    after = client.get(f"/api/applications/{old['id']}").json()
    assert after["state"] == "APPROVED" and after["lock_version"] == before["lock_version"]
    with db.read() as conn:
        assert not db.rows(conn, 'award_supersessions')
        assert not db.rows(conn, 'competition_projects')


def test_same_competition_lower_equal_and_higher_scores_can_all_be_submitted(flow):
    activity = flow["activity"]()
    facts = {"edition_key": "2026", "track_key": "组A", "award_level": "NATIONAL"}
    applications = [flow["submit"](flow["draft"](score, "RESEARCH", facts=facts, activity_id=activity["id"]))
                    for score in ("4", "3", "4", "5")]
    for application in applications:
        detail = flow["client"].get(f"/api/applications/{application['id']}").json()
        assert detail['state'] == 'PENDING_PEER'
        assert detail['round']['competition_group_key'] is None


def test_rejecting_or_withdrawing_one_competition_item_leaves_others_alone(flow):
    activity = flow["activity"]()
    facts = {"edition_key": "2026", "track_key": "组A"}
    a = flow["review"](flow["submit"](flow["draft"]("1", "RESEARCH", facts=facts, activity_id=activity["id"])))
    b = flow["submit"](flow["draft"]("2", "RESEARCH", facts=facts, activity_id=activity["id"]))
    c = flow["submit"](flow["draft"]("3", "RESEARCH", facts=facts, activity_id=activity["id"]))
    client = flow["client"]
    assert client.post(f"/api/applications/{c['id']}/withdraw", json={}).status_code == 200
    assert client.get(f"/api/applications/{b['id']}").json()['state'] == 'PENDING_PEER'
    flow["review"](b, action="REJECT", reason="DUPLICATE_COMPETITION")
    assert client.get(f"/api/applications/{a['id']}").json()['state'] == 'APPROVED'
    assert client.get(f"/api/applications/{c['id']}").json()['state'] == 'WITHDRAWN'


def test_caps_post_max_volunteer_annual_cap_and_all_class_members(flow):
    for score, category, facts in [("2", "COLLECTIVE", {}), ("2", "COLLECTIVE", {}),
                                   ("2", "POST", {}), ("3", "POST", {}),
                                   ("0.60", "SERVICE", {"subtype": "VOLUNTEER", "volunteer_hours": 16}),
                                   ("0.60", "SERVICE", {"subtype": "VOLUNTEER", "volunteer_hours": 16})]:
        reviewed = flow["review"](flow["submit"](flow["draft"](score, category, facts=facts)))
        if reviewed['state'] == 'PENDING_COUNSELOR':
            flow['review'](reviewed, 'counselor')
    flow["login"]("monitor")
    result = flow["client"].get("/api/scores", params={"class_id": flow["class"]["id"], "year_id": flow["year"]["id"]}).json()
    mine = next(m for m in result["members"] if m["user_id"] == flow["users"]["student"]["id"])
    cats = {c["code"]: c for c in mine["categories"]}
    assert cats["COLLECTIVE"]["score"] == "3.00"
    assert cats["POST"]["score"] == "3.00"
    assert cats["SERVICE"]["score"] == "1.00"
    assert mine["total"] == "77.00" and len(result["members"]) == 5
    assert any(m["total"] == "70.00" for m in result["members"])


def test_rejection_reason_required_and_stale_review_conflicts(flow):
    application = flow["submit"](flow["draft"]())
    flow["login"]("reviewer1")
    client = flow["client"]
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "REJECT"}).status_code == 400
    approved = client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE", "expected_version": application["lock_version"], "idempotency_key": "test-one"})
    assert approved.status_code == 200
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE", "idempotency_key": "test-one"}).status_code == 200
    flow["login"]("reviewer2")
    assert client.post(f"/api/applications/{application['id']}/review", json={"action": "APPROVE", "expected_version": application["lock_version"]}).status_code == 409


def test_scope_specificity_and_conflicts_require_human_decision(flow):
    activity = flow["activity"]("VALID")
    flow["login"]("counselor")
    client = flow["client"]
    assert client.put(f"/api/activities/{activity['id']}", json={"category_id": flow["cats"]["RESEARCH"], "decision": "INVALID", "edition_key": "2026"}).status_code == 200
    with db.read() as conn:
        assert workflow.activity_eligibility(conn, activity["id"], flow["cats"]["RESEARCH"], flow["year"]["id"], {"edition_key": "2026"})["decision"] == "UNKNOWN"
        assert workflow.activity_eligibility(conn, activity["id"], flow["cats"]["RESEARCH"], flow["year"]["id"], {"edition_key": "2025"})["decision"] == "UNKNOWN"
    client.put(f"/api/activities/{activity['id']}", json={"category_id": flow["cats"]["RESEARCH"], "decision": "VALID", "track_key": "组A"})
    with db.read() as conn:
        result = workflow.activity_eligibility(conn, activity["id"], flow["cats"]["RESEARCH"], flow["year"]["id"], {"edition_key": "2026", "track_key": "组A"})
        assert result["decision"] == "UNKNOWN" and result["conflict"] is True
