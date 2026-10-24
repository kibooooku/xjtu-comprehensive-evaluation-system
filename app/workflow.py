"""Transactional applications, qualification, scoring and publicity.

Submitted content is immutable. Every approval concerns a submission round, never a
mutable working draft. SQLite BEGIN IMMEDIATE serializes the publication gate and
award replacement with submissions (see db.write).
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import delete

from . import db
from .migrations import active_year, assert_current_year, ensure_group
from .security import active_grants, allowed, class_context, current_user, require, student_enrollment

router = APIRouter(prefix="/api", tags=["applications and evaluation"])
PENDING = {"PENDING_PEER", "PENDING_COUNSELOR"}
LIVE = PENDING | {"APPROVED"}
COMPETITION_CODES = {"RESEARCH", "ARTS", "ART", "SPORT", "SPORTS", "REWARD"}
SCOPE_FIELDS = ("edition_key", "track_key", "group_key", "academic_year_id")


def _missing(message="记录不存在"):
    raise HTTPException(404, message)


def _norm(value):
    return re.sub(r"[\s\W_]+", "", str(value or "")).casefold()


def _text(value, maximum=255):
    text = str(value or "").strip()
    if len(text) > maximum:
        raise HTTPException(400, f"内容不能超过{maximum}个字符")
    return text


def _amount(value):
    try:
        amount = db.money(value)
        if not amount.is_finite() or amount < 0 or amount > Decimal("9999.99"):
            raise ValueError()
        return amount
    except (ValueError, TypeError, InvalidOperation):
        raise HTTPException(400, "分值必须是0至9999.99之间的有限数值")


def _version(application, payload):
    expected = payload.get("expected_version")
    if expected is not None and str(expected) != str(application["lock_version"]):
        raise HTTPException(409, "记录已被更新，请刷新后重试")


def _bump(conn, application):
    updated = db.change(conn, "applications", application["id"],
                        lock_version=application["lock_version"] + 1)
    application.update(updated)


def app_context(conn, application_id, include_archived=False):
    application = db.one(conn, "applications", application_id)
    if not application or application.get("archived_at") and not include_archived:
        _missing("申请不存在")
    context = class_context(conn, application["enrollment_id"])
    context.update(application=application, category=db.one(conn, "score_categories", application["category_id"]))
    return context


def _round(conn, application):
    return db.one(conn, "submission_rounds", application.get("current_round_id")) if application.get("current_round_id") else None


def _display_revision(conn, application):
    current = _round(conn, application)
    if current and current["state"] in LIVE:
        return db.one(conn, "application_revisions", current["revision_id"])
    return db.one(conn, "application_revisions", application.get("working_revision_id"))


def _readable(conn, user, context):
    if user["base_identity"] == "ADMIN":
        raise HTTPException(403, "系统管理员不读取业务申请与证明材料")
    require(conn, user, "application.read", class_id=context["class"]["id"], owner_id=context["user"]["id"])


def editable_revision(conn, application, user):
    context = app_context(conn, application["id"])
    if user["base_identity"] != "STUDENT" or context["user"]["id"] != user["id"]:
        raise HTTPException(403, "只能修改本人的申请")
    require(conn, user, "application.write", class_id=context["class"]["id"], owner_id=user["id"])
    assert_current_year(conn, context["year"]["id"])
    current = _round(conn, application)
    if current and current["state"] in LIVE:
        raise HTTPException(409, "待审申请请先撤回；已通过申请需由辅导员驳回后修改")
    revision = db.one(conn, "application_revisions", application["working_revision_id"])
    if revision.get("content_frozen_at"):
        clone = {k: v for k, v in revision.items() if k not in {"id", "created_at", "revision_no", "content_frozen_at"}}
        clone.update(revision_no=max(r["revision_no"] for r in db.rows(conn, "application_revisions", application_id=application["id"])) + 1,
                     content_frozen_at=None, created_by=user["id"])
        new = db.add(conn, "application_revisions", **clone)
        for table in ("revision_files", "evidence_crops"):
            for row in db.rows(conn, table, revision_id=revision["id"]):
                values = {k: v for k, v in row.items() if k != "id"}
                values["revision_id"] = new["id"]
                db.add(conn, table, **values)
        db.change(conn, "applications", application["id"], working_revision_id=new["id"])
        application["working_revision_id"] = new["id"]
        revision = new
    return revision


def _event(conn, application, event_type, actor=None, round_=None, from_state=None, to_state=None, detail=None):
    events = db.rows(conn, "application_events", application_id=application["id"])
    event = db.add(conn, "application_events", application_id=application["id"],
                   round_id=round_["id"] if round_ else None,
                   revision_id=round_["revision_id"] if round_ else application.get("working_revision_id"),
                   event_no=max((e["event_no"] for e in events), default=0) + 1,
                   event_type=event_type, actor_type="USER" if actor else "SYSTEM",
                   actor_id=actor["id"] if actor else None, from_state=from_state, to_state=to_state,
                   detail_json=detail or {}, occurred_at=db.now(), operation_id=db.uid())
    if round_:
        values = {"last_event_id": event["id"], "lock_version": round_["lock_version"] + 1}
        if to_state:
            values["state"] = to_state
        round_.update(db.change(conn, "submission_rounds", round_["id"], **values))
    return event


def _summary(conn, application, context=None):
    context = context or app_context(conn, application["id"])
    revision = _display_revision(conn, application)
    current = _round(conn, application)
    return {
        "id": application["id"], "title": revision["title"],
        "category_id": application["category_id"], "category_name": context["category"]["name"],
        "category_code": context["category"]["code"], "requested_score": str(db.money(revision["requested_score"])),
        "state": current["state"] if current else "DRAFT", "student_id": context["student"]["id"],
        "student_user_id": context["user"]["id"], "student_name": context["user"]["display_name"],
        "class_id": context["class"]["id"], "class_name": context["class"]["name"],
        "year_id": context["year"]["id"], "year_name": context["year"]["name"],
        "student_modified_score": revision["student_modified_score"],
        "round_id": current["id"] if current else None, "revision_id": revision["id"],
        "lock_version": application["lock_version"], "created_at": application["created_at"],
        "updated_at": current["submitted_at"] if current else revision["created_at"],
    }


def application_detail(conn, application, user, revision_id=None, include_deleted=False):
    from .materials import crop_view, file_view
    if isinstance(application, str):
        application = db.one(conn, "applications", application)
    context = app_context(conn, application["id"], include_archived=include_deleted)
    _readable(conn, user, context)
    revision = _display_revision(conn, application)
    historical = revision_id is not None
    if historical:
        revision = db.one(conn, "application_revisions", revision_id)
        if not revision or revision["application_id"] != application["id"]:
            _missing("申请内容版本不存在")
    current = _round(conn, application)
    result = _summary(conn, application, context)
    result.update(title=revision["title"], requested_score=str(db.money(revision["requested_score"])),
                  revision_id=revision["id"], student_modified_score=revision["student_modified_score"])
    files = []
    for attachment in sorted(db.rows(conn, "revision_files", revision_id=revision["id"]), key=lambda r: r["sort_order"]):
        file = db.one(conn, "file_objects", attachment["file_id"])
        files.append(file_view(conn, file))
    crops = [crop_view(c) for c in db.rows(conn, "evidence_crops", revision_id=revision["id"])]
    rounds = db.rows(conn, "submission_rounds", application_id=application["id"])
    round_ids = {r["id"] for r in rounds}
    reviews = [r for r in db.rows(conn, "review_decisions") if r["round_id"] in round_ids]
    for decision in reviews:
        decision["reviewer_name"] = decision["actor_name_snapshot"]
    history = sorted(db.rows(conn, "application_events", application_id=application["id"]), key=lambda e: e["event_no"])
    for event in history:
        actor = db.one(conn, "users", event["actor_id"]) if event.get("actor_id") else None
        event["actor_name"] = actor["display_name"] if actor else "系统"
    in_current_year = active_year(conn)["id"] == context["year"]["id"]
    can_edit = user["id"] == context["user"]["id"] and in_current_year and not application.get("archived_at") and (not current or current["state"] not in LIVE)
    can_peer = current and current["state"] == "PENDING_PEER" and user["id"] != context["user"]["id"] and allowed(conn, user, "review.peer", class_id=context["class"]["id"], owner_id=context["user"]["id"])
    can_counselor = current and current["state"] in {"PENDING_COUNSELOR", "APPROVED"} and user["base_identity"] == "COUNSELOR" and allowed(conn, user, "review.counselor" if current["state"] == "PENDING_COUNSELOR" else "review.revoke", class_id=context["class"]["id"])
    result.update(revision={**revision, "requested_score": str(db.money(revision["requested_score"]))},
                  files=files, crops=crops, history=history, reviews=reviews,
                  round=current, rounds=rounds, can_edit=bool(can_edit and not historical), can_review=bool((can_peer or can_counselor) and not historical and not application.get("archived_at")),
                  can_withdraw=bool(not historical and not application.get("archived_at") and in_current_year and current and current["state"] in PENDING and user["id"] == context["user"]["id"]),
                  viewing_history=bool(historical or application.get("archived_at")),
                  versions=[{"id": r["id"], "revision_no": r["revision_no"], "title": r["title"],
                             "requested_score": str(db.money(r["requested_score"])), "frozen_at": r["content_frozen_at"],
                             "created_at": r["created_at"]}
                            for r in sorted(db.rows(conn, "application_revisions", application_id=application["id"]), key=lambda r: r["revision_no"])])
    activity_id = revision.get("activity_id")
    if not activity_id and current and current.get("eligibility_version_id") and current["revision_id"] == revision["id"]:
        qualification = db.one(conn, "activity_eligibility_versions", current["eligibility_version_id"])
        activity_id = qualification["activity_id"] if qualification else None
    if activity_id:
        result["activity"] = db.one(conn, "activities", activity_id)
        result["eligibility"] = activity_eligibility(conn, activity_id, application["category_id"], context["year"]["id"], revision["payload_json"])
    item = db.one(conn, "category_items", application_id=application["id"])
    result.update(submission_id=item["submission_id"] if item else None,
                  confirmed_at=item["confirmed_at"] if item else application["created_at"],
                  deleted_at=item["deleted_at"] if item else application.get("archived_at"),
                  is_current_year=in_current_year)
    analyses = sorted(db.rows(conn, "ai_analyses", application_id=application["id"], status="SUCCEEDED"), key=lambda a: a["created_at"])
    # V1 generated suggestions before durable jobs existed. Keep them accessible
    # after grouping instead of requiring another paid generation to view them.
    result["latest_analysis"] = analyses[-1] if analyses else None
    return result


@router.get("/applications")
def list_applications(year_id: str | None = None, class_id: str | None = None,
                      scope: str = "mine", state: str | None = None, user=Depends(current_user)):
    with db.read() as conn:
        if user["base_identity"] == "ADMIN":
            raise HTTPException(403, "系统管理员不查看业务申请")
        result = []
        from .submissions import visible_application
        for application in db.rows(conn, "applications"):
            if not visible_application(conn, application):
                continue
            context = app_context(conn, application["id"])
            if year_id and context["year"]["id"] != year_id or class_id and context["class"]["id"] != class_id:
                continue
            owner_id, cid = context["user"]["id"], context["class"]["id"]
            if scope == "mine" and owner_id != user["id"]:
                continue
            if not allowed(conn, user, "application.read", class_id=cid, owner_id=owner_id):
                continue
            summary = _summary(conn, application, context)
            if state and state != "ALL" and summary["state"] != state:
                continue
            if scope == "review":
                permission = "review.peer" if summary["state"] == "PENDING_PEER" else "review.counselor"
                if summary["state"] not in PENDING or owner_id == user["id"] or not allowed(conn, user, permission, class_id=cid, owner_id=owner_id):
                    continue
            result.append(summary)
        return sorted(result, key=lambda r: r["updated_at"], reverse=True)


def new_application(conn, user, category_id, year_id=None, confirmed=True):
    if user["base_identity"] != "STUDENT":
        raise HTTPException(403, "只有学生可以填报")
    year = active_year(conn) if year_id is None else db.one(conn, "academic_years", year_id)
    category = db.one(conn, "score_categories", category_id)
    if not year or not year["is_selectable"] or not category:
        raise HTTPException(400, "请选择有效的加分类别")
    assert_current_year(conn, year["id"])
    enrollment = student_enrollment(conn, user, year["id"])
    context = class_context(conn, enrollment["id"])
    require(conn, user, "application.write", class_id=context["class"]["id"], owner_id=user["id"])
    application = db.add(conn, "applications", enrollment_id=enrollment["id"], category_id=category["id"],
                         working_revision_id=None, current_round_id=None, lock_version=0)
    revision = db.add(conn, "application_revisions", application_id=application["id"], revision_no=1,
                      title="", requested_score=Decimal("0.00"), student_modified_score=False,
                      activity_id=None, competition_project_id=None, ai_analysis_id=None,
                      payload_json={}, content_frozen_at=None, created_by=user["id"])
    application = db.change(conn, "applications", application["id"], working_revision_id=revision["id"])
    ensure_group(conn, application, confirmed=confirmed)
    _event(conn, application, "DRAFT_CREATED", user)
    return application


@router.post("/applications")
def create_application(payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        application = new_application(conn, user, payload.get("category_id"), payload.get("year_id"), confirmed=True)
        return application_detail(conn, application, user)


@router.get("/applications/{application_id}")
def get_application(application_id: str, revision_id: str | None = None, include_deleted: bool = False, user=Depends(current_user)):
    with db.read() as conn:
        return application_detail(conn, app_context(conn, application_id, include_archived=include_deleted)["application"], user,
                                  revision_id, include_deleted=include_deleted)


@router.put("/applications/{application_id}")
def update_application(application_id: str, payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        context = app_context(conn, application_id)
        application = context["application"]
        _version(application, payload)
        revision = editable_revision(conn, application, user)
        if payload.get("category_id", application["category_id"]) != application["category_id"]:
            raise HTTPException(400, "修改大类请从对应入口新建申请")
        values = {}
        original_facts = revision.get("payload_json") or {}
        protected_flags = {k: original_facts[k] for k in ("evidence_manually_edited", "content_manually_edited") if original_facts.get(k)}
        content_changed = False
        if "title" in payload:
            values["title"] = _text(payload["title"])
            content_changed = values["title"] != revision["title"]
        if "requested_score" in payload:
            score = _amount(payload["requested_score"])
            values["requested_score"] = score
            content_changed = content_changed or score != db.money(revision["requested_score"])
            # The marker records any manual edit, including reverting to the AI value.
            values["student_modified_score"] = revision["student_modified_score"] or score != db.money(revision["requested_score"])
        if "activity_id" in payload:
            activity_id = payload["activity_id"] or None
            activity = db.one(conn, "activities", activity_id) if activity_id else None
            if activity_id and (not activity or activity["status"] != "ACTIVE"):
                raise HTTPException(400, "所选活动不存在或已停用，请重新选择")
            values["activity_id"] = activity_id
            content_changed = content_changed or activity_id != revision.get("activity_id")
        if "payload_json" in payload:
            facts = payload["payload_json"]
            if not isinstance(facts, dict) or len(json.dumps(facts, ensure_ascii=False)) > 12000:
                raise HTTPException(400, "活动信息格式不正确或过长")
            facts = {**facts, **protected_flags}
            content_changed = content_changed or facts != original_facts
            values["payload_json"] = facts
        if "file_ids" in payload:
            if not isinstance(payload["file_ids"], list) or len(payload["file_ids"]) > 12 or any(not isinstance(i, str) for i in payload["file_ids"]):
                raise HTTPException(400, "附件列表格式无效或超过12份")
            ids = list(dict.fromkeys(payload["file_ids"]))
            for file_id in ids:
                file = db.one(conn, "file_objects", file_id)
                if not file or file["uploaded_by"] != user["id"]:
                    raise HTTPException(403, "只能使用本人上传的原文件")
            from .submissions import assert_unique_files
            assert_unique_files(conn, application_id, ids)
            for link in db.rows(conn, "revision_files", revision_id=revision["id"]):
                if link["file_id"] not in ids:
                    conn.execute(delete(db.tables["revision_files"]).where(db.tables["revision_files"].c.id == link["id"]))
            existing = {r["file_id"] for r in db.rows(conn, "revision_files", revision_id=revision["id"])}
            for index, file_id in enumerate(ids):
                if file_id not in existing:
                    db.add(conn, "revision_files", revision_id=revision["id"], file_id=file_id, sort_order=index,
                           added_by=user["id"], added_at=db.now(), purpose="证明材料")
            attached_pages = {p["id"] for file_id in ids for p in db.rows(conn, "file_pages", file_id=file_id)}
            for crop in db.rows(conn, "evidence_crops", revision_id=revision["id"]):
                if crop["page_id"] not in attached_pages:
                    conn.execute(delete(db.tables["evidence_crops"]).where(db.tables["evidence_crops"].c.id == crop["id"]))
        if content_changed:
            values["payload_json"] = {**values.get("payload_json", original_facts), **protected_flags, "content_manually_edited": True}
        db.change(conn, "application_revisions", revision["id"], **values)
        _bump(conn, application)
        _event(conn, application, "DRAFT_UPDATED", user, detail={"fields": list(values)})
        return application_detail(conn, application, user)


def _latest_eligibilities(conn, activity_id):
    latest = {}
    for version in db.rows(conn, "activity_eligibility_versions", activity_id=activity_id):
        key = (version["category_id"], version["scope_key"])
        if key not in latest or (version["version_no"], version["effective_at"]) > (latest[key]["version_no"], latest[key]["effective_at"]):
            latest[key] = version
    return [v for v in latest.values() if v["decision"] != "REMOVED"]


def activity_eligibility(conn, activity_id, category_id, year_id, facts):
    if not activity_id:
        return {"decision": "UNKNOWN", "version_id": None}
    activity = db.one(conn, "activities", activity_id)
    if not activity or activity["status"] != "ACTIVE":
        return {"decision": "UNKNOWN", "version_id": None}
    candidates = []
    actual = {**(facts or {}), "academic_year_id": year_id}
    for version in _latest_eligibilities(conn, activity_id):
        if version["category_id"] != category_id:
            continue
        restrictions = {key: version.get(key) for key in SCOPE_FIELDS if version.get(key)}
        if all(_norm(actual.get(key)) == _norm(value) for key, value in restrictions.items()):
            candidates.append((len(restrictions), version))
    if not candidates:
        return {"decision": "UNKNOWN", "version_id": None}
    specificity = max(c[0] for c in candidates)
    matched = [v for score, v in candidates if score == specificity]
    if len({v["decision"] for v in matched}) > 1:
        return {"decision": "UNKNOWN", "version_id": None, "conflict": True,
                "message": "同等范围资格冲突，需辅导员人工认定"}
    chosen = max(matched, key=lambda v: (v["effective_at"], v["version_no"]))
    return {"decision": chosen["decision"], "version_id": chosen["id"], "reason": chosen.get("reason")}


def _set_eligibility(conn, user, activity, payload, review_id=None):
    category_id = payload.get("category_id")
    if not db.one(conn, "score_categories", category_id):
        raise HTTPException(400, "请选择有效的申请大类")
    decision = str(payload.get("decision") or "UNKNOWN").upper()
    if decision not in {"VALID", "INVALID", "REMOVED"}:
        raise HTTPException(400, "资格认定须为VALID、INVALID或REMOVED")
    scopes = {key: _text(payload.get(key), 100) or None for key in SCOPE_FIELDS}
    if scopes["academic_year_id"] and not db.one(conn, "academic_years", scopes["academic_year_id"]):
        raise HTTPException(400, "认定学年不存在")
    scope_key = json.dumps(scopes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = [v for v in db.rows(conn, "activity_eligibility_versions", activity_id=activity["id"])
                if v["category_id"] == category_id and v["scope_key"] == scope_key]
    return db.add(conn, "activity_eligibility_versions", activity_id=activity["id"], category_id=category_id,
                  scope_key=scope_key, **scopes, version_no=max((v["version_no"] for v in existing), default=0) + 1,
                  decision=decision, effective_at=db.now(), decided_by=user["id"],
                  source_review_decision_id=review_id, reason=_text(payload.get("reason"), 2000))


def _competition(conn, context, revision, user, related_id=None):
    """Return (identity key, priority). Category/owner/year form the boundary."""
    category = context["category"]
    facts = revision["payload_json"] or {}
    if category["code"].upper() not in COMPETITION_CODES and not category["requires_eligibility_review"]:
        return None, None
    if not revision.get("activity_id") and not facts.get("competition_name") and not facts.get("edition_key") and not related_id:
        return None, None
    edition = _norm(facts.get("edition_key"))
    track = _norm(facts.get("track_key"))
    group = _norm(facts.get("group_key"))
    work = _norm(facts.get("work_key"))
    activity = revision.get("activity_id") or _norm(facts.get("competition_name") or revision["title"])
    if related_id:
        old_context = app_context(conn, related_id)
        if old_context["user"]["id"] != user["id"] or old_context["year"]["id"] != context["year"]["id"] or old_context["category"]["id"] != category["id"]:
            raise HTTPException(400, "关联的旧申请必须属于本人、同一学年及同一类别")
        old_round = _round(conn, old_context["application"])
        if not old_round or not old_round.get("competition_group_key"):
            raise HTTPException(400, "旧申请尚未形成可关联的竞赛项目")
        key = old_round["competition_group_key"]
        old_revision = db.one(conn, "application_revisions", old_round["revision_id"])
        project_id = old_revision.get("competition_project_id")
    else:
        # Insufficient identity is not guessed from similar titles. Require a
        # concrete edition and track/group/work, or explicit old-item confirmation.
        related = []
        for old in db.rows(conn, "applications", enrollment_id=context["enrollment"]["id"], category_id=category["id"]):
            if old["id"] == context["application"]["id"]:
                continue
            old_round = _round(conn, old)
            if not old_round or old_round["state"] not in LIVE:
                continue
            old_revision = db.one(conn, "application_revisions", old_round["revision_id"])
            old_facts = old_revision["payload_json"] or {}
            same_activity = old_revision.get("activity_id") == revision.get("activity_id") and revision.get("activity_id")
            same_free_name = _norm(old_facts.get("competition_name")) == _norm(facts.get("competition_name")) and facts.get("competition_name")
            if not (same_activity or same_free_name):
                continue
            # Known distinct values establish distinct projects. Missing identity
            # on either claim is ambiguous even if the newer claim is complete.
            pairs = [(facts.get(k), old_facts.get(k)) for k in ("edition_key", "track_key", "group_key", "work_key")]
            known_difference = any(a and b and _norm(a) != _norm(b) for a, b in pairs)
            old_complete = bool(old_facts.get("edition_key") and any(old_facts.get(k) for k in ("track_key", "group_key", "work_key")))
            if not known_difference and (not edition or not (track or group or work) or not old_complete):
                related.append({"id": old["id"], "title": old_revision["title"]})
        if related:
            raise HTTPException(409, {"message": "比赛项目信息不足，请补充届次及赛道／组别／作品，或确认关联的旧申请", "related_applications": related})
        if not edition or not (track or group or work):
            # First submission may still proceed, but uses an explicit unknown
            # identity so later same-activity incomplete claims require confirmation.
            edition = edition or "未注明届次"
        identity = [user["id"], context["year"]["id"], category["id"], activity, edition, track, group, work]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        project_id = None
        if revision.get("activity_id"):
            fingerprint = hashlib.sha256(json.dumps(identity[3:], ensure_ascii=False).encode()).hexdigest()
            project = next((p for p in db.rows(conn, "competition_projects") if p.get("identity_fingerprint") == fingerprint), None)
            if not project:
                project = db.add(conn, "competition_projects", activity_id=revision["activity_id"], edition_key=edition,
                                 track_key=track, group_key=group, work_key=work, identity_fingerprint=fingerprint,
                                 status="CONFIRMED", confirmed_by=user["id"], confirmed_at=db.now())
            project_id = project["id"]
    if project_id:
        db.change(conn, "application_revisions", revision["id"], competition_project_id=project_id)
        revision["competition_project_id"] = project_id
    return key, (db.money(revision["requested_score"]), _award_rank(facts.get("award_level")))


def _award_rank(value):
    text = str(value or "").upper()
    ranks = {"INTERNATIONAL": 5, "国际": 5, "NATIONAL": 4, "国家": 4, "国赛": 4,
             "PROVINCIAL": 3, "省级": 3, "省赛": 3, "UNIVERSITY": 2, "SCHOOL": 2, "校级": 2,
             "COLLEGE": 1, "院级": 1}
    if text.isdigit():
        return min(int(text), 100)
    return next((rank for name, rank in ranks.items() if name in text), 0)


def _restore(conn, failed_round):
    """Restore only the transition caused by this supersession, never human work."""
    for replacement in db.rows(conn, "award_supersessions", new_round_id=failed_round["id"], status="ACTIVE"):
        old_round = db.one(conn, "submission_rounds", replacement["old_round_id"])
        old_app = db.one(conn, "applications", old_round["application_id"])
        if (not old_app.get("archived_at") and old_app["current_round_id"] == old_round["id"] and old_round["state"] == "REJECTED"
                and old_round.get("last_event_id") == replacement["superseded_event_id"]):
            event = _event(conn, old_app, "AWARD_RESTORED", round_=old_round, from_state="REJECTED",
                           to_state=replacement["old_state"], detail={"supersession_id": replacement["id"], "failed_round_id": failed_round["id"]})
            db.change(conn, "submission_rounds", old_round["id"], is_current_candidate=True)
            db.change(conn, "award_supersessions", replacement["id"], status="RESTORED",
                      restored_event_id=event["id"], resolved_at=db.now())
            _bump(conn, old_app)
        else:
            db.change(conn, "award_supersessions", replacement["id"], status="SKIPPED", resolved_at=db.now())


def _assert_class_ready(conn, context):
    if context["class"]["status"] != "ACTIVE":
        raise HTTPException(409, "班级尚未完成组织配置，请联系管理员设置班长及至少两名审查员")
    roles = {r["id"]: r["code"] for r in db.rows(conn, "roles")}
    role_users = {"MONITOR": set(), "REVIEWER": set()}
    instant = db.now()
    for grant in db.rows(conn, "role_assignments", class_id=context["class"]["id"], status="ACTIVE"):
        code = roles.get(grant["role_id"])
        if code not in role_users or grant["valid_from"] > instant or grant.get("valid_to") and grant["valid_to"] <= instant:
            continue
        actor = db.one(conn, "users", grant["user_id"])
        if actor and actor["status"] == "ACTIVE" and actor["base_identity"] == "STUDENT":
            role_users[code].add(actor["id"])
    if len(role_users["MONITOR"]) != 1 or len(role_users["REVIEWER"]) < 2:
        raise HTTPException(409, "班级必须配置一名班长和至少两名不同的有效学生审查员后才能正式填报")


@router.post("/applications/{application_id}/submit")
def submit_application(application_id: str, payload: dict = Body(default={}), user=Depends(current_user)):
    with db.write() as conn:
        context = app_context(conn, application_id)
        application = context["application"]
        _version(application, payload)
        require(conn, user, "application.submit", class_id=context["class"]["id"], owner_id=context["user"]["id"])
        if user["id"] != context["user"]["id"]:
            raise HTTPException(403, "只能提交本人申请")
        from .submissions import assert_confirmed
        assert_confirmed(conn, application_id)
        _assert_class_ready(conn, context)
        publication = db.one(conn, "class_publicity", class_year_id=context["class_year"]["id"])
        if publication and publication["is_open"]:
            raise HTTPException(409, "本班本学年正在公示，允许保存草稿，暂停正式提交及奖项替代")
        revision = editable_revision(conn, application, user)
        if not revision["title"].strip():
            raise HTTPException(400, "请填写加分条目名称")
        _amount(revision["requested_score"])
        if not db.rows(conn, "revision_files", revision_id=revision["id"]):
            raise HTTPException(400, "每条申请至少上传一个原文件")
        eligibility = activity_eligibility(conn, revision.get("activity_id"), application["category_id"], context["year"]["id"], revision["payload_json"])
        if eligibility["decision"] == "INVALID":
            raise HTTPException(400, "所选活动在当前适用范围被认定无加分资格，请核对活动匹配")
        key, priority = _competition(conn, context, revision, user, payload.get("related_application_id"))
        competitors = []
        if key:
            for old_round in db.rows(conn, "submission_rounds", competition_group_key=key):
                old_app = db.one(conn, "applications", old_round["application_id"])
                if old_app["id"] == application_id or old_app["current_round_id"] != old_round["id"] or old_round["state"] not in LIVE:
                    continue
                old_revision = db.one(conn, "application_revisions", old_round["revision_id"])
                old_priority = (db.money(old_revision["requested_score"]), _award_rank((old_revision["payload_json"] or {}).get("award_level")))
                if priority <= old_priority:
                    raise HTTPException(409, "同一比赛项目已有更高或同等优先级申请，不能重复申报")
                competitors.append((old_app, old_round, old_revision, old_priority))
        db.change(conn, "application_revisions", revision["id"], content_frozen_at=db.now())
        previous = _round(conn, application)
        if previous:
            db.change(conn, "submission_rounds", previous["id"], is_current_candidate=False)
        round_ = db.add(conn, "submission_rounds", application_id=application_id, revision_id=revision["id"],
                        round_no=max((r["round_no"] for r in db.rows(conn, "submission_rounds", application_id=application_id)), default=0) + 1,
                        state="PENDING_PEER", eligibility_version_id=eligibility["version_id"], competition_group_key=key,
                        is_current_candidate=True, lock_version=0, submitted_at=db.now(), last_event_id=None)
        application.update(db.change(conn, "applications", application_id, current_round_id=round_["id"]))
        _event(conn, application, "SUBMITTED", user, round_=round_, to_state="PENDING_PEER")
        for old_app, old_round, old_revision, old_priority in competitors:
            old_state, old_version = old_round["state"], old_round["lock_version"]
            event = _event(conn, old_app, "AWARD_SUPERSEDED", round_=old_round, from_state=old_state,
                           to_state="REJECTED", detail={"new_round_id": round_["id"], "new_application_id": application_id})
            db.change(conn, "submission_rounds", old_round["id"], is_current_candidate=False)
            db.add(conn, "award_supersessions", new_round_id=round_["id"], old_round_id=old_round["id"],
                   competition_group_key=key, old_state=old_state, old_lock_version=old_version,
                   superseded_event_id=event["id"], restored_event_id=None, status="ACTIVE",
                   comparison_snapshot_json={"old_score": str(old_priority[0]), "old_rank": old_priority[1],
                                             "new_score": str(priority[0]), "new_rank": priority[1]}, created_at=db.now(), resolved_at=None)
            _bump(conn, old_app)
        _bump(conn, application)
        return application_detail(conn, application, user)


@router.post("/applications/{application_id}/withdraw")
def withdraw_application(application_id: str, payload: dict = Body(default={}), user=Depends(current_user)):
    with db.write() as conn:
        context = app_context(conn, application_id)
        application = context["application"]
        _version(application, payload)
        require(conn, user, "application.withdraw", class_id=context["class"]["id"], owner_id=context["user"]["id"])
        if user["id"] != context["user"]["id"]:
            raise HTTPException(403, "只能撤回本人待审申请")
        assert_current_year(conn, context["year"]["id"])
        round_ = _round(conn, application)
        if not round_ or round_["state"] not in PENDING:
            raise HTTPException(409, "只有待审申请可以撤回")
        _event(conn, application, "WITHDRAWN", user, round_=round_, from_state=round_["state"], to_state="WITHDRAWN")
        db.change(conn, "submission_rounds", round_["id"], is_current_candidate=False)
        _restore(conn, round_)
        _bump(conn, application)
        return application_detail(conn, application, user)


@router.post("/applications/{application_id}/review")
def review_application(application_id: str, payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        context = app_context(conn, application_id)
        application = context["application"]
        _readable(conn, user, context)
        round_ = _round(conn, application)
        action = str(payload.get("action") or "").upper()
        key = _text(payload.get("idempotency_key"), 100) or db.uid()
        existing = db.one(conn, "review_decisions", idempotency_key=key)
        if existing:
            if existing["actor_id"] == user["id"] and existing["round_id"] == (round_ or {}).get("id") and existing["action"] == action:
                return application_detail(conn, application, user)
            raise HTTPException(409, "审核请求标识重复")
        _version(application, payload)
        if not round_ or action not in {"APPROVE", "REJECT"}:
            raise HTTPException(400, "请选择有效的审核操作")
        if user["id"] == context["user"]["id"]:
            raise HTTPException(403, "不能审核自己的申请")
        previous_state = round_["state"]
        if previous_state == "PENDING_PEER":
            if user["base_identity"] != "STUDENT":
                raise HTTPException(403, "初审由本班学生审查员完成")
            require(conn, user, "review.peer", class_id=context["class"]["id"], owner_id=context["user"]["id"])
            stage, role_code = "PEER", "REVIEWER"
        elif previous_state in {"PENDING_COUNSELOR", "APPROVED"}:
            if user["base_identity"] != "COUNSELOR":
                raise HTTPException(403, "当前阶段须由所带班级辅导员处理")
            if previous_state == "APPROVED" and action != "REJECT":
                raise HTTPException(409, "已通过申请只允许辅导员驳回")
            require(conn, user, "review.revoke" if previous_state == "APPROVED" else "review.counselor", class_id=context["class"]["id"])
            stage, role_code = ("REVOCATION" if previous_state == "APPROVED" else "COUNSELOR"), "COUNSELOR"
        elif previous_state == "REJECTED" and user["base_identity"] == "COUNSELOR" and action == "REJECT":
            # An independent human rejection of a superseded round is meaningful:
            # it prevents a later failed replacement restoring invalid material.
            active = db.rows(conn, "award_supersessions", old_round_id=round_["id"], status="ACTIVE")
            if not active:
                raise HTTPException(409, "该申请本轮已审核结束")
            require(conn, user, "review.revoke", class_id=context["class"]["id"])
            stage, role_code = "REVOCATION", "COUNSELOR"
        else:
            raise HTTPException(409, "该申请本轮已审核结束或不在可审核阶段")
        reason = _text(payload.get("reason"), 100)
        if action == "REJECT" and not reason:
            raise HTTPException(400, "驳回必须选择原因，备注可选")
        revision = db.one(conn, "application_revisions", round_["revision_id"])
        assignment = next((grant for grant, role in active_grants(conn, user)
                           if role["code"] == role_code and (not grant.get("class_id") or grant["class_id"] == context["class"]["id"])), None)
        decision = db.add(conn, "review_decisions", round_id=round_["id"], revision_id=revision["id"], actor_id=user["id"],
                          role_assignment_id=assignment["id"] if assignment else None, stage=stage, action=action,
                          rejection_reason=reason or None, comment=_text(payload.get("comment"), 2000),
                          actor_name_snapshot=user["display_name"], decided_at=db.now(), idempotency_key=key)
        # Approving an unknown activity is itself a positive qualification
        # decision; rejecting individual evidence without a qualification choice
        # never inserts a globally invalid activity.
        qualification = str(payload.get("activity_decision") or ("VALID" if stage == "COUNSELOR" and action == "APPROVE" else "")).upper()
        if qualification and stage not in {"COUNSELOR", "REVOCATION"}:
            raise HTTPException(403, "活动资格只能由辅导员认定")
        if qualification:
            if qualification not in {"VALID", "INVALID"}:
                raise HTTPException(400, "活动资格认定不正确")
            if qualification == "INVALID" and action != "REJECT":
                raise HTTPException(400, "无加分资格的活动不能批准个人申请")
            activity = db.one(conn, "activities", revision["activity_id"]) if revision.get("activity_id") else None
            if not activity:
                canonical_name = _text(payload.get("activity_name") or revision["title"])
                normalized = _norm(canonical_name)
                activity = db.one(conn, "activities", normalized_name=normalized)
                if not activity:
                    activity = db.add(conn, "activities", canonical_name=canonical_name, normalized_name=normalized,
                                      status="ACTIVE", maintained_by=user["id"], description="审核时认定", organizer="")
                # Submitted revision remains immutable: this link belongs in the
                # review/event and eligibility snapshot, not patched into content.
            scope_payload = {**(revision["payload_json"] or {}), "academic_year_id": context["year"]["id"],
                             "category_id": application["category_id"], "decision": qualification,
                             "reason": payload.get("comment") or reason}
            eligibility_version = _set_eligibility(conn, user, activity, scope_payload, decision["id"])
            db.change(conn, "submission_rounds", round_["id"], eligibility_version_id=eligibility_version["id"])
        if action == "REJECT":
            target = "REJECTED"
        elif stage == "PEER":
            eligibility = activity_eligibility(conn, revision.get("activity_id"), application["category_id"], context["year"]["id"], revision["payload_json"])
            if eligibility["decision"] == "INVALID":
                raise HTTPException(409, "活动现已被认定无加分资格，请驳回或交由辅导员复核")
            target = "PENDING_COUNSELOR" if context["category"]["requires_eligibility_review"] and eligibility["decision"] != "VALID" else "APPROVED"
        else:
            target = "APPROVED"
        _event(conn, application, "REVIEW_" + action, user, round_=round_, from_state=previous_state,
               to_state=target, detail={"review_decision_id": decision["id"], "stage": stage,
                                      "reason": reason, "activity_decision": qualification or None})
        if target == "REJECTED":
            db.change(conn, "submission_rounds", round_["id"], is_current_candidate=False)
            _restore(conn, round_)
        _bump(conn, application)
        return application_detail(conn, application, user)


@router.get("/activities")
def list_activities(search: str = "", user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, "activity.read")
        result = []
        for activity in db.rows(conn, "activities", status="ACTIVE"):
            aliases = [a["alias"] for a in db.rows(conn, "activity_aliases", activity_id=activity["id"], status="ACTIVE")]
            if search and _norm(search) not in _norm(activity["canonical_name"] + " ".join(aliases)):
                continue
            result.append({**activity, "aliases": aliases, "eligibilities": _latest_eligibilities(conn, activity["id"])})
        return sorted(result, key=lambda a: a["canonical_name"])


def _activity_values(conn, user, payload, activity=None):
    if user["base_identity"] != "COUNSELOR":
        raise HTTPException(403, "活动资格库由辅导员维护")
    require(conn, user, "activity.manage")
    name = _text(payload.get("canonical_name") or (activity or {}).get("canonical_name"))
    if not name:
        raise HTTPException(400, "请填写规范活动名称")
    normalized = _norm(name)
    duplicate = db.one(conn, "activities", normalized_name=normalized)
    if duplicate and (not activity or duplicate["id"] != activity["id"]):
        raise HTTPException(409, "规范活动名称已存在，请添加别名或修改已有记录")
    if any(a["activity_id"] != (activity or {}).get("id") for a in db.rows(conn, "activity_aliases", normalized_alias=normalized, status="ACTIVE")):
        raise HTTPException(409, "活动名称已作为其他活动的别名存在，请核对规范活动")
    values = {"canonical_name": name, "normalized_name": normalized, "organizer": _text(payload.get("organizer")),
              "description": _text(payload.get("description"), 3000), "maintained_by": user["id"], "status": "ACTIVE", "updated_at": db.now()}
    activity = db.change(conn, "activities", activity["id"], **values) if activity else db.add(conn, "activities", **values)
    if "aliases" in payload:
        aliases = payload["aliases"]
        if not isinstance(aliases, list) or len(aliases) > 30:
            raise HTTPException(400, "别名格式不正确或数量过多")
        for alias in db.rows(conn, "activity_aliases", activity_id=activity["id"]):
            db.change(conn, "activity_aliases", alias["id"], status="INACTIVE")
        for name in dict.fromkeys(_text(a) for a in aliases if _text(a)):
            normalized_alias = _norm(name)
            conflicts = [a for a in db.rows(conn, "activity_aliases", normalized_alias=normalized_alias, status="ACTIVE") if a["activity_id"] != activity["id"]]
            canonical_conflict = db.one(conn, "activities", normalized_name=normalized_alias, status="ACTIVE")
            if conflicts or canonical_conflict and canonical_conflict["id"] != activity["id"]:
                raise HTTPException(409, "别名已属于其他活动，请先处理重复名称")
            old = db.one(conn, "activity_aliases", activity_id=activity["id"], normalized_alias=normalized_alias)
            if old:
                db.change(conn, "activity_aliases", old["id"], alias=name, status="ACTIVE")
            else:
                db.add(conn, "activity_aliases", activity_id=activity["id"], alias=name, normalized_alias=normalized_alias,
                       created_by=user["id"], status="ACTIVE")
    if payload.get("decision"):
        _set_eligibility(conn, user, activity, payload)
    return {**activity, "aliases": [a["alias"] for a in db.rows(conn, "activity_aliases", activity_id=activity["id"], status="ACTIVE")],
            "eligibilities": _latest_eligibilities(conn, activity["id"])}


@router.post("/activities")
def create_activity(payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        return _activity_values(conn, user, payload)


@router.put("/activities/{activity_id}")
def update_activity(activity_id: str, payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        activity = db.one(conn, "activities", activity_id)
        if not activity:
            _missing("活动不存在")
        return _activity_values(conn, user, payload, activity)


@router.delete("/activities/{activity_id}")
def remove_activity(activity_id: str, user=Depends(current_user)):
    with db.write() as conn:
        require(conn, user, "activity.manage")
        if user["base_identity"] != "COUNSELOR":
            raise HTTPException(403, "活动资格库由辅导员维护")
        activity = db.one(conn, "activities", activity_id)
        if not activity:
            _missing("活动不存在")
        for version in _latest_eligibilities(conn, activity_id):
            _set_eligibility(conn, user, activity, {**version, "decision": "REMOVED", "reason": "辅导员移除资格认定"})
        db.change(conn, "activities", activity_id, status="INACTIVE", updated_at=db.now(), maintained_by=user["id"])
        return {"ok": True, "message": "已移除；历史审核与计分保留"}


@router.get("/activities/{activity_id}/impact")
def activity_impact(activity_id: str, user=Depends(current_user)):
    with db.read() as conn:
        require(conn, user, "activity.manage")
        result = []
        for application in db.rows(conn, "applications"):
            current = _round(conn, application)
            if not current or current["state"] != "APPROVED":
                continue
            revision = db.one(conn, "application_revisions", current["revision_id"])
            effective_activity_id = revision.get("activity_id")
            if not effective_activity_id and current.get("eligibility_version_id"):
                qualification = db.one(conn, "activity_eligibility_versions", current["eligibility_version_id"])
                effective_activity_id = qualification["activity_id"] if qualification else None
            if effective_activity_id != activity_id:
                continue
            context = app_context(conn, application["id"])
            if not allowed(conn, user, "application.read", class_id=context["class"]["id"], owner_id=context["user"]["id"]):
                continue
            qualification = activity_eligibility(conn, activity_id, application["category_id"], context["year"]["id"], revision["payload_json"])
            result.append({**_summary(conn, application, context), "current_eligibility": qualification})
        return result


def _year_class(conn, class_id, year_id):
    class_ = db.one(conn, "classes", class_id)
    year = db.one(conn, "academic_years", year_id)
    class_year = db.one(conn, "class_years", class_id=class_id, academic_year_id=year_id)
    if not class_ or not year or not class_year:
        _missing("班级或学年档案不存在")
    return class_, year, class_year


def _safe_public_text(value, student_numbers):
    value = str(value or "")
    for number in student_numbers:
        if number:
            value = value.replace(number, "[学号已隐藏]")
    return re.sub(r"(?<!\d)\d{8,20}(?!\d)", "[编号已隐藏]", value)


def class_scores(conn, class_id, year_id, public=False):
    from .submissions import visible_application
    class_, year, class_year = _year_class(conn, class_id, year_id)
    publication = db.one(conn, "class_publicity", class_year_id=class_year["id"])
    categories = sorted(db.rows(conn, "score_categories"), key=lambda c: c["sort_order"])
    rule = db.one(conn, "score_rule_versions", class_year["score_rule_version_id"])
    baseline = db.money(rule["baseline_score"] if rule else 70)
    enrolled = db.rows(conn, "enrollments", class_year_id=class_year["id"], status="ACTIVE")
    student_numbers = [db.one(conn, "student_profiles", e["student_id"])["student_number"] for e in enrolled]
    members = []
    for enrollment in enrolled:
        profile = db.one(conn, "student_profiles", enrollment["student_id"])
        student = db.one(conn, "users", profile["user_id"])
        grouped = {c["id"]: [] for c in categories}
        for application in db.rows(conn, "applications", enrollment_id=enrollment["id"]):
            if not visible_application(conn, application):
                continue
            current = _round(conn, application)
            if not current or current["state"] != "APPROVED":
                continue
            revision = db.one(conn, "application_revisions", current["revision_id"])
            peer_reviews = [r for r in db.rows(conn, "review_decisions", round_id=current["id"]) if r["stage"] == "PEER" and r["action"] == "APPROVE"]
            peer_name = max(peer_reviews, key=lambda r: r["decided_at"])["actor_name_snapshot"] if peer_reviews else ""
            if public:
                peer_name = _safe_public_text(peer_name, student_numbers)
            crops = []
            for crop in db.rows(conn, "evidence_crops", revision_id=revision["id"]):
                if public and crop["public_asset_status"] != "READY":
                    continue
                crops.append({"id": crop["id"], "evidence_type": crop["evidence_type"],
                              "url": f"/api/crops/{crop['id']}/public" if public else f"/api/crops/{crop['id']}/image"})
            grouped[application["category_id"]].append({"id": application["id"], "title": _safe_public_text(revision["title"], student_numbers) if public else revision["title"],
                                                        "score": str(db.money(revision["requested_score"])), "requested_score": str(db.money(revision["requested_score"])),
                                                        "reviewer_name": peer_name, "student_modified_score": revision["student_modified_score"],
                                                        "crops": crops, "counted": True,
                                                        "_group": current.get("competition_group_key"), "_facts": revision["payload_json"] or {}})
        category_results = []
        total = baseline
        for category in categories:
            items = grouped[category["id"]]
            groups = {}
            for item in items:
                group = item["_group"] or item["id"]
                if group not in groups or db.money(item["score"]) > db.money(groups[group]["score"]):
                    if group in groups:
                        groups[group]["counted"] = False
                    groups[group] = item
                else:
                    item["counted"] = False
            chosen = list(groups.values())
            is_post = category["code"].upper() in {"POST", "POSITION", "ORGANIZATION"} or category["aggregation_kind"].upper() in {"MAX", "HIGHEST", "MAXIMUM"}
            if is_post and chosen:
                highest = max(chosen, key=lambda i: db.money(i["score"]))
                for item in chosen:
                    item["counted"] = item is highest
                chosen = [highest]
            raw = sum((db.money(i["score"]) for i in chosen), Decimal("0"))
            if category["code"].upper() == "SERVICE":
                volunteers = [i for i in chosen if "volunteer_hours" in i["_facts"] or str(i["_facts"].get("subtype", "")).upper() in {"VOLUNTEER", "志愿服务", "志愿工时"}]
                if volunteers:
                    # Human-reviewed requested scores are authoritative. Hours/32
                    # informs the suggestion/review, never overwrites a student's
                    # accepted correction. Only the annual volunteer cap applies.
                    annual = min(sum((db.money(i["score"]) for i in volunteers), Decimal(0)), Decimal(1))
                    raw = raw - sum((db.money(i["score"]) for i in volunteers), Decimal(0)) + db.money(annual)
                    for item in volunteers:
                        item["aggregation_note"] = "志愿工时参考32小时1分；采用审核通过的申报分，年度该子项累计上限1分"
            cap = db.money(category["default_cap"])
            score = min(db.money(raw), cap)
            total += score
            for item in items:
                item.pop("_group", None)
                item.pop("_facts", None)
            category_results.append({"id": category["id"], "code": category["code"], "name": category["name"],
                                     "raw": str(db.money(raw)), "score": str(db.money(score)), "cap": str(cap), "items": items})
        member = {"student_id": profile["id"], "user_id": student["id"], "display_name": _safe_public_text(student["display_name"], student_numbers) if public else student["display_name"],
                  "baseline": str(baseline), "total": str(db.money(total)), "categories": category_results}
        if not public:
            member["student_number"] = profile["student_number"]
        members.append(member)
    members.sort(key=lambda m: (-Decimal(m["total"]), m["display_name"]))
    return {"class": class_, "year": year, "publicity": publication or {"is_open": False}, "members": members}


def build_scores(conn, user, class_id, year_id, public=False):
    if user["base_identity"] == "ADMIN":
        raise HTTPException(403, "系统管理员不查看业务成绩")
    _, _, class_year = _year_class(conn, class_id, year_id)
    if public:
        publication = db.one(conn, "class_publicity", class_year_id=class_year["id"])
        if not publication or not publication["is_open"]:
            raise HTTPException(403, "本班本学年尚未开启公示")
        require(conn, user, "publicity.read", class_id=class_id)
        if user["base_identity"] == "STUDENT":
            profile = db.one(conn, "student_profiles", user_id=user["id"])
            if not profile or not db.one(conn, "enrollments", student_id=profile["id"], class_year_id=class_year["id"], status="ACTIVE"):
                raise HTTPException(403, "只能查看本班本学年公示")
    else:
        require(conn, user, "scores.read", class_id=class_id)
    return class_scores(conn, class_id, year_id, public)


@router.get("/scores")
def get_scores(class_id: str, year_id: str, user=Depends(current_user)):
    with db.read() as conn:
        return build_scores(conn, user, class_id, year_id)


@router.get("/publicity")
def get_publicity(class_id: str, year_id: str, user=Depends(current_user)):
    with db.read() as conn:
        return build_scores(conn, user, class_id, year_id, public=True)


@router.put("/publicity")
def set_publicity(payload: dict = Body(...), user=Depends(current_user)):
    from .submissions import visible_application
    with db.write() as conn:
        class_id, year_id = payload.get("class_id"), payload.get("year_id")
        _, _, class_year = _year_class(conn, class_id, year_id)
        if user["base_identity"] != "COUNSELOR":
            raise HTTPException(403, "公示由所带班级辅导员开启或关闭")
        require(conn, user, "publicity.manage", class_id=class_id)
        is_open = payload.get("is_open")
        if not isinstance(is_open, bool):
            raise HTTPException(400, "公示开关必须为布尔值")
        enrollment_ids = {e["id"] for e in db.rows(conn, "enrollments", class_year_id=class_year["id"])}
        pending = 0
        for application in db.rows(conn, "applications"):
            if application["enrollment_id"] in enrollment_ids and visible_application(conn, application):
                current = _round(conn, application)
                pending += bool(current and current["state"] in PENDING)
        if is_open and pending:
            raise HTTPException(409, f"仍有{pending}条正式申请待审核，全部审核结束后才能开启公示")
        publication = db.one(conn, "class_publicity", class_year_id=class_year["id"])
        if not publication:
            publication = db.add(conn, "class_publicity", class_year_id=class_year["id"], is_open=False, lock_version=0)
        if publication["is_open"] == is_open:
            return publication
        values = {"is_open": is_open, "lock_version": publication["lock_version"] + 1}
        values.update({"opened_by": user["id"], "opened_at": db.now()} if is_open else {"closed_by": user["id"], "closed_at": db.now()})
        publication = db.change(conn, "class_publicity", publication["id"], **values)
        db.change(conn, "class_years", class_year["id"], lock_version=class_year["lock_version"] + 1, updated_at=db.now())
        events = db.rows(conn, "publicity_events", publicity_id=publication["id"])
        db.add(conn, "publicity_events", publicity_id=publication["id"], event_no=max((e["event_no"] for e in events), default=0) + 1,
               action="OPEN" if is_open else "CLOSE", actor_id=user["id"], pending_count_checked=pending,
               occurred_at=db.now(), note="建议公示不少于三个工作日，由辅导员掌握关闭时间")
        return publication


@router.get("/dashboard")
def dashboard(year_id: str | None = None, class_id: str | None = None, user=Depends(current_user)):
    from .submissions import visible_application
    with db.read() as conn:
        if user["base_identity"] == "ADMIN":
            return {"stats": {"users": len(db.rows(conn, "users")), "classes": len(db.rows(conn, "classes")),
                              "applications": 0, "pending": 0, "approved": 0, "total_score": "0.00", "baseline": "70.00"},
                    "category_scores": [], "recent": []}
        summaries = []
        for application in db.rows(conn, "applications"):
            if not visible_application(conn, application):
                continue
            context = app_context(conn, application["id"])
            if year_id and context["year"]["id"] != year_id or class_id and context["class"]["id"] != class_id:
                continue
            if user["base_identity"] == "STUDENT" and context["user"]["id"] != user["id"]:
                continue
            if allowed(conn, user, "application.read", class_id=context["class"]["id"], owner_id=context["user"]["id"]):
                summaries.append(_summary(conn, application, context))
        category_scores, total = [], Decimal("70")
        if user["base_identity"] == "STUDENT" and year_id:
            try:
                enrollment = student_enrollment(conn, user, year_id)
                context = class_context(conn, enrollment["id"])
                result = class_scores(conn, context["class"]["id"], year_id)
                mine = next((m for m in result["members"] if m["user_id"] == user["id"]), None)
                if mine:
                    category_scores = [{"id": c["id"], "name": c["name"], "score": c["score"], "cap": c["cap"]} for c in mine["categories"]]
                    total = Decimal(mine["total"])
            except HTTPException:
                pass
        elif user["base_identity"] == "COUNSELOR" and year_id and class_id:
            require(conn, user, "scores.read", class_id=class_id)
            result = class_scores(conn, class_id, year_id)
            members = result["members"]
            if members:
                total = sum((Decimal(m["total"]) for m in members), Decimal(0)) / len(members)
                category_scores = [{"id": c["id"], "name": c["name"], "cap": c["cap"],
                                    "score": str(db.money(sum((Decimal(m["categories"][index]["score"]) for m in members), Decimal(0)) / len(members)))}
                                   for index, c in enumerate(members[0]["categories"])]
        return {"stats": {"applications": len(summaries), "pending": sum(s["state"] in PENDING for s in summaries),
                          "approved": sum(s["state"] == "APPROVED" for s in summaries), "total_score": str(db.money(total)), "baseline": "70.00"},
                "category_scores": category_scores, "recent": sorted(summaries, key=lambda s: s["updated_at"], reverse=True)[:6]}
