"""V2 category containers preserve item-level review and immutable history."""
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import db, seed, submissions, workflow
from app.migrations import active_year, initialize_v2
from app.security import current_user


@pytest.fixture
def v2(tmp_path, monkeypatch):
    db.configure("sqlite:///" + (tmp_path / "groups.db").as_posix(), tmp_path / "assets")
    seed.initialize()
    initialize_v2()
    app = FastAPI()
    app.include_router(workflow.router)
    app.include_router(submissions.router)
    selected = {}
    app.dependency_overrides[current_user] = lambda: selected["user"]
    client = TestClient(app)
    with db.read() as conn:
        users = {u["username"]: u for u in db.rows(conn, "users")}
        categories = {c["code"]: c["id"] for c in db.rows(conn, "score_categories")}
        year = active_year(conn)
        classroom = db.one(conn, "classes", name="软件2401")
    queued = []
    from app import jobs
    def enqueue(application_id, user, kind="AI", force=False):
        # The confirmation endpoint must have committed first. A separate read
        # proves workers can see the confirmed item before they are enqueued.
        with db.read() as conn:
            assert db.one(conn, "category_items", application_id=application_id)["confirmed_at"]
        queued.append((application_id, kind))
        return {"id": "test-" + application_id, "application_id": application_id, "kind": kind, "state": "QUEUED"}
    monkeypatch.setattr(jobs, "enqueue", enqueue)

    def login(name="student"):
        selected["user"] = users[name]
    login()

    def group(category="COLLECTIVE"):
        response = client.post("/api/submissions", json={"category_id": categories[category]})
        assert response.status_code == 200, response.text
        return response.json()

    def item(parent, title="具体活动", score="0.30", staged=True, content=None, category=None, facts=None, activity_id=None):
        login()
        response = client.post(f"/api/submissions/{parent['id']}/items", json={"staged": staged})
        assert response.status_code == 200, response.text
        application = response.json()
        with db.write() as conn:
            digest = hashlib.sha256((content or db.uid()).encode()).hexdigest()
            file = db.add(conn, "file_objects", uploaded_by=users["student"]["id"], original_name=title + ".png",
                          mime_type="image/png", byte_size=1, sha256=digest, storage_uri="only-a-logical-fixture.png")
        response = client.put(f"/api/applications/{application['id']}", json={"title": title, "requested_score": score,
                              "file_ids": [file["id"]], "payload_json": facts or {}, "activity_id": activity_id})
        assert response.status_code == 200, response.text
        return response.json()

    def confirm(parent, children):
        login()
        response = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={"item_ids": [i["id"] for i in children]})
        assert response.status_code == 200, response.text
        return response.json()

    def submit(parent, children):
        login()
        response = client.post(f"/api/submissions/{parent['id']}/submit", json={"item_ids": [i["id"] for i in children]})
        assert response.status_code == 200, response.text
        return response.json()

    def review(child, action="APPROVE", user="reviewer1", **extra):
        login(user)
        response = client.post(f"/api/applications/{child['id']}/review", json={"action": action, **extra})
        assert response.status_code == 200, response.text
        return response.json()

    yield {"client": client, "login": login, "group": group, "item": item, "confirm": confirm,
           "submit": submit, "review": review, "queued": queued, "categories": categories,
           "users": users, "year": year, "class": classroom}
    client.close()


def test_unique_category_parent_and_staging_is_invisible_until_confirmation(v2):
    parent = v2["group"]()
    assert v2["group"]()["id"] == parent["id"]
    first, second = v2["item"](parent), v2["item"](parent, title="第二次活动")
    client = v2["client"]
    assert client.get("/api/submissions").json() == []
    assert client.get("/api/applications").json() == []
    assert client.get("/api/dashboard").json()["stats"]["applications"] == 0
    assert v2["queued"] == []
    assert client.post(f"/api/applications/{first['id']}/submit", json={}).status_code == 409
    confirmed = v2["confirm"](parent, [first, second])
    assert confirmed["submission"]["item_count"] == 2
    assert len(v2["queued"]) == 2 and all(kind == "OCR" for _, kind in v2["queued"])
    assert len(client.get("/api/submissions").json()) == 1
    assert len(client.get("/api/applications").json()) == 2
    for code in v2["categories"]:
        v2["group"](code)
    with db.read() as conn:
        assert len(db.rows(conn, "category_submissions")) == 7


