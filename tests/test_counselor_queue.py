"""Dashboard counts match the existing two-stage review workflow."""
from test_submissions_v2 import v2


def test_counselor_counts_only_own_stage_and_keeps_peer_queue_separate(v2):
    client = v2["client"]
    collective, research = v2["group"](), v2["group"]("RESEARCH")
    first = v2["item"](collective, title="周末集体活动")
    second = v2["item"](research, title="待认定的科研竞赛")
    for parent, item in ((collective, first), (research, second)):
        v2["confirm"](parent, [item])
        assert v2["submit"](parent, [item])["submitted"] == 1

    params = {"year_id": v2["year"]["id"], "class_id": v2["class"]["id"]}
    v2["login"]("counselor")
    stats = client.get("/api/dashboard", params=params).json()["stats"]
    assert (stats["pending"], stats["pending_peer"], stats["pending_counselor"], stats["pending_total"]) == (0, 2, 0, 2)
    assert client.get("/api/submissions", params={**params, "scope": "review"}).json() == []
    # A counselor can track the peer stage without approving it out of order.
    groups = client.get("/api/submissions", params={**params, "scope": "class"}).json()
    assert sum(len(group["items"]) for group in groups) == 2
    assert client.post(f"/api/applications/{second['id']}/review", json={"action": "APPROVE"}).status_code == 403

    assert v2["review"](second)["state"] == "PENDING_COUNSELOR"
    v2["login"]("counselor")
    stats = client.get("/api/dashboard", params=params).json()["stats"]
    assert (stats["pending"], stats["pending_peer"], stats["pending_counselor"], stats["pending_total"]) == (1, 1, 1, 2)
    groups = client.get("/api/submissions", params={**params, "scope": "review"}).json()
    assert [item["id"] for group in groups for item in group["items"]] == [second["id"]]
    assert client.get(f"/api/applications/{second['id']}").json()["can_review"]

    # Ordinary collective activities still finish at the peer stage.
    assert v2["review"](first)["state"] == "APPROVED"
    v2["login"]("student")
    stats = client.get("/api/dashboard", params=params).json()["stats"]
    assert stats["pending"] == 1 and stats["pending_counselor"] == 1


def test_counselor_stage_counts_do_not_expose_other_classes(v2):
    parent = v2["group"]()
    item = v2["item"](parent)
    v2["confirm"](parent, [item])
    v2["submit"](parent, [item])
    v2["login"]("counselor")
    response = v2["client"].get("/api/dashboard", params={"year_id": v2["year"]["id"], "class_id": "unassigned-class"})
    assert response.status_code == 403
