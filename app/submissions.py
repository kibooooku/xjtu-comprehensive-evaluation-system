"""One category container per student/year, independently reviewed child items."""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException

from . import db
from .migrations import active_year, assert_current_year
from .security import allowed, class_context, current_user, require, student_enrollment

router = APIRouter(prefix="/api", tags=["category submissions"])


def item_record(conn, application_id):
    return db.one(conn, "category_items", application_id=application_id)


def visible_application(conn, application):
    if application.get("archived_at"):
        return False
    item = item_record(conn, application["id"])
    # Legacy records are visible until initialize_v2 groups them. A new staged
    # record always has an item row and stays hidden until explicit confirmation.
    return not item or bool(item.get("confirmed_at") and not item.get("deleted_at"))


def assert_confirmed(conn, application_id):
    item = item_record(conn, application_id)
    if item and (not item.get("confirmed_at") or item.get("deleted_at")):
        raise HTTPException(409, "请先在上传整理窗口确认材料，再进行识别或正式提交")


def assert_unique_files(conn, application_id, file_ids_or_hashes):
    """Reject identical bytes owned by another confirmed child of the same parent.

    File names and global file identity are deliberately irrelevant. Historical
    links reserve a proof while its child remains active; deleting a rejected
    child releases that reservation without destroying its audit trail.
    """
    from .migrations import ensure_group
    application = db.one(conn, "applications", application_id)
    if not application or application.get("archived_at"):
        raise HTTPException(404, "申请小项不存在")
    parent, _ = ensure_group(conn, application, confirmed=False)
    hashes = set()
    for value in file_ids_or_hashes:
        if not isinstance(value, str):
            raise HTTPException(400, "文件标识格式无效")
        file = db.one(conn, "file_objects", value)
        if file:
            hashes.add(file["sha256"].lower())
        elif re.fullmatch(r"[0-9a-fA-F]{64}", value):
            hashes.add(value.lower())
        else:
            raise HTTPException(400, "文件不存在或摘要格式无效")
    if not hashes:
        return
    for sibling in db.rows(conn, "category_items", submission_id=parent["id"]):
        if sibling["application_id"] == application_id or sibling.get("deleted_at") or not sibling.get("confirmed_at"):
            continue
        other = db.one(conn, "applications", sibling["application_id"])
        if not other or other.get("archived_at"):
            continue
        for revision in db.rows(conn, "application_revisions", application_id=other["id"]):
            for link in db.rows(conn, "revision_files", revision_id=revision["id"]):
                file = db.one(conn, "file_objects", link["file_id"])
                if file and file["sha256"].lower() in hashes:
                    raise HTTPException(409, {"message": "该文件已存在于本学年同一大类的其他小项中，改名后也不能重复上传",
                                              "existing_item_id": sibling["id"], "application_id": other["id"]})


def _context(conn, submission_id):
    submission = db.one(conn, "category_submissions", submission_id)
    if not submission:
        raise HTTPException(404, "大类申报不存在")
    context = class_context(conn, submission["enrollment_id"])
    context.update(submission=submission, category=db.one(conn, "score_categories", submission["category_id"]))
    return context


def _readable(conn, user, context):
    if user["base_identity"] == "ADMIN":
        raise HTTPException(403, "系统管理员不读取业务申报与证明材料")
    require(conn, user, "application.read", class_id=context["class"]["id"], owner_id=context["user"]["id"])


def _writable(conn, user, context):
    if user["base_identity"] != "STUDENT" or user["id"] != context["user"]["id"]:
        raise HTTPException(403, "只能整理本人的申报小项")
    require(conn, user, "application.write", class_id=context["class"]["id"], owner_id=user["id"])
    assert_current_year(conn, context["year"]["id"])


def _job_status(conn, application_id):
    from .jobs import job_view
    jobs = sorted(db.rows(conn, "background_jobs", application_id=application_id), key=lambda j: j["created_at"])
    latest = jobs[-1] if jobs else None
    ocr = next((j for j in reversed(jobs) if j["kind"] == "OCR"), None)
    ai = next((j for j in reversed(jobs) if j["kind"] == "AI"), None)
    analyses = sorted(db.rows(conn, "ai_analyses", application_id=application_id), key=lambda a: a["created_at"])
    return {"analysis_status": ai["state"] if ai else (analyses[-1]["status"] if analyses else "UNANALYZED"),
            "ocr_status": ocr["state"] if ocr else "UNANALYZED", "latest_job": job_view(latest) if latest else None}