def test_append_after_approval_and_partial_submit_never_resubmits_approved_item(v2):
    parent = v2["group"]()
    old = v2["item"](parent)
    v2["confirm"](parent, [old])
    assert v2["submit"](parent, [old])["submitted"] == 1
    approved = v2["review"](old)
    frozen_id = approved["revision"]["id"]
    new = v2["item"](parent, title="之后补交的独立活动", score="0.60")
    v2["confirm"](parent, [new])
    results = v2["submit"](parent, [old, new])["results"]
    assert [r["ok"] for r in results] == [False, True]
    assert results[0]["status_code"] == 409
    client = v2["client"]
    assert client.put(f"/api/applications/{old['id']}", json={"title": "不许改变"}).status_code == 409
    v2["login"]("reviewer1")
    groups = client.get("/api/submissions", params={"scope": "review"}).json()
    assert len(groups) == 1 and len(groups[0]["items"]) == 2
    assert {i["state"] for i in groups[0]["items"]} == {"APPROVED", "PENDING_PEER"}
    assert groups[0]["approved_total"] == "0.30"
    with db.read() as conn:
        assert len(db.rows(conn, "submission_rounds", application_id=old["id"])) == 1
        assert db.one(conn, "application_revisions", frozen_id)["title"] == "具体活动"


def test_same_parent_exact_hash_duplicate_is_blocked_cross_category_is_allowed(v2):
    parent = v2["group"]()
    old = v2["item"](parent, content="identical bytes")
    v2["confirm"](parent, [old])
    client = v2["client"]
    other = client.post(f"/api/submissions/{parent['id']}/items", json={}).json()
    with db.write() as conn:
        renamed = db.add(conn, "file_objects", uploaded_by=v2["users"]["student"]["id"], original_name="已改名.png",
                         mime_type="image/png", byte_size=1, sha256=hashlib.sha256(b"identical bytes").hexdigest(), storage_uri="logical.png")
    response = client.put(f"/api/applications/{other['id']}", json={"file_ids": [renamed["id"]]})
    assert response.status_code == 409 and response.json()["detail"]["application_id"] == old["id"]
    other_category = v2["group"]("ARTS")
    child = client.post(f"/api/submissions/{other_category['id']}/items", json={}).json()
    assert client.put(f"/api/applications/{child['id']}", json={"file_ids": [renamed["id"]]}).status_code == 200
    assert client.delete(f"/api/submissions/{parent['id']}/items/{old['id']}").status_code == 200
    assert client.put(f"/api/applications/{other['id']}", json={"file_ids": [renamed["id"]]}).status_code == 200
    historical = client.get(f"/api/submissions/{parent['id']}", params={"include_deleted": True, "include_staged": True}).json()
    deleted = next(i for i in historical["items"] if i["id"] == old["id"])
    assert deleted["deleted_at"] and deleted["files"] and deleted["can_edit"] is False


def test_old_year_is_readonly_but_original_pending_review_can_finish(v2):
    parent = v2["group"]()
    pending = v2["item"](parent)
    v2["confirm"](parent, [pending])
    v2["submit"](parent, [pending])
    draft = v2["item"](parent, title="旧学年未提交草稿")
    with db.write() as conn:
        other_year = next(y for y in db.rows(conn, "academic_years") if y["id"] != v2["year"]["id"])
        db.change(conn, "system_settings", "active_academic_year", value_json={"year_id": other_year["id"]})
    client = v2["client"]
    assert client.post("/api/applications", json={"year_id": v2["year"]["id"], "category_id": v2["categories"]["COLLECTIVE"]}).status_code == 409
    assert client.put(f"/api/applications/{draft['id']}", json={"title": "不能补交旧年"}).status_code == 409
    assert client.post(f"/api/applications/{pending['id']}/withdraw", json={}).status_code == 409
    assert client.delete(f"/api/submissions/{parent['id']}/items/{draft['id']}").status_code == 409
    assert v2["review"](pending)["state"] == "APPROVED"
    v2["login"]()
    detail = client.get(f"/api/applications/{draft['id']}").json()
    assert detail["can_edit"] is False and detail["is_current_year"] is False
    assert client.post("/api/submissions", json={"category_id": v2["categories"]["COLLECTIVE"]}).json()["id"] != parent["id"]


def test_batch_confirmation_is_atomic_and_submission_is_per_item(v2):
    parent = v2["group"]()
    good = v2["item"](parent)
    client = v2["client"]
    no_file = client.post(f"/api/submissions/{parent['id']}/items", json={}).json()
    assert client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={"item_ids": [good["id"], no_file["id"]]}).status_code == 400
    assert v2["queued"] == [] and client.get("/api/submissions").json() == []
    v2["confirm"](parent, [good])
    results = v2["submit"](parent, [good, no_file])["results"]
    assert results[0]["ok"] and not results[1]["ok"]
    assert results[1]["status_code"] == 409


