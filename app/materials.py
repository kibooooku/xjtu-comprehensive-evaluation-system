"""Protected evidence storage, OCR orchestration and the original 15-column export."""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pypdfium2 as pdfium
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from sqlalchemy import and_, delete, func, select

from . import db
from .db import add, change, money, now, one, rows, tables, uid
from .integrations import ProviderError, configuration, deepseek_suggestion, tencent_ocr
from .security import current_user, require

router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 60
MAX_PAGE_PIXELS = 36_000_000
REDACTION_VERSION = "student-number-v1"
PDF_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
Image.MAX_IMAGE_PIXELS = MAX_PAGE_PIXELS


def asset_path(relative: str) -> Path:
    root = Path(db.storage_root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(404, "文件不存在。")
    return path


def _write_asset(relative: str, data: bytes) -> Path:
    path = asset_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def render_upload(content: bytes, filename: str) -> tuple[str, list[bytes]]:
    """Decode actual file contents, never trust filename or client MIME type."""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".jpg", ".jpeg", ".png"):
        raise HTTPException(400, "仅支持 PDF、JPG、JPEG、PNG 文件。")
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "每个文件需大于0字节且不超过20MB。")
    pages = []
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "文件内容不是有效PDF。")
        try:
            with PDF_LOCK:
                document = pdfium.PdfDocument(content)
                try:
                    if not 1 <= len(document) <= MAX_PAGES:
                        raise HTTPException(400, f"PDF需包含1至{MAX_PAGES}页。")
                    for index in range(len(document)):
                        page = document[index]
                        width, height = page.get_size()
                        if width <= 0 or height <= 0:
                            raise HTTPException(400, "PDF页面尺寸无效。")
                        scale = min(2.0, 3600 / max(width, height))
                        bitmap = page.render(scale=scale)
                        image = bitmap.to_pil().convert("RGB")
                        pages.append(_png(image))
                        bitmap.close()
                        page.close()
                finally:
                    document.close()
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "PDF无法解析，可能已损坏或有密码保护。") from None
        return "application/pdf", pages
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in ("JPEG", "PNG"):
                raise HTTPException(400, "图片内容与支持的格式不符。")
            if (suffix == ".png") != (image.format == "PNG"):
                raise HTTPException(400, "图片扩展名与实际内容不符。")
            if image.width * image.height > MAX_PAGE_PIXELS:
                raise HTTPException(400, "图片分辨率过大，请压缩后上传。")
            mime = "image/png" if image.format == "PNG" else "image/jpeg"
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            if normalized.width < 10 or normalized.height < 10:
                raise HTTPException(400, "图片尺寸过小。")
            pages.append(_png(normalized))
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "图片无法解码或尺寸过大。") from None
    return mime, pages


def file_view(conn, record: dict) -> dict:
    return {
        "id": record["id"], "original_name": record["original_name"],
        "mime_type": record["mime_type"], "byte_size": record["byte_size"],
        "url": f"/api/files/{record['id']}/original",
        "pages": [{"id": page["id"], "page_index": page["page_index"], "width_px": page["width_px"], "height_px": page["height_px"], "url": f"/api/pages/{page['id']}/image"} for page in sorted(rows(conn, "file_pages", file_id=record["id"]), key=lambda p: p["page_index"])],
    }


def polygon_box(polygon) -> list[int]:
    if isinstance(polygon, dict):
        return list(polygon.get("box", [0, 0, 0, 0]))
    if not polygon:
        return [0, 0, 0, 0]
    points = [(p.get("X", p.get("x", 0)), p.get("Y", p.get("y", 0))) if isinstance(p, dict) else tuple(p) for p in polygon]
    x1, y1 = min(p[0] for p in points), min(p[1] for p in points)
    x2, y2 = max(p[0] for p in points), max(p[1] for p in points)
    return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]


def crop_view(record: dict, public: bool = False) -> dict:
    ready = record.get("public_asset_status") == "READY"
    result = {"id": record["id"], "page_id": record["page_id"], "evidence_type": record["evidence_type"], "public_asset_status": record.get("public_asset_status"), "public_url": f"/api/crops/{record['id']}/public" if ready else None}
    if not public:
        result.update({"source": record["source"], "box": polygon_box(record["polygon_json"]), "url": f"/api/crops/{record['id']}/image", "masks": (record.get("masks_json") or {}).get("manual", [])})
    return result


def _app_context(conn, application_id: str) -> dict:
    from .workflow import app_context
    return app_context(conn, application_id)


def _editable(conn, application_id: str, user: dict) -> tuple[dict, dict]:
    from .workflow import editable_revision
    context = _app_context(conn, application_id)
    context["previous_revision_id"] = context["application"].get("working_revision_id")
    require(conn, user, "application.write", class_id=context["class"]["id"], owner_id=context["user"]["id"])
    revision = editable_revision(conn, context["application"], user)
    return context, revision