def _evidence_summary(conn, revision):
    """Live booleans across attachments; never infer manual work from old jobs."""
    from .materials import crop_view
    summary = {"has_participation": False, "has_activity_name": False,
               "has_activity_time": False, "has_seal": False}
    keys = {"NAME": "has_activity_name", "TIME": "has_activity_time", "SEAL": "has_seal"}
    for crop in db.rows(conn, "evidence_crops", revision_id=revision["id"]):
        view = crop_view(crop, conn=conn)
        if view.get("source") == "AI" and view.get("automatic_policy_allowed") is False:
            continue
        if view["evidence_type"] == "PARTICIPATION" and view.get("participation_role") in (None, "", "PRIMARY"):
            summary["has_participation"] = True
        elif view["evidence_type"] == "VALIDITY":
            kind = view.get("validity_kind")
            if not kind and crop.get("recognition_region_id"):
                region = db.one(conn, "recognition_regions", crop["recognition_region_id"])
                kind = "SEAL" if region and region.get("kind") == "SEAL" else None
            if kind in keys:
                summary[keys[kind]] = True
    summary["complete"] = all(summary.values())
    return summary


def _group_view(conn, context, user, detail=False, include_deleted=False, include_staged=False):
    from .workflow import _display_revision, _summary, application_detail, class_scores
    _readable(conn, user, context)
    entries = []
    for item in sorted(db.rows(conn, "category_items", submission_id=context["submission"]["id"]), key=lambda i: i["sort_order"]):
        application = db.one(conn, "applications", item["application_id"])
        if not application:
            continue
        deleted = bool(item.get("deleted_at") or application.get("archived_at"))
        if deleted and not include_deleted:
            continue
        if not item.get("confirmed_at") and not (include_staged and user["id"] == context["user"]["id"]):
            continue
        app_context = {**context, "application": application}
        entry = application_detail(conn, application, user, include_deleted=deleted) if detail else _summary(conn, application, app_context)
        revision = _display_revision(conn, application)
        if not detail:
            files = [db.one(conn, "file_objects", link["file_id"]) for link in
                     sorted(db.rows(conn, "revision_files", revision_id=revision["id"]), key=lambda r: r["sort_order"])]
            entry.update(file_count=len(files), primary_file_name=files[0]["original_name"] if files else "",
                         files=[{"id": f["id"], "original_name": f["original_name"]} for f in files])
        entry.update(possible_duplicates=[], confirmed_at=item["confirmed_at"], deleted_at=item["deleted_at"], item_id=item["id"], sort_order=item["sort_order"],
                     evidence_summary=_evidence_summary(conn, revision),
                     **_job_status(conn, application["id"]))
        entries.append(entry)
    active = [e for e in entries if e["confirmed_at"] and not e["deleted_at"]]
    counts = dict(Counter(e["state"] for e in active))
    state = next(iter(counts)) if len(counts) == 1 else "PARTIAL" if counts else "EMPTY"
    requested_total = sum((db.money(e["requested_score"]) for e in active), Decimal(0))
    approved_raw = sum((db.money(e["requested_score"]) for e in active if e["state"] == "APPROVED"), Decimal(0))
    approved_total = Decimal(0)
    if approved_raw:
        scores = class_scores(conn, context["class"]["id"], context["year"]["id"])
        member = next((m for m in scores["members"] if m["user_id"] == context["user"]["id"]), None)
        category_score = next((c for c in member["categories"] if c["id"] == context["category"]["id"]), None) if member else None
        approved_total = Decimal(category_score["score"]) if category_score else Decimal(0)
    return {"id": context["submission"]["id"], "category_id": context["category"]["id"],
            "category_name": context["category"]["name"], "category_code": context["category"]["code"],
            "year_id": context["year"]["id"], "year_name": context["year"]["name"],
            "class_id": context["class"]["id"], "class_name": context["class"]["name"],
            "student_user_id": context["user"]["id"], "student_name": context["user"]["display_name"],
            "item_count": len(active), "counts": counts, "state": state,
            "requested_total": str(db.money(requested_total)), "approved_total": str(db.money(approved_total)),
            "raw_approved_total": str(db.money(approved_raw)), "items": entries,
            "created_at": context["submission"]["created_at"], "updated_at": context["submission"]["updated_at"],
            "can_edit": bool(user["id"] == context["user"]["id"] and active_year(conn)["id"] == context["year"]["id"])}