def test_new_competition_item_does_not_make_approved_item_deletable(v2):
    v2["login"]("counselor")
    client = v2["client"]
    activity = client.post("/api/activities", json={"canonical_name": "V2具体竞赛", "category_id": v2["categories"]["RESEARCH"], "decision": "VALID"}).json()
    v2["login"]()
    parent = v2["group"]("RESEARCH")
    facts = {"edition_key": "2026", "track_key": "算法", "work_key": "项目A"}
    old = v2["item"](parent, title="V2具体竞赛省赛一等奖", score="2", facts=facts, activity_id=activity["id"])
    v2["confirm"](parent, [old]);v2["submit"](parent, [old])
    assert v2["review"](old)["state"] == "APPROVED"
    new = v2["item"](parent, title="V2具体竞赛国赛一等奖", score="4", facts=facts, activity_id=activity["id"])
    v2["confirm"](parent, [new]);assert v2["submit"](parent, [new])["submitted"] == 1
    assert client.get(f"/api/applications/{old['id']}").json()["state"] == "APPROVED"
    assert client.delete(f"/api/submissions/{parent['id']}/items/{old['id']}").status_code == 409
    assert client.post(f"/api/applications/{new['id']}/withdraw", json={}).status_code == 200
    with db.read() as conn:
        old_app = db.one(conn, "applications", old["id"])
        assert not old_app["archived_at"]
        assert db.one(conn, "submission_rounds", old_app["current_round_id"])["state"] == "APPROVED"
        assert not db.rows(conn, "award_supersessions")


def test_partial_group_cannot_bypass_publication_gate_or_lock(v2):
    parent = v2["group"]()
    approved, pending = v2["item"](parent), v2["item"](parent, title="另一待审项")
    v2["confirm"](parent, [approved, pending])
    v2["submit"](parent, [approved, pending])
    v2["review"](approved)
    v2["login"]("counselor")
    publication = {"class_id": v2["class"]["id"], "year_id": v2["year"]["id"], "is_open": True}
    client = v2["client"]
    assert client.put("/api/publicity", json=publication).status_code == 409
    v2["review"](pending, action="REJECT", reason="MISSING_PROOF")
    v2["login"]("counselor")
    assert client.put("/api/publicity", json=publication).status_code == 200
    v2["login"]()
    new = v2["item"](parent, title="公示中的补充草稿")
    v2["confirm"](parent, [new])
    assert v2["submit"](parent, [new])["results"][0]["status_code"] == 409


def test_abandoned_staged_upload_does_not_reserve_hash_and_batch_duplicates_are_atomic(v2):
    parent = v2["group"]()
    abandoned = v2["item"](parent, content="same proof after interrupted upload")
    retry = v2["item"](parent, title="刷新后重试", content="same proof after interrupted upload")
    client = v2["client"]
    duplicate_batch = client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={"item_ids": [abandoned["id"], retry["id"]]})
    assert duplicate_batch.status_code == 409
    assert client.get("/api/submissions").json() == [] and v2["queued"] == []
    assert v2["confirm"](parent, [retry])["submission"]["item_count"] == 1
    assert client.post(f"/api/submissions/{parent['id']}/confirm-upload", json={"item_ids": [abandoned["id"]]}).status_code == 409
    assert len(v2["queued"]) == 1


def test_soft_deleted_only_group_remains_in_explicit_history_without_write_or_foreign_access(v2):
    parent = v2["group"]()
    item = v2["item"](parent)
    v2["confirm"](parent, [item])
    client = v2["client"]
    assert client.delete(f"/api/submissions/{parent['id']}/items/{item['id']}").status_code == 200
    assert client.get("/api/submissions").json() == []
    history = client.get("/api/submissions", params={"include_deleted": True}).json()
    assert len(history) == 1 and history[0]["items"][0]["deleted_at"]
    assert history[0]["items"][0]["file_count"] == 1
    detail = client.get(f"/api/applications/{item['id']}", params={"include_deleted": True}).json()
    assert detail["viewing_history"] and not detail["can_edit"] and not detail["can_review"]
    v2["login"]("student2")
    assert client.get(f"/api/applications/{item['id']}", params={"include_deleted": True}).status_code == 403


def test_internal_manual_markers_survive_form_payload_replacement(v2):
    parent = v2["group"]()
    item = v2["item"](parent)
    with db.write() as conn:
        revision = db.one(conn, "application_revisions", item["revision"]["id"])
        db.change(conn, "application_revisions", revision["id"], payload_json={**revision["payload_json"], "evidence_manually_edited": True})
    client = v2["client"]
    response = client.put(f"/api/applications/{item['id']}", json={"title": "", "requested_score": "0", "payload_json": {}})
    assert response.status_code == 200
    facts = response.json()["revision"]["payload_json"]
    assert facts["evidence_manually_edited"] is True and facts["content_manually_edited"] is True