def _can_read_file(conn, file_id: str, user: dict) -> dict:
    record = one(conn, "file_objects", file_id)
    if not record:
        raise HTTPException(404, "文件不存在。")
    if user["base_identity"] == "ADMIN":
        raise HTTPException(403, "管理员不能查看业务证明材料。")
    links = rows(conn, "revision_files", file_id=file_id)
    for link in links:
        revision = one(conn, "application_revisions", link["revision_id"])
        if not revision:
            continue
        context = _app_context(conn, revision["application_id"])
        try:
            require(conn, user, "materials.read", class_id=context["class"]["id"], owner_id=context["user"]["id"])
            return record
        except HTTPException as error:
            if error.status_code != 403:
                raise
    raise HTTPException(403, "你没有查看此材料的权限。")


def _private_response(relative: str, media_type: str = "image/png", filename: str | None = None):
    path = asset_path(relative)
    if not path.is_file():
        raise HTTPException(404, "文件不存在。")
    return FileResponse(path, media_type=media_type, filename=filename, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})


@router.post("/applications/{application_id}/files")
def upload_file(application_id: str, file: UploadFile = File(...), user: dict = Depends(current_user)):
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    filename = Path((file.filename or "证明材料").replace("\\", "/")).name[:240]
    mime, rendered = render_upload(content, filename)
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        attached = rows(conn, "revision_files", revision_id=revision["id"])
        if len(attached) >= 12:
            raise HTTPException(400, "每条申请最多12份附件。")
        file_id = uid()
        original_uri = f"originals/{file_id}{Path(filename).suffix.lower()}"
        _write_asset(original_uri, content)
        record = add(conn, "file_objects", id=file_id, uploaded_by=user["id"], original_name=filename, mime_type=mime, byte_size=len(content), sha256=hashlib.sha256(content).hexdigest(), storage_uri=original_uri)
        for index, page_bytes in enumerate(rendered):
            page_id = uid()
            uri = f"pages/{page_id}.png"
            _write_asset(uri, page_bytes)
            with Image.open(io.BytesIO(page_bytes)) as image:
                width, height = image.size
            add(conn, "file_pages", id=page_id, file_id=file_id, page_index=index + 1, render_version="pdfium-pillow-v1", width_px=width, height_px=height, rotation_degrees=0, image_uri=uri, render_sha256=hashlib.sha256(page_bytes).hexdigest())
        add(conn, "revision_files", revision_id=revision["id"], file_id=file_id, sort_order=len(attached), purpose="SUPPORT", added_by=user["id"], added_at=now())
        _touch(conn, context["application"])
        return file_view(conn, record)


def _touch(conn, application: dict):
    current = one(conn, "applications", application["id"])
    change(conn, "applications", application["id"], lock_version=(current.get("lock_version") or 0) + 1)


@router.delete("/applications/{application_id}/files/{file_id}")
def remove_file(application_id: str, file_id: str, user: dict = Depends(current_user)):
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        link = one(conn, "revision_files", revision_id=revision["id"], file_id=file_id)
        if not link:
            raise HTTPException(404, "附件不存在。")
        page_ids = [p["id"] for p in rows(conn, "file_pages", file_id=file_id)]
        if page_ids:
            conn.execute(delete(tables["evidence_crops"]).where(and_(tables["evidence_crops"].c.revision_id == revision["id"], tables["evidence_crops"].c.page_id.in_(page_ids))))
        conn.execute(delete(tables["revision_files"]).where(tables["revision_files"].c.id == link["id"]))
        _touch(conn, context["application"])
    return {"ok": True}


@router.get("/files/{file_id}/original")
def get_original(file_id: str, user: dict = Depends(current_user)):
    with db.read() as conn:
        record = _can_read_file(conn, file_id, user)
    return _private_response(record["storage_uri"], record["mime_type"], record["original_name"])


@router.get("/pages/{page_id}/image")
def get_page(page_id: str, user: dict = Depends(current_user)):
    with db.read() as conn:
        page = one(conn, "file_pages", page_id)
        if not page:
            raise HTTPException(404, "页面不存在。")
        _can_read_file(conn, page["file_id"], user)
    return _private_response(page["image_uri"])