@router.get("/submissions")
def list_submissions(year_id: str | None = None, class_id: str | None = None,
                     scope: str = "mine", state: str | None = None, include_deleted: bool = False, user=Depends(current_user)):
    with db.read() as conn:
        if user["base_identity"] == "ADMIN":
            raise HTTPException(403, "系统管理员不查看业务申报")
        result = []
        for submission in db.rows(conn, "category_submissions"):
            context = _context(conn, submission["id"])
            if year_id and context["year"]["id"] != year_id or class_id and context["class"]["id"] != class_id:
                continue
            if scope == "mine" and context["user"]["id"] != user["id"]:
                continue
            if not allowed(conn, user, "application.read", class_id=context["class"]["id"], owner_id=context["user"]["id"]):
                continue
            group = _group_view(conn, context, user, include_deleted=include_deleted)
            if not group["items"]:
                continue
            if scope == "review":
                pending = [i for i in group["items"] if i["state"] in {"PENDING_PEER", "PENDING_COUNSELOR"} and
                           allowed(conn, user, "review.peer" if i["state"] == "PENDING_PEER" else "review.counselor",
                                   class_id=context["class"]["id"], owner_id=context["user"]["id"])]
                if not pending:
                    continue
            if state and state != "ALL" and state != group["state"] and not group["counts"].get(state):
                continue
            result.append(group)
        return sorted(result, key=lambda g: g["updated_at"], reverse=True)


@router.post("/submissions")
def create_submission(payload: dict = Body(...), user=Depends(current_user)):
    with db.write() as conn:
        if user["base_identity"] != "STUDENT":
            raise HTTPException(403, "只有学生可以填报")
        year = active_year(conn)
        if payload.get("year_id") and payload["year_id"] != year["id"]:
            assert_current_year(conn, payload["year_id"])
        category = db.one(conn, "score_categories", payload.get("category_id"))
        if not category:
            raise HTTPException(400, "请选择有效的大类入口")
        enrollment = student_enrollment(conn, user, year["id"])
        context = class_context(conn, enrollment["id"])
        require(conn, user, "application.write", class_id=context["class"]["id"], owner_id=user["id"])
        submission = db.one(conn, "category_submissions", enrollment_id=enrollment["id"], category_id=category["id"])
        if not submission:
            submission = db.add(conn, "category_submissions", enrollment_id=enrollment["id"], category_id=category["id"])
        return _group_view(conn, _context(conn, submission["id"]), user, detail=True)


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: str, include_deleted: bool = False, include_staged: bool = False, user=Depends(current_user)):
    with db.read() as conn:
        return _group_view(conn, _context(conn, submission_id), user, detail=True,
                           include_deleted=include_deleted, include_staged=include_staged)


@router.post("/submissions/{submission_id}/items")
def create_item(submission_id: str, payload: dict = Body(default={}), user=Depends(current_user)):
    from .workflow import new_application
    with db.write() as conn:
        context = _context(conn, submission_id)
        _writable(conn, user, context)
        staged = payload.get("staged", True)
        if not isinstance(staged, bool):
            raise HTTPException(400, "暂存标记格式无效")
        application = new_application(conn, user, context["category"]["id"], context["year"]["id"], confirmed=not staged)
        db.change(conn, "category_submissions", submission_id, updated_at=db.now())
        from .workflow import application_detail
        return application_detail(conn, application, user)