def test_legacy_group_migration_preserves_round_revision_score_and_flags_duplicate_names(v2):
    parent = v2["group"]()
    first = v2["item"](parent, title="校园春季夜跑活动")
    second = v2["item"](parent, title="校园春季夜跑活动")
    v2["confirm"](parent, [first, second])
    v2["submit"](parent, [first])
    approved = v2["review"](first)
    with db.write() as conn:
        original_round = db.one(conn, "submission_rounds", approved["round_id"])
        original_revision = db.one(conn, "application_revisions", approved["revision_id"])
        conn.execute(delete(db.tables["category_items"]).where(db.tables["category_items"].c.submission_id == parent["id"]))
        conn.execute(delete(db.tables["category_submissions"]).where(db.tables["category_submissions"].c.id == parent["id"]))
    initialize_v2()
    initialize_v2()  # migration is idempotent, not a second grouping/import
    with db.read() as conn:
        assert db.one(conn, "submission_rounds", approved["round_id"]) == original_round
        assert db.one(conn, "application_revisions", approved["revision_id"]) == original_revision
        assert len(db.rows(conn, "category_submissions")) == 1
        assert len(db.rows(conn, "category_items")) == 2
    v2["login"]()
    group = v2["client"].get("/api/submissions").json()[0]
    assert group["approved_total"] == "0.30"
    assert all(item["possible_duplicates"] == [] for item in group["items"])


def test_list_evidence_summary_tracks_current_cross_file_manual_crops_not_old_jobs(v2, monkeypatch):
    import io
    from PIL import Image
    from app import materials
    client = v2["client"]
    client.app.include_router(materials.router)
    monkeypatch.setattr(materials, "configuration", lambda: {"max_analysis_pages": 12})
    monkeypatch.setattr(materials, "run_ocr", lambda *args: pytest.fail("summary must not start recognition"))
    parent = v2["group"]()
    created = client.post(f"/api/submissions/{parent['id']}/items", json={})
    assert created.status_code == 200, created.text
    item = created.json()
    files = []
    for name, color in (("参与和活动.png", "white"), ("补充印章.png", "lightblue")):
        content = io.BytesIO()
        Image.new("RGB", (400, 600), color).save(content, "PNG")
        uploaded = client.post(f"/api/applications/{item['id']}/files", files={"file": (name, content.getvalue(), "image/png")})
        assert uploaded.status_code == 200, uploaded.text
        files.append(uploaded.json())
    v2["confirm"](parent, [item])
    with db.write() as conn:
        old_job = db.add(conn, "background_jobs", application_id=item["id"], owner_id=v2["users"]["student"]["id"],
                         input_revision_id=item["revision"]["id"], kind="OCR", state="SUCCEEDED",
                         result_json={"status": "MANUAL_REQUIRED", "manual_required_files": [{"file_id": files[0]["id"]}],
                                      "missing_validity": ["NAME", "TIME", "SEAL"]})

    def summary():
        response = client.get("/api/submissions")
        assert response.status_code == 200, response.text
        group = next(group for group in response.json() if group["id"] == parent["id"])
        return next(child for child in group["items"] if child["id"] == item["id"])["evidence_summary"]

    assert summary() == {"has_participation": False, "has_activity_name": False,
                         "has_activity_time": False, "has_seal": False, "complete": False}
    crops = []
    for file, kind, validity in ((files[0], "PARTICIPATION", None), (files[0], "VALIDITY", "NAME"),
                                  (files[0], "VALIDITY", "TIME"), (files[1], "VALIDITY", "SEAL")):
        response = client.post(f"/api/applications/{item['id']}/crops", json={
            "page_id": file["pages"][0]["id"], "evidence_type": kind,
            "validity_kind": validity, "box": [10, 10, 200, 90]})
        assert response.status_code == 200, response.text
        crops.append(response.json())
    assert summary() == {"has_participation": True, "has_activity_name": True,
                         "has_activity_time": True, "has_seal": True, "complete": True}
    deleted = client.delete(f"/api/applications/{item['id']}/crops/{crops[-1]['id']}")
    assert deleted.status_code == 200, deleted.text
    assert summary() == {"has_participation": True, "has_activity_name": True,
                         "has_activity_time": True, "has_seal": False, "complete": False}
    with db.read() as conn:
        assert db.one(conn, "background_jobs", old_job["id"])["result_json"] == old_job["result_json"]