def checked_box(value, width: int, height: int) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise HTTPException(400, "截图范围应为[x,y,width,height]。")
    try:
        x, y, w, h = [float(n) for n in value]
    except (ValueError, TypeError):
        raise HTTPException(400, "截图坐标无效。") from None
    if not all(math.isfinite(n) for n in (x, y, w, h)) or w < 2 or h < 2:
        raise HTTPException(400, "截图范围无效或过小。")
    if x < 0 or y < 0 or x + w > width + 1 or y + h > height + 1:
        raise HTTPException(400, "截图范围超出原图。")
    x1, y1, x2, y2 = max(0, int(x)), max(0, int(y)), min(width, math.ceil(x + w)), min(height, math.ceil(y + h))
    return [x1, y1, x2 - x1, y2 - y1]


def _polygon(box: list[int]) -> list[list[int]]:
    x, y, w, h = box
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _intersection(a: list, b: list) -> list | None:
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return [x, y, right - x, bottom - y] if right > x and bottom > y else None


def _build_public_crop(conn, crop: dict, page: dict, manual_masks: list) -> dict:
    """Fail closed without a successful text run; never substitute a private crop."""
    runs = [r for r in rows(conn, "recognition_runs", page_id=page["id"]) if r["api_name"] == "GeneralAccurateOCR" and r["status"] == "SUCCEEDED"]
    if not runs:
        return {"public_asset_status": "BLOCKED", "public_asset_uri": None, "redaction_version": None, "masks_json": {"manual": manual_masks, "automatic": [], "reason": "需要成功的文字OCR定位学号后才能生成公示截图"}}
    run = sorted(runs, key=lambda item: item.get("finished_at") or "")[-1]
    regions = rows(conn, "recognition_regions", run_id=run["id"])
    if not any((region.get("text") or "").strip() for region in regions):
        return {"public_asset_status": "BLOCKED", "public_asset_uri": None, "redaction_version": None, "masks_json": {"manual": manual_masks, "automatic": [], "reason": "文字OCR未检测到可靠文本，未生成公示截图"}}
    automatic = []
    for region in regions:
        text = re.sub(r"\s+", "", region.get("text") or "")
        # Mask every long numeric identifier, including OCR-separated digits and the whole 学号 row.
        if re.search(r"[0-9０-９]{6,}", text) or "学号" in text or (region.get("confidence") is not None and Decimal(str(region["confidence"])) < Decimal("0.85")):
            box = polygon_box(region["polygon_json"])
            box = [max(0, box[0] - 5), max(0, box[1] - 4), box[2] + 10, box[3] + 8]
            automatic.append(box)
    box = polygon_box(crop["polygon_json"])
    with Image.open(asset_path(crop["private_asset_uri"])) as original:
        image = original.convert("RGB")
    draw = ImageDraw.Draw(image)
    for mask in manual_masks + automatic:
        overlap = _intersection(box, mask)
        if overlap:
            x, y, w, h = overlap
            draw.rectangle((x - box[0], y - box[1], x - box[0] + w, y - box[1] + h), fill="#253244")
    public_uri = f"public-crops/{crop['id']}-{REDACTION_VERSION}.png"
    _write_asset(public_uri, _png(image))
    return {"public_asset_status": "READY", "public_asset_uri": public_uri, "redaction_version": REDACTION_VERSION, "masks_json": {"manual": manual_masks, "automatic": automatic, "ocr_run_id": run["id"], "page_hash": page["render_sha256"]}}


def create_crop(conn, revision: dict, page: dict, evidence_type: str, box: list, manual_masks: list | None = None, source: str = "STUDENT", region_id: str | None = None) -> dict:
    box = checked_box(box, page["width_px"], page["height_px"])
    manual_masks = manual_masks or []
    if len(manual_masks) > 100:
        raise HTTPException(400, "手动遮挡区域过多。")
    manual_masks = [checked_box(mask, page["width_px"], page["height_px"]) for mask in manual_masks]
    with Image.open(asset_path(page["image_uri"])) as image:
        x, y, w, h = box
        content = _png(image.convert("RGB").crop((x, y, x + w, y + h)))
    crop_id = uid()
    uri = f"private-crops/{crop_id}.png"
    _write_asset(uri, content)
    existing = rows(conn, "evidence_crops", revision_id=revision["id"])
    crop = add(conn, "evidence_crops", id=crop_id, revision_id=revision["id"], page_id=page["id"], evidence_type=evidence_type, source=source, recognition_region_id=region_id, polygon_json=_polygon(box), private_asset_uri=uri, source_hash=hashlib.sha256(content).hexdigest(), redaction_version=None, masks_json={"manual": manual_masks}, public_asset_uri=None, public_asset_status="BLOCKED", sort_order=len(existing))
    return change(conn, "evidence_crops", crop_id, **_build_public_crop(conn, crop, page, manual_masks))