def _ids(payload):
    ids = payload.get("item_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(i, str) or not i for i in ids):
        raise HTTPException(400, "请选择1至100个有效小项")
    return list(dict.fromkeys(ids))


@router.post("/submissions/{submission_id}/confirm-upload")
def confirm_upload(submission_id: str, payload: dict = Body(...), user=Depends(current_user)):
    from .workflow import _round
    ids = _ids(payload)
    with db.write() as conn:
        context = _context(conn, submission_id)
        _writable(conn, user, context)
        batch_hashes = {}
        for application_id in ids:
            item = db.one(conn, "category_items", submission_id=submission_id, application_id=application_id)
            application = db.one(conn, "applications", application_id)
            if not item or item.get("deleted_at") or not application or application.get("archived_at"):
                raise HTTPException(400, "所选小项不属于当前大类或已经删除")
            current = _round(conn, application)
            if current and current["state"] in {"PENDING_PEER", "PENDING_COUNSELOR", "APPROVED"}:
                raise HTTPException(409, "待审或已批准小项不能重新确认上传")
            files = db.rows(conn, "revision_files", revision_id=application["working_revision_id"])
            if not files:
                raise HTTPException(400, "每个确认的小项至少需要一个原文件")
            for attachment in files:
                file = db.one(conn, "file_objects", attachment["file_id"])
                digest = file["sha256"].lower()
                if digest in batch_hashes and batch_hashes[digest] != application_id:
                    raise HTTPException(409, {"message": "本批上传中有内容完全相同的文件，不能在同一大类重复生成小项",
                                              "application_id": batch_hashes[digest], "duplicate_application_id": application_id})
                batch_hashes[digest] = application_id
            assert_unique_files(conn, application_id, [f["file_id"] for f in files])
        for application_id in ids:
            item = item_record(conn, application_id)
            if not item["confirmed_at"]:
                db.change(conn, "category_items", item["id"], confirmed_at=db.now())
        db.change(conn, "category_submissions", submission_id, updated_at=db.now())
    # Queue only after committing; enqueue owns its transaction and durable worker.
    from .jobs import enqueue
    jobs, errors = [], []
    for application_id in ids:
        try:
            jobs.append(enqueue(application_id, user, "OCR"))
        except HTTPException as error:
            errors.append({"id": application_id, "detail": error.detail})
    with db.read() as conn:
        return {"submission": _group_view(conn, _context(conn, submission_id), user, detail=True),
                "confirmed_item_ids": ids, "jobs": jobs, "ocr_errors": errors}


@router.post("/submissions/{submission_id}/submit")
def submit_items(submission_id: str, payload: dict = Body(...), user=Depends(current_user)):
    from .workflow import submit_application
    ids = _ids(payload)
    with db.read() as conn:
        context = _context(conn, submission_id)
        _writable(conn, user, context)
        if any(not db.one(conn, "category_items", submission_id=submission_id, application_id=i) for i in ids):
            raise HTTPException(400, "所选小项不属于当前大类")
    results = []
    versions = payload.get('expected_versions') or {}
    if not isinstance(versions, dict):
        raise HTTPException(400, '提交版本格式无效')
    for application_id in ids:
        try:
            body = {'score_confirmed': True}
            if application_id in versions:
                body['expected_version'] = versions[application_id]
            result = submit_application(application_id, body, user)
            results.append({"id": application_id, "ok": True, "state": result["state"]})
        except HTTPException as error:
            results.append({"id": application_id, "ok": False, "status_code": error.status_code, "detail": error.detail})
    return {"results": results, "submitted": sum(r["ok"] for r in results)}


@router.delete("/submissions/{submission_id}/items/{application_id}")
def delete_item(submission_id: str, application_id: str, user=Depends(current_user)):
    from .workflow import _event, _round
    with db.write() as conn:
        context = _context(conn, submission_id)
        _writable(conn, user, context)
        item = db.one(conn, "category_items", submission_id=submission_id, application_id=application_id)
        application = db.one(conn, "applications", application_id)
        if not item or not application:
            raise HTTPException(404, "小项不存在")
        if item.get("deleted_at"):
            return {"ok": True}
        current = _round(conn, application)
        if current and current["state"] not in {"REJECTED", "WITHDRAWN"}:
            raise HTTPException(409, "只能删除草稿、已驳回或已撤回小项；待审先撤回，已批准不能删除")
        _event(conn, application, "ITEM_DELETED", user, detail={"submission_id": submission_id})
        instant = db.now()
        db.change(conn, "category_items", item["id"], deleted_at=instant)
        db.change(conn, "applications", application_id, archived_at=instant, lock_version=application["lock_version"] + 1)
        db.change(conn, "category_submissions", submission_id, updated_at=instant)
        return {"ok": True, "message": "已删除小项，材料与审核历史保留"}