@router.post("/applications/{application_id}/crops")
def add_crop(application_id: str, body: dict, user: dict = Depends(current_user)):
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        if len(rows(conn, "evidence_crops", revision_id=revision["id"])) >= 30:
            raise HTTPException(400, "每条申请最多30张证据截图。")
        if not isinstance(body.get("page_id"), str) or not body["page_id"]:
            raise HTTPException(400, "请选择截图页面。")
        page = one(conn, "file_pages", body["page_id"])
        if not page or not one(conn, "revision_files", revision_id=revision["id"], file_id=page["file_id"]):
            raise HTTPException(400, "截图页面不属于此版本附件。")
        kind = str(body.get("evidence_type", ""))
        if kind not in ("VALIDITY", "PARTICIPATION"):
            raise HTTPException(400, "请选择有效性证明或本人参与证明。")
        crop = create_crop(conn, revision, page, kind, body.get("box"), body.get("masks", []))
        _touch(conn, context["application"])
        return crop_view(crop)


@router.delete("/applications/{application_id}/crops/{crop_id}")
def remove_crop(application_id: str, crop_id: str, user: dict = Depends(current_user)):
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        crop = one(conn, "evidence_crops", crop_id)
        if crop and crop["revision_id"] != revision["id"] and crop["revision_id"] == context.get("previous_revision_id"):
            # The first edit of a rejected frozen version clones attachments and crops.
            # Resolve the displayed old id to that exact clone, never delete history.
            crop = next((candidate for candidate in rows(conn, "evidence_crops", revision_id=revision["id"])
                         if all(candidate.get(key) == crop.get(key) for key in ("page_id", "evidence_type", "source_hash", "polygon_json", "sort_order"))), None)
        if not crop or crop["revision_id"] != revision["id"]:
            raise HTTPException(404, "截图不属于当前可编辑版本。")
        conn.execute(delete(tables["evidence_crops"]).where(tables["evidence_crops"].c.id == crop["id"]))
        _touch(conn, context["application"])
    return {"ok": True}


@router.get("/crops/{crop_id}/image")
def get_crop(crop_id: str, user: dict = Depends(current_user)):
    with db.read() as conn:
        crop = one(conn, "evidence_crops", crop_id)
        if not crop:
            raise HTTPException(404, "截图不存在。")
        page = one(conn, "file_pages", crop["page_id"])
        _can_read_file(conn, page["file_id"], user)
    return _private_response(crop["private_asset_uri"])


@router.get("/crops/{crop_id}/public")
def get_public_crop(crop_id: str, user: dict = Depends(current_user)):
    with db.read() as conn:
        crop = one(conn, "evidence_crops", crop_id)
        if not crop or crop.get("public_asset_status") != "READY" or crop.get("redaction_version") != REDACTION_VERSION:
            raise HTTPException(404, "该截图尚未生成安全的公示版本。")
        revision = one(conn, "application_revisions", crop["revision_id"])
        context = _app_context(conn, revision["application_id"])
        current_round = one(conn, "submission_rounds", context["application"].get("current_round_id")) if context["application"].get("current_round_id") else None
        publicity = one(conn, "class_publicity", class_year_id=context["class_year"]["id"])
        if not publicity or not publicity["is_open"] or not current_round or current_round["state"] != "APPROVED" or current_round["revision_id"] != revision["id"]:
            raise HTTPException(403, "此截图当前未公开。")
        require(conn, user, "publicity.read", class_id=context["class"]["id"], owner_id=context["user"]["id"])
        if user["base_identity"] == "STUDENT":
            profile = one(conn, "student_profiles", user_id=user["id"])
            if not profile or not one(conn, "enrollments", student_id=profile["id"], class_year_id=context["class_year"]["id"], status="ACTIVE"):
                raise HTTPException(403, "只能查看本班本学年的公示截图。")
        page = one(conn, "file_pages", crop["page_id"])
        meta = crop.get("masks_json") or {}
        if meta.get("page_hash") != page["render_sha256"]:
            raise HTTPException(404, "公示截图来源校验失败。")
        if not asset_path(crop["private_asset_uri"]).is_file() or hashlib.sha256(asset_path(crop["private_asset_uri"]).read_bytes()).hexdigest() != crop["source_hash"]:
            raise HTTPException(404, "公示截图来源校验失败。")
    return _private_response(crop["public_asset_uri"])


def revision_fingerprint(conn, revision: dict) -> str:
    attachments = sorted(rows(conn, "revision_files", revision_id=revision["id"]), key=lambda r: (r["sort_order"], r["id"]))
    crops = sorted(rows(conn, "evidence_crops", revision_id=revision["id"]), key=lambda c: c["id"])
    body = {"revision_id": revision["id"], "title": revision["title"], "score": str(revision["requested_score"]), "activity_id": revision.get("activity_id"), "payload": revision.get("payload_json") or {}, "files": [link["file_id"] for link in attachments], "crops": [{"id": c["id"], "hash": c["source_hash"], "box": c["polygon_json"], "manual_masks": (c.get("masks_json") or {}).get("manual", [])} for c in crops]}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _usage(conn, action: str) -> int:
    month = now()[:7]
    table = tables["recognition_runs"]
    return conn.scalar(select(func.count()).select_from(table).where(and_(table.c.api_name == action, table.c.started_at >= month + "-01"))) or 0


@router.get("/integrations/status")
def integration_status(user: dict = Depends(current_user)):
    config = configuration()
    with db.read() as conn:
        general_used, seal_used = _usage(conn, "GeneralAccurateOCR"), _usage(conn, "SealOCR")
    return {"enabled": config["enabled"], "tencent_configured": bool(config["secret_id"] and config["secret_key"]), "deepseek_configured": bool(config["deepseek_key"]), "deepseek_model": config["deepseek_model"], "free_only": True, "general": {"used": general_used, "limit": config["general_limit"]}, "seal": {"used": seal_used, "limit": config["seal_limit"]}, "max_analysis_pages": config["max_analysis_pages"], "message": "仅调用已开通服务，不自动重试或购买资源；本地预算不代表云端共享余额。额度不足时可手动填写和截图。"}


def _ocr_page(page: dict, action: str, config: dict) -> dict:
    param_hash = hashlib.sha256((page["render_sha256"] + action + "v1").encode()).hexdigest()
    with db.write() as conn:
        cached = [r for r in rows(conn, "recognition_runs", page_id=page["id"], api_name=action, parameters_hash=param_hash) if r["status"] == "SUCCEEDED"]
        if cached:
            return cached[-1]
        limit = config["general_limit"] if action == "GeneralAccurateOCR" else config["seal_limit"]
        if _usage(conn, action) >= limit:
            raise ProviderError("LocalBudgetExceeded", "本月本地 OCR 保护预算已用完，已停止调用，可手动继续。")
        run = add(conn, "recognition_runs", page_id=page["id"], provider="TENCENT", api_name=action, parameters_hash=param_hash, status="RUNNING", provider_request_id=None, result_json=None, started_at=now(), finished_at=None)
    try:
        data = tencent_ocr(action, asset_path(page["image_uri"]).read_bytes(), config)
    except ProviderError as error:
        with db.write() as conn:
            change(conn, "recognition_runs", run["id"], status="FAILED", result_json={"error_code": error.code, "message": error.message}, finished_at=now())
        raise
    with db.write() as conn:
        run = change(conn, "recognition_runs", run["id"], status="SUCCEEDED", provider_request_id=data.get("RequestId"), result_json=data, finished_at=now())
        if action == "GeneralAccurateOCR":
            for index, item in enumerate(data.get("TextDetections") or []):
                polygon = item.get("Polygon") or _rect_polygon(item.get("ItemPolygon") or {})
                add(conn, "recognition_regions", run_id=run["id"], region_no=index, kind="TEXT", text=str(item.get("DetectedText", "")), confidence=Decimal(str(item.get("Confidence", 0))) / 100, polygon_json=polygon, extra_json={})
        else:
            seals = data.get("SealInfos") or ([data] if data.get("Location") else [])
            for index, item in enumerate(seals):
                add(conn, "recognition_regions", run_id=run["id"], region_no=index, kind="SEAL", text=str(item.get("SealBody", "")), confidence=None, polygon_json=_rect_polygon(item.get("Location") or {}), extra_json={"shape": item.get("SealShape"), "other_texts": item.get("OtherTexts", [])})
        return run


def _rect_polygon(rect: dict) -> list:
    return _polygon([int(rect.get("X", 0)), int(rect.get("Y", 0)), int(rect.get("Width", 0)), int(rect.get("Height", 0))])


def _evidence_suggestions(conn, pages: list[dict], student: dict, student_user: dict, class_name: str) -> list[dict]:
    result = []
    for page in pages:
        for run in rows(conn, "recognition_runs", page_id=page["id"]):
            if run["status"] != "SUCCEEDED":
                continue
            for region in rows(conn, "recognition_regions", run_id=run["id"]):
                text = region.get("text") or ""
                kind = None
                if region["kind"] == "SEAL" or re.search(r"20\d{2}[年./\-]|证明|盖章|活动时间", text):
                    kind = "VALIDITY"
                if any(token and token in text for token in (student_user["display_name"], student.get("student_number"), class_name)):
                    kind = "PARTICIPATION"
                if kind and len([r for r in result if r["evidence_type"] == kind]) < 4:
                    box = polygon_box(region["polygon_json"])
                    x, y, w, h = box
                    x1, y1 = max(0, x - 12), max(0, y - 10)
                    right, bottom = min(page["width_px"], x + w + 12), min(page["height_px"], y + h + 10)
                    if right - x1 >= 2 and bottom - y1 >= 2:
                        result.append({"page_id": page["id"], "evidence_type": kind, "box": [x1, y1, right - x1, bottom - y1], "region_id": region["id"]})
    return result


@router.post("/applications/{application_id}/analyze")
def analyze(application_id: str, body: dict | None = None, user: dict = Depends(current_user)):
    config = configuration()
    if not config["enabled"]:
        return {"status": "MANUAL_REQUIRED", "message": "云识别已在本地配置关闭，可手动填写和截图。"}
    if not ANALYSIS_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有识别任务正在进行，请稍后重试。")
    try:
        return _analyze(application_id, user, config)
    finally:
        ANALYSIS_LOCK.release()


def _analyze(application_id: str, user: dict, config: dict) -> dict:
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        links = rows(conn, "revision_files", revision_id=revision["id"])
        if not links:
            raise HTTPException(400, "请先上传至少一份证明材料。")
        pages = [page for link in links for page in sorted(rows(conn, "file_pages", file_id=link["file_id"]), key=lambda p: p["page_index"])]
        if len(pages) > config["max_analysis_pages"]:
            raise HTTPException(400, f"本次材料共{len(pages)}页，超过本地单次识别保护上限{config['max_analysis_pages']}页，请拆减附件或手动填写。")
        fingerprint = revision_fingerprint(conn, revision)
        rule = one(conn, "score_rule_versions", context["class_year"]["score_rule_version_id"])
        analysis = add(conn, "ai_analyses", application_id=application_id, input_revision_id=revision["id"], input_fingerprint=fingerprint, score_rule_version_id=rule["id"], model_name=config["deepseek_model"], prompt_version="moral-score-v1", status="RUNNING", suggested_title=None, suggested_score=None, suggested_activity_id=None, result_json=None)
        for index, page in enumerate(pages):
            add(conn, "analysis_inputs", analysis_id=analysis["id"], file_id=page["file_id"], page_id=page["id"], recognition_run_id=None, input_kind="PAGE", input_hash=page["render_sha256"], sort_order=index)
    warnings = []
    ocr_runs = []
    blocked_actions = set()
    if config["secret_id"] and config["secret_key"]:
        for page in pages:
            for action in ("GeneralAccurateOCR", "SealOCR"):
                if action in blocked_actions:
                    continue
                try:
                    ocr_runs.append(_ocr_page(page, action, config))
                except ProviderError as error:
                    warnings.append(error.message)
                    # A resource/permission error is not resolved by charging through subsequent pages.
                    if error.code in ("LocalBudgetExceeded", "NotConfigured", "NetworkError", "HTTPError") or any(p in error.code for p in ("AuthFailure", "Unauthorized", "ResourcePackageRunOut", "InArrears", "ChargeStatusException")):
                        blocked_actions.add(action)
                        if "AuthFailure" in error.code or "Unauthorized" in error.code or error.code in ("NetworkError", "HTTPError"):
                            blocked_actions.update(("GeneralAccurateOCR", "SealOCR"))
    else:
        warnings.append("腾讯云 OCR 未配置，可手动填写和框选。")
    with db.write() as conn:
        texts = []
        for run in ocr_runs:
            regions = rows(conn, "recognition_regions", run_id=run["id"])
            text = "\n".join(region.get("text") or "" for region in regions).strip()[:12000]
            if text:
                texts.append({"page_id": run["page_id"], "kind": run["api_name"], "text": text})
            page = one(conn, "file_pages", run["page_id"])
            add(conn, "analysis_inputs", analysis_id=analysis["id"], file_id=page["file_id"], page_id=page["id"], recognition_run_id=run["id"], input_kind="OCR", input_hash=run["parameters_hash"], sort_order=len(texts) + len(pages))
            # Material content is immutable; upgrading public redaction does not alter private evidence.
            for crop in rows(conn, "evidence_crops", page_id=page["id"]):
                manual = (crop.get("masks_json") or {}).get("manual", [])
                change(conn, "evidence_crops", crop["id"], **_build_public_crop(conn, crop, page, manual))
        activities = [{"id": a["id"], "name": a["canonical_name"], "aliases": [v["alias"] for v in rows(conn, "activity_aliases", activity_id=a["id"]) if v["status"] == "ACTIVE"]} for a in rows(conn, "activities", status="ACTIVE")][:300]
        evidence = _evidence_suggestions(conn, pages, context["student"], context["user"], context["class"]["name"])
    if not texts:
        with db.write() as conn:
            change(conn, "ai_analyses", analysis["id"], status="FAILED", result_json={"message": "未取得可供分析的OCR内容。", "warnings": list(dict.fromkeys(warnings))})
        return {"status": "MANUAL_REQUIRED", "analysis_id": analysis["id"], "analysis": {"id": analysis["id"], "status": "FAILED"}, "message": "未取得可供分析的文字，可手动填写、计分和框选证明。", "warnings": list(dict.fromkeys(warnings))}
    prompt_context = {"category": context["category"]["name"], "category_cap": str(context["category"]["default_cap"]), "academic_year": context["year"]["name"], "student": {"name": context["user"]["display_name"], "class": context["class"]["name"]}, "scoring_rules": rule.get("rules_json") or {}, "activity_catalog": activities, "untrusted_materials": texts}
    try:
        suggestion = deepseek_suggestion(prompt_context, config)
        title = str(suggestion.get("title", "")).strip()[:255]
        score = money(suggestion.get("requested_score", "0"))
        if not title or score < 0 or score > 105:
            raise ValueError("invalid suggestion")
        valid_activity_ids = {activity["id"] for activity in activities}
        activity_id = suggestion.get("activity_id")
        if activity_id not in valid_activity_ids:
            activity_id = None
        payload = suggestion.get("payload_json") if isinstance(suggestion.get("payload_json"), dict) else {}
        payload = {key: str(payload.get(key, ""))[:500] for key in ("edition_key", "track_key", "group_key", "work_key", "award_level", "subtype")}
        suggestion = {"title": title, "requested_score": str(score), "score": str(score), "suggested_score": str(score), "activity_id": activity_id, "payload_json": payload, "explanation": str(suggestion.get("explanation", ""))[:3000], "match_uncertain": bool(suggestion.get("match_uncertain", not activity_id)), "suggested_crops": evidence}
    except (ProviderError, InvalidOperation, ValueError, TypeError, HTTPException) as error:
        message = error.message if isinstance(error, ProviderError) else "AI 建议不符合预期格式，请手动填写。"
        with db.write() as conn:
            change(conn, "ai_analyses", analysis["id"], status="FAILED", result_json={"message": message, "warnings": list(dict.fromkeys(warnings)), "suggested_crops": evidence})
        return {"status": "MANUAL_REQUIRED", "analysis_id": analysis["id"], "analysis": {"id": analysis["id"], "status": "FAILED"}, "message": message, "warnings": list(dict.fromkeys(warnings)), "suggested_crops": evidence}
    with db.write() as conn:
        change(conn, "ai_analyses", analysis["id"], status="SUCCEEDED", suggested_title=suggestion["title"], suggested_score=score, suggested_activity_id=activity_id, result_json={"suggestion": suggestion, "warnings": list(dict.fromkeys(warnings))})
    return {"status": "SUCCEEDED", "analysis_id": analysis["id"], "analysis": {"id": analysis["id"], "status": "SUCCEEDED", "input_revision_id": revision["id"]}, "suggestion": suggestion, "warnings": list(dict.fromkeys(warnings)), "message": "建议已生成；请核对后点击应用，当前填写内容尚未改变。"}


@router.post("/applications/{application_id}/apply-analysis")
def apply_analysis(application_id: str, body: dict, user: dict = Depends(current_user)):
    from .workflow import application_detail
    with db.write() as conn:
        context, revision = _editable(conn, application_id, user)
        if not isinstance(body.get("analysis_id"), str) or not body["analysis_id"]:
            raise HTTPException(400, "请选择分析记录。")
        analysis = one(conn, "ai_analyses", body["analysis_id"])
        if not analysis or analysis["application_id"] != application_id or analysis["status"] != "SUCCEEDED":
            raise HTTPException(400, "该分析记录不能应用。")
        if analysis["input_revision_id"] != revision["id"] or analysis["input_fingerprint"] != revision_fingerprint(conn, revision):
            raise HTTPException(409, "分析后内容或附件已变更，请重新分析；已有填写不会被覆盖。")
        suggestion = (analysis.get("result_json") or {}).get("suggestion", {})
        payload = {**(revision.get("payload_json") or {}), **suggestion.get("payload_json", {})}
        payload["ai_match_uncertain"] = suggestion.get("match_uncertain", False)
        payload["ai_explanation"] = suggestion.get("explanation", "")
        revision = change(conn, "application_revisions", revision["id"], title=suggestion["title"], requested_score=money(suggestion["requested_score"]), activity_id=suggestion.get("activity_id"), payload_json=payload, ai_analysis_id=analysis["id"])
        existing = rows(conn, "evidence_crops", revision_id=revision["id"])
        for item in suggestion.get("suggested_crops", [])[:8]:
            page = one(conn, "file_pages", item["page_id"])
            if not page or not one(conn, "revision_files", revision_id=revision["id"], file_id=page["file_id"]):
                continue
            if any(c["page_id"] == page["id"] and c["evidence_type"] == item["evidence_type"] and polygon_box(c["polygon_json"]) == item["box"] for c in existing):
                continue
            create_crop(conn, revision, page, item["evidence_type"], item["box"], source="AI", region_id=item.get("region_id"))
        _touch(conn, context["application"])
        return application_detail(conn, one(conn, "applications", application_id), user)


def excel_text(value) -> str:
    value = str(value or "").replace("\x00", "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


def export_workbook(scores: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "德育分汇总"
    first = {1: "序号", 2: "班级", 3: "姓名", 4: "学号", 5: "品德素质分（80分）", 10: "能力拓展分（20分）", 13: "奖励分（5分）", 14: "总分", 15: "签字确认"}
    second = {5: "基准分（70分）", 6: "集体活动分（3分）", 7: "思想政治理论学习分（3分）", 8: "社会服务分（4分）", 9: "扣分（按规定）", 10: "学术科研及创新创业加分（10分）", 11: "文体竞赛加分（6分）", 12: "学生组织、社团任职加分（4分）"}
    for col, title in first.items():
        sheet.cell(1, col, title)
    for col, title in second.items():
        sheet.cell(2, col, title)
    for col in (1, 2, 3, 4, 13, 14, 15):
        sheet.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)
    sheet.merge_cells("E1:I1")
    sheet.merge_cells("J1:L1")
    for row_no, student in enumerate(scores["members"], start=3):
        sheet.cell(row_no, 1, row_no - 2)
        sheet.cell(row_no, 2, excel_text(scores["class"]["name"]))
        sheet.cell(row_no, 3, excel_text(student["display_name"]))
        sheet.cell(row_no, 4, excel_text(student.get("student_number", "")))
        sheet.cell(row_no, 5, 70)
        sheet.cell(row_no, 9, 0)
        categories = sorted(student["categories"], key=lambda c: c.get("sort_order", 0))
        category_cols = [6, 7, 8, 10, 11, 12, 13]
        max_lines = 1
        for category, col in zip(categories, category_cols):
            lines = [f"{i + 1}、{item.get('title', '')}（{money(item.get('score', item.get('requested_score', 0)))}分）" + ("〔同项目/兼任职务取最高，本项不重复计入〕" if item.get("counted") is False else "") for i, item in enumerate(category.get("items", []))]
            lines.append(f"合计：{money(category['score'])}分")
            max_lines = max(max_lines, len(lines))
            sheet.cell(row_no, col, excel_text("\n".join(lines)))
        sheet.cell(row_no, 14, float(money(student["total"])))
        sheet.cell(row_no, 15, "")
        sheet.row_dimensions[row_no].height = min(300, max(42, max_lines * 18))
    thin = Side(style="thin", color="B3BEC9")
    for row in sheet.iter_rows(min_row=1, max_row=max(2, sheet.max_row), max_col=15):
        for cell in row:
            cell.font = Font(name="宋体", size=10, bold=cell.row <= 2, color="FFFFFF" if cell.row <= 2 else "1F2937")
            cell.fill = PatternFill("solid", fgColor="173F66" if cell.row <= 2 else ("F2F6FA" if cell.row % 2 == 0 else "FFFFFF"))
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(horizontal="center" if cell.column in (1, 2, 3, 4, 5, 9, 14, 15) or cell.row <= 2 else "left", vertical="center", wrap_text=True)
            if cell.column in (5, 9, 14) and cell.row >= 3:
                cell.number_format = "0.00"
    widths = [7, 14, 12, 19, 12, 34, 34, 34, 12, 37, 34, 34, 30, 12, 16]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 30
    sheet.row_dimensions[2].height = 48
    sheet.freeze_panes = "F3"
    sheet.auto_filter.ref = f"A2:O{max(2, sheet.max_row)}"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = "1:2"
    sheet.print_area = f"A1:O{max(2, sheet.max_row)}"
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


@router.get("/export")
def export_scores(class_id: str, year_id: str, user: dict = Depends(current_user)):
    from urllib.parse import quote
    from .workflow import class_scores
    with db.read() as conn:
        require(conn, user, "export.read", class_id=class_id)
        scores = class_scores(conn, class_id, year_id, public=False)
    filename = f"{scores['year']['name']}-{scores['class']['name']}-德育分汇总.xlsx"
    content = export_workbook(scores)
    return StreamingResponse(io.BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=score-summary.xlsx; filename*=UTF-8''{quote(filename)}", "Cache-Control": "private, no-store"})
