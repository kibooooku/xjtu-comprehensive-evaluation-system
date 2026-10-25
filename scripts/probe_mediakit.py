"""Small, opt-in AI MediaKit OCR benchmark; entirely separate from application data.

Default invocation is offline. --cloud permits at most --max-calls cumulative OCR
attempts in this probe's private ledger (default 8, absolute ceiling 10). A prior
failure is not retried unless the operator explicitly supplies --retry-failed.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
API_ROOT = "https://mediakit.cn-beijing.volces.com"
UPLOAD_API = "/api/v1/tools-sync/request-media-upload-url"
OCR_API = "/api/v1/tools-sync/image-ocr"
VERSION = "standard"
KEY_NAME = "VOLCENGINE_MEDIAKIT_API_KEY"
UNIT_COST = Decimal("0.00138")
TOKEN_KEYS = {"name", "student_number", "class", "activity", "date"}


class ProbeError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_key() -> str:
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        return value
    path = ROOT / "secrets" / "volcengine_mediakit.env"
    if path.is_file():
        from dotenv import dotenv_values
        return str(dotenv_values(path, encoding="utf-8-sig").get(KEY_NAME) or "").strip()
    return ""


def read_manifest(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        samples = data["samples"]
        if not isinstance(samples, list) or not samples or len(samples) > 100:
            raise ValueError
        ids = set()
        for sample in samples:
            if not isinstance(sample, dict) or not isinstance(sample.get("id"), str) or not sample["id"] or sample["id"] in ids:
                raise ValueError
            ids.add(sample["id"])
            if not isinstance(sample.get("image_path"), str) or not Path(sample["image_path"]).is_absolute():
                raise ValueError
            tokens = sample.get("expected_tokens") or {}
            if not isinstance(tokens, dict) or any(key not in TOKEN_KEYS or not (value is None or isinstance(value, str) or (isinstance(value, list) and all(isinstance(item, str) for item in value))) for key, value in tokens.items()):
                raise ValueError
            if sample.get("tencent_result_path") and not Path(sample["tencent_result_path"]).is_absolute():
                raise ValueError
        return samples
    except (OSError, ValueError, KeyError, TypeError):
        raise ProbeError("INVALID_MANIFEST", "清单无效：需要samples数组、唯一id、图片绝对路径；对照字段可为字符串、备选字符串列表或留空。") from None


@dataclass
class PreparedImage:
    original: Image.Image
    encoded: bytes
    mime_type: str
    upload_width: int
    upload_height: int
    cache_key: str


def prepare_image(path: Path) -> PreparedImage:
    try:
        with Image.open(path) as image:
            if image.width * image.height > 100_000_000:
                raise ProbeError("IMAGE_TOO_LARGE", "源图像素过大，请先选择单页图像。")
            oriented = ImageOps.exif_transpose(image)
            if oriented.mode in ("RGBA", "LA") or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                original = Image.new("RGB", rgba.size, "white")
                original.paste(rgba, mask=rgba.getchannel("A"))
            else:
                original = oriented.convert("RGB")
            original.load()
    except ProbeError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ProbeError("INVALID_IMAGE", "图片不能读取；请提供有效单页图片，不直接传PDF。") from None
    width, height = original.size
    if min(width, height) < 2:
        raise ProbeError("INVALID_IMAGE", "图片尺寸无效。")
    factor = min(1.0, 2160 / min(width, height), 3840 / max(width, height))
    dimensions = (max(1, int(width * factor)), max(1, int(height * factor)))
    resized = original if dimensions == original.size else original.resize(dimensions, Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    resized.save(stream, "PNG", optimize=True)
    encoded, mime = stream.getvalue(), "image/png"
    if len(encoded) > 10 * 1024 * 1024:
        stream = io.BytesIO()
        resized.save(stream, "JPEG", quality=92)
        encoded, mime = stream.getvalue(), "image/jpeg"
    if len(encoded) > 10 * 1024 * 1024:
        raise ProbeError("IMAGE_TOO_LARGE", "预处理图片仍超过10MB，请压缩或另选单页。")
    key = hashlib.sha256((VERSION + ":probe-resize-v1:").encode() + encoded).hexdigest()
    return PreparedImage(original, encoded, mime, dimensions[0], dimensions[1], key)


class Ledger:
    """Only the probe's own file is opened; application evaluation.db is never used."""
    def __init__(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "probe-ledger.sqlite3"
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, elapsed REAL, error_code TEXT, result_json TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def cached(self, key: str):
        with self.connect() as conn:
            row = conn.execute("SELECT result_json,elapsed FROM attempts WHERE cache_key=? AND version=? AND state='SUCCEEDED' ORDER BY created_at DESC, rowid DESC LIMIT 1", (key, VERSION)).fetchone()
        return self._cached_payload(row) if row else None

    @staticmethod
    def _cached_payload(row):
        payload = json.loads(row[0])
        # Older successful entries already retain the real interface duration in elapsed.
        if payload.get("ocr_elapsed_seconds") is None and row[1] is not None:
            payload["ocr_elapsed_seconds"] = round(float(row[1]), 3)
        return payload

    def reserve(self, key: str, cap: int, retry_failed=False):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cached = conn.execute("SELECT result_json,elapsed FROM attempts WHERE cache_key=? AND version=? AND state='SUCCEEDED' ORDER BY rowid DESC LIMIT 1", (key, VERSION)).fetchone()
            if cached:
                return None, self._cached_payload(cached)
            previous = conn.execute("SELECT state FROM attempts WHERE cache_key=? AND version=? ORDER BY rowid DESC LIMIT 1", (key, VERSION)).fetchone()
            if previous and previous[0] == "STARTED":
                raise ProbeError("PREVIOUS_ATTEMPT_UNCERTAIN", "此图前次请求仍在进行或中断结果未知，不重复提交同图OCR。")
            if previous and not retry_failed:
                raise ProbeError("PREVIOUS_ATTEMPT_FAILED", "此图已有失败或结果未知的调用记录；不会隐式重试。确认后可显式使用--retry-failed，仍计入总预算。")
            if conn.execute("SELECT count(*) FROM attempts").fetchone()[0] >= cap:
                raise ProbeError("CALL_CAP_REACHED", "探针累计OCR尝试已达到本次预算，不再调用。")
            attempt = str(uuid4())
            conn.execute("INSERT INTO attempts(id,cache_key,version,state,created_at) VALUES(?,?,?,'STARTED',?)", (attempt, key, VERSION, timestamp()))
            return attempt, None

    def check_budget(self, key: str, cap: int, retry_failed=False):
        with self.connect() as conn:
            previous = conn.execute("SELECT state FROM attempts WHERE cache_key=? AND version=? ORDER BY rowid DESC LIMIT 1", (key, VERSION)).fetchone()
            if previous and previous[0] == "STARTED":
                raise ProbeError("PREVIOUS_ATTEMPT_UNCERTAIN", "此图前次请求仍在进行或中断结果未知，不再上传或调用。")
            if previous and previous[0] != "SUCCEEDED" and not retry_failed:
                raise ProbeError("PREVIOUS_ATTEMPT_FAILED", "此图已有失败或结果未知的调用记录，不会隐式重试。")
        if self.stats()["attempts"] >= cap:
            raise ProbeError("CALL_CAP_REACHED", "探针累计OCR尝试已达到预算，不再上传或调用。")

    def finish(self, attempt, state, elapsed, result=None, error_code=None):
        with self.connect() as conn:
            conn.execute("UPDATE attempts SET state=?,finished_at=?,elapsed=?,error_code=?,result_json=? WHERE id=?", (state, timestamp(), elapsed, error_code, json.dumps(result, ensure_ascii=False) if result is not None else None, attempt))

    def stats(self):
        with self.connect() as conn:
            states = dict(conn.execute("SELECT state,count(*) FROM attempts GROUP BY state"))
        return {"attempts": sum(states.values()), "successful_calls": states.get("SUCCEEDED", 0), "states": states}


def trusted_upload_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.fragment and any(host == domain or host.endswith("." + domain) for domain in ("volcengine.com", "volces.com", "volcvod.com"))
    except (ValueError, TypeError):
        return False


def safe_value(value, key="", limit=12000) -> str:
    text = str(value or "")[:limit]
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"https?://[^\s<>\"']*(?:[?&](?:Authorization|Signature|X-Amz-[^=]+|X-Tos-[^=]+)=)[^\s<>\"']*", "[REDACTED_UPLOAD_URL]", text, flags=re.I)


def response_failure(status: int, stage: str) -> ProbeError:
    if status == 401:
        return ProbeError("AUTHENTICATION_FAILED", "服务未接受API Key，请检查配置与开通状态。")
    if status == 403:
        return ProbeError("PERMISSION_DENIED", "当前凭据没有调用该服务的权限。")
    if status == 429:
        return ProbeError("RATE_LIMITED", "服务触发限流，本次停止且不自动重试。")
    if status == 402:
        return ProbeError("PAYMENT_REQUIRED", "服务计费状态不允许调用，请检查账户。")
    if status >= 500:
        return ProbeError("PROVIDER_UNAVAILABLE", "服务暂不可用，本次停止且不自动重试。")
    return ProbeError("HTTP_ERROR", f"{stage}失败（HTTP {status}），已停止后续样本。")


def normalize_blocks(result, key="") -> list[dict]:
    if not isinstance(result, dict) or not isinstance(result.get("ocr_result"), list):
        raise ProbeError("INVALID_RESPONSE", "OCR响应缺少标准版文字结果。")
    blocks = []
    for item in result["ocr_result"]:
        try:
            coords = [float(item[name]) for name in ("top_left_x", "top_left_y", "bottom_right_x", "bottom_right_y")]
            if not all(math.isfinite(number) for number in coords):
                raise ValueError
            position_valid = coords[2] > coords[0] and coords[3] > coords[1]
            confidence = float(item["confidence"]) if item.get("confidence") is not None else None
            if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError
            block = {"content": safe_value(item.get("content"), key), "confidence": confidence, "top_left_x": coords[0], "top_left_y": coords[1], "bottom_right_x": coords[2], "bottom_right_y": coords[3], "position_valid": position_valid}
            if not position_valid:
                block["position_warning"] = "坐标顺序异常或区域为空：保留文字，不交换坐标，不绘制定位框。"
            blocks.append(block)
        except (ValueError, TypeError, KeyError):
            raise ProbeError("INVALID_RESPONSE", "OCR文字框或置信度格式无效，已停止。") from None
    return blocks


def map_blocks(blocks, image: PreparedImage):
    mapped = []
    sx, sy = image.original.width / image.upload_width, image.original.height / image.upload_height
    for item in blocks:
        if item.get("position_valid") is False or item["bottom_right_x"] <= item["top_left_x"] or item["bottom_right_y"] <= item["top_left_y"]:
            mapped.append({**item, "position_valid": False, "position_warning": item.get("position_warning") or "坐标顺序异常：保留文字，不绘制定位框。", "original_box": None})
            continue
        x1 = max(0.0, min(image.upload_width, item["top_left_x"])) * sx
        y1 = max(0.0, min(image.upload_height, item["top_left_y"])) * sy
        x2 = max(0.0, min(image.upload_width, item["bottom_right_x"])) * sx
        y2 = max(0.0, min(image.upload_height, item["bottom_right_y"])) * sy
        if x2 <= x1 or y2 <= y1:
            mapped.append({**item, "position_valid": False, "position_warning": "文字框超出图像或区域为空：保留文字，不绘制定位框。", "original_box": None})
        else:
            mapped.append({**item, "position_valid": True, "original_box": [round(x1, 3), round(y1, 3), round(x2 - x1, 3), round(y2 - y1, 3)]})
    return mapped


def token_hits(blocks: list[dict], expected: dict) -> dict:
    fields = {}
    position_evaluated = any("original_box" in block for block in blocks)
    for name, token in expected.items():
        alternatives = token if isinstance(token, list) else [token]
        alternatives = [value for value in alternatives if isinstance(value, str) and value]
        if not alternatives:
            continue
        matches = [block for block in blocks if any(value in block.get("content", "") for value in alternatives)]
        boxes = [block["original_box"] for block in matches if block.get("original_box") is not None and block.get("position_valid") is not False]
        fields[name] = {"exact_match": bool(matches), "matched_boxes": boxes, "position_evaluated": position_evaluated, "reliable_position_match": bool(boxes) if position_evaluated else None, "unpositioned_matches": sum(block.get("original_box") is None or block.get("position_valid") is False for block in matches) if position_evaluated else None, "alternatives_checked": len(alternatives), "alternative_matches": [any(value in block.get("content", "") for block in blocks) for value in alternatives]}
    return {"fields": fields, "checked_fields": len(fields), "matched_fields": sum(field["exact_match"] for field in fields.values()), "note": "仅为对照字段的字面子串命中，不等同于完整OCR字符准确率。" if fields else "未提供对照字段，不能据此计算准确率。"}


def tencent_comparison(sample: dict) -> dict | None:
    if not sample.get("tencent_result_path"):
        return None
    try:
        data = json.loads(Path(sample["tencent_result_path"]).read_text(encoding="utf-8-sig"))
        if isinstance(data.get("result_json"), dict):
            data = data["result_json"]
        data = data.get("Response", data)
        blocks = [{"content": str(item.get("DetectedText") or "")} for item in data["TextDetections"]]
        return {"source": "existing_cache_only", "text_blocks": len(blocks), "characters": sum(len(block["content"]) for block in blocks), "tokens": token_hits(blocks, sample.get("expected_tokens") or {}), "note": "只读既有腾讯缓存，没有新调用；输入处理差异及缓存生成字段不构成人工真值。"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"source": "existing_cache_only", "error_code": "TENCENT_CACHE_UNAVAILABLE", "note": "腾讯缓存无法读取；不会为此补调云服务。"}


class Probe:
    def __init__(self, output_dir: Path, *, cloud=False, api_key="", max_calls=8, retry_failed=False, client=None):
        if not isinstance(max_calls, int) or isinstance(max_calls, bool) or not 1 <= max_calls <= 10:
            raise ProbeError("INVALID_CAP", "--max-calls必须在1至10之间。")
        self.folder, self.cloud, self.key = Path(output_dir), cloud, api_key
        self.cap, self.retry_failed = max_calls, retry_failed
        self.ledger = Ledger(self.folder)
        self.client = client
        self.owns_client = False

    def close(self):
        if self.owns_client and self.client:
            self.client.close()

    def _http(self):
        if not self.cloud:
            raise ProbeError("OFFLINE", "离线模式禁止网络访问。")
        if not self.key:
            raise ProbeError("MISSING_KEY", f"请先配置{KEY_NAME}，本次没有发起网络请求。")
        if self.client is None:
            self.client = httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False)
            self.owns_client = True
        return self.client

    def _api(self, path: str, body: dict) -> dict:
        request = self._http().build_request("POST", API_ROOT + path, headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}, json=body)
        response = self._http().send(request, auth=None, follow_redirects=False)
        try:
            data = response.json()
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            # Inspect only in memory: an AccessDenied envelope can describe an unknown
            # API Key. Never copy provider text, including its masked Key, to output.
            error = data["error"]
            description = (str(error.get("code") or "") + " " + str(error.get("message") or "")).casefold()
            compact = re.sub(r"[^a-z]", "", description)
            if "invalidapikey" in compact or "get api key error" in description:
                raise ProbeError("INVALID_API_KEY", "平台找不到此API Key，请核对创建入口及完整Key值。")
        if not 200 <= response.status_code < 300:
            raise response_failure(response.status_code, "MediaKit请求")
        if not isinstance(data, dict):
            raise ProbeError("INVALID_RESPONSE", "服务响应不是有效JSON。") from None
        if data.get("success") is not True:
            # No provider message/body is copied: these can echo credentials or upload URLs.
            error = data.get("error") or {}
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            if "auth" in code.lower() or "key" in code.lower():
                raise ProbeError("AUTHENTICATION_FAILED", "API Key或服务权限验证失败。")
            if "limit" in code.lower():
                raise ProbeError("RATE_LIMITED", "服务限制本次请求，不自动重试。")
            raise ProbeError("PROVIDER_REJECTED", "服务没有接受本次请求，已停止后续样本。")
        return data

    def _upload(self, image: PreparedImage) -> str:
        response = self._api(UPLOAD_API, {})
        result = response.get("result") or {}
        if not isinstance(result, dict) or result.get("method") != "PUT" or not trusted_upload_url(result.get("upload_url", "")):
            raise ProbeError("UNTRUSTED_UPLOAD", "上传凭证的目标域名或方法不符合官方协议，已停止。")
        headers = {}
        try:
            for header in result.get("upload_headers") or []:
                name, value = str(header["key"]), str(header["value"])
                if "\r" in name + value or "\n" in name + value or (name.lower() == "authorization" and value.lower().startswith("bearer ")) or (self.key and self.key in value):
                    raise ValueError
                headers[name] = value
        except (TypeError, ValueError, KeyError):
            raise ProbeError("UNSAFE_UPLOAD_HEADERS", "上传请求头格式异常，已停止。") from None
        content_type = next((value for name, value in headers.items() if name.lower() == "content-type"), None)
        if content_type and content_type.split(";", 1)[0].strip().lower() != image.mime_type:
            raise ProbeError("UPLOAD_TYPE_MISMATCH", "预签上传类型与图片实际类型不一致。")
        if not content_type:
            headers["Content-Type"] = image.mime_type
        # Do not carry the MediaKit Bearer token to the presigned upload host.
        request = self._http().build_request("PUT", result["upload_url"], headers=headers, content=image.encoded)
        if not any(name.lower() == "authorization" for name in headers):
            request.headers.pop("Authorization", None)
        response = self._http().send(request, auth=None, follow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise response_failure(response.status_code, "图片上传")
        file_id = result.get("file_id")
        if not isinstance(file_id, str) or not file_id or any(char.isspace() for char in file_id):
            raise ProbeError("INVALID_RESPONSE", "上传凭证缺少有效文件标识。")
        return file_id if file_id.startswith("mediakit://") else "mediakit://" + file_id

    def _recognize(self, image: PreparedImage):
        cached = self.ledger.cached(image.cache_key)
        if cached:
            return cached, True
        self.ledger.check_budget(image.cache_key, self.cap, self.retry_failed)
        file_id = self._upload(image)
        attempt, cached = self.ledger.reserve(image.cache_key, self.cap, self.retry_failed)
        if cached:
            return cached, True
        started = time.monotonic()
        try:
            response = self._api(OCR_API, {"image_url": file_id, "tool_version": VERSION})
            result = {"version": VERSION, "request_id": safe_value(response.get("request_id"), self.key, 150), "blocks": normalize_blocks(response.get("result"), self.key), "ocr_elapsed_seconds": round(time.monotonic() - started, 3)}
            self.ledger.finish(attempt, "SUCCEEDED", result["ocr_elapsed_seconds"], result=result)
            return result, False
        except httpx.TimeoutException:
            self.ledger.finish(attempt, "TIMEOUT", time.monotonic() - started, error_code="TIMEOUT")
            raise ProbeError("TIMEOUT", "OCR请求超时，结果和费用可能未知；已记录尝试，不自动重试。") from None
        except httpx.HTTPError:
            self.ledger.finish(attempt, "FAILED", time.monotonic() - started, error_code="NETWORK_ERROR")
            raise ProbeError("NETWORK_ERROR", "OCR网络请求失败，已记录尝试且不会自动重试。") from None
        except ProbeError as error:
            self.ledger.finish(attempt, "FAILED", time.monotonic() - started, error_code=error.code)
            raise

    def run(self, samples: list[dict]) -> dict:
        before = self.ledger.stats()
        output = []
        for sample in samples:
            started = time.monotonic()
            entry = {"id": sample["id"], "label": safe_value(sample.get("label") or sample["id"], self.key, 200), "source_page_number": sample.get("source_page_number"), "reference_source": safe_value(sample.get("expected_source") or "用户提供的对照字段，未经人工真值校验", self.key, 500)}
            try:
                image = prepare_image(Path(sample["image_path"]))
                stem = hashlib.sha256(sample["id"].encode()).hexdigest()[:12] + "-" + image.cache_key[:12]
                assets = self.folder / "assets"
                assets.mkdir(exist_ok=True)
                original_name = stem + "-source.png"
                image.original.save(assets / original_name, "PNG")
                entry.update(original_size=list(image.original.size), uploaded_size=[image.upload_width, image.upload_height], upload_bytes=len(image.encoded), source_image="assets/" + original_name, tencent=tencent_comparison(sample))
                cached = self.ledger.cached(image.cache_key)
                if not self.cloud and not cached:
                    entry.update(state="OFFLINE_READY", message="离线图像检查通过；未调用OCR。", tokens={"fields": {}, "checked_fields": 0, "matched_fields": 0, "note": "尚未识别，字段尚未评价。"})
                else:
                    result, from_cache = (cached, True) if cached else self._recognize(image)
                    blocks = map_blocks(result["blocks"], image)
                    overlay = image.original.copy()
                    draw = ImageDraw.Draw(overlay)
                    for index, block in enumerate(blocks, 1):
                        if block.get("original_box") is None:
                            continue
                        x, y, width, height = block["original_box"]
                        if width and height:
                            draw.rectangle((x, y, x + width - 1, y + height - 1), outline="#df2525", width=max(2, image.original.width // 600))
                    overlay_name = stem + "-overlay.png"
                    overlay.save(assets / overlay_name, "PNG")
                    entry.update(state="CACHED" if from_cache else "SUCCEEDED", blocks=blocks, position_warning_blocks=sum(block.get("position_valid") is False for block in blocks), ocr_elapsed_seconds=result.get("ocr_elapsed_seconds"), tokens=token_hits(blocks, sample.get("expected_tokens") or {}), overlay_image="assets/" + overlay_name, request_id=result.get("request_id"), text_file="assets/" + stem + ".txt", result_file="assets/" + stem + ".json")
                    (assets / (stem + ".txt")).write_text("\n".join(block["content"] for block in blocks), encoding="utf-8")
                    (assets / (stem + ".json")).write_text(json.dumps({"version": VERSION, "request_id": result.get("request_id"), "ocr_elapsed_seconds": entry["ocr_elapsed_seconds"], "position_warning_blocks": entry["position_warning_blocks"], "blocks": blocks, "tokens": entry["tokens"]}, ensure_ascii=False, indent=2), encoding="utf-8")
            except ProbeError as error:
                entry.update(state="FAILED", error_code=error.code, message=error.message)
            except httpx.TimeoutException:
                entry.update(state="FAILED", error_code="UPLOAD_TIMEOUT", message="获取凭证或上传超时，未继续OCR，也不自动重试。")
            except httpx.HTTPError:
                entry.update(state="FAILED", error_code="UPLOAD_NETWORK_ERROR", message="获取凭证或上传失败，未继续OCR，也不自动重试。")
            except Exception:
                entry.update(state="FAILED", error_code="LOCAL_CHECK_FAILED", message="本地检查未完成，已停止；不会输出可能包含敏感信息的原始异常。")
            entry["elapsed_seconds"] = round(time.monotonic() - started, 3)
            output.append(entry)
            if entry["state"] == "FAILED":
                break
        after = self.ledger.stats()
        successes = after["successful_calls"] - before["successful_calls"]
        attempts = after["attempts"] - before["attempts"]
        report = {"created_at": timestamp(), "mode": "cloud" if self.cloud else "offline", "version": VERSION, "max_cumulative_attempts": self.cap, "samples_requested": len(samples), "samples_checked": len(output), "ocr_attempts_this_run": attempts, "successful_calls_this_run": successes, "ledger": after, "estimated_success_cost_cny_this_run": str(UNIT_COST * successes), "estimated_possible_cost_cny_this_run": str(UNIT_COST * attempts), "estimated_success_cost_cny_total": str(UNIT_COST * after["successful_calls"]), "estimated_possible_cost_cny_total": str(UNIT_COST * after["attempts"]), "cost_note": "按官方标准版1.38元/千次估算，不是账单；缓存不算新调用，超时结果未知。未计免费额度、存储或其他可能费用。", "samples": output}
        (self.folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self._html(report)
        return report

    def _html(self, report):
        esc = lambda value: html.escape(str(value))
        sections = []
        for sample in report["samples"]:
            pictures = "".join(f'<figure><img src="{esc(sample[key])}" alt="{label}"><figcaption>{label}</figcaption></figure>' for key, label in (("source_image", "原图副本"), ("overlay_image", "映回原图坐标的文字框")) if sample.get(key))
            fields = (sample.get("tokens") or {}).get("fields", {})
            hits = "、".join(f"{esc(name)}：{'命中' if field['exact_match'] else '未命中'}" + ("（无可靠定位框）" if field["exact_match"] and field.get("reliable_position_match") is False else "") for name, field in fields.items())
            text = "\n".join(block["content"] for block in sample.get("blocks", []))
            historical = sample.get("tencent")
            historical_ok = historical is not None and not historical.get("error_code")
            mediakit_ok = sample["state"] in ("SUCCEEDED", "CACHED")
            baseline_fields = (historical.get("tokens") or {}).get("fields", {}) if historical_ok else {}
            rows = [f'<tr><td>文字块数</td><td>{len(sample.get("blocks", [])) if mediakit_ok else "尚未识别"}</td><td>{historical["text_blocks"] if historical_ok else "无历史结果"}</td></tr>']
            for name in sorted(set(fields) | set(baseline_fields)):
                current = (("命中" if fields[name]["exact_match"] else "未命中") + ("（无可靠定位框）" if fields[name]["exact_match"] and fields[name].get("reliable_position_match") is False else "")) if name in fields else ("未提供对照" if mediakit_ok else "尚未评价")
                previous = ("命中" if baseline_fields[name]["exact_match"] else "未命中") if name in baseline_fields else ("未提供对照" if historical_ok else "无历史结果")
                rows.append(f'<tr><td>{esc(name)}</td><td>{current}</td><td>{previous}</td></tr>')
            comparison = '<table><thead><tr><th>对照项</th><th>MediaKit标准版</th><th>既存腾讯缓存</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
            page_number = f' · 原件第{esc(sample["source_page_number"])}页' if sample.get("source_page_number") else ""
            original_elapsed = sample.get("ocr_elapsed_seconds")
            elapsed_label = "原OCR接口耗时（缓存保留）" if sample["state"] == "CACHED" else "本次OCR接口耗时"
            timing = f'；{elapsed_label}：{esc(original_elapsed)} 秒' if original_elapsed is not None else ""
            positioning = f'<p>文字块 {len(sample.get("blocks", []))} 块；定位异常 {sample.get("position_warning_blocks", 0)} 块。异常块保留文字、不绘框，文字命中不表示定位可靠。</p>' if mediakit_ok else ""
            sections.append(f'<article><h2>{esc(sample["label"])}{page_number}</h2><p>{esc(sample["state"])} · 本轮耗时：{sample["elapsed_seconds"]} 秒{timing}</p>{positioning}<p>{esc(sample.get("message", ""))}</p><p>{hits or esc((sample.get("tokens") or {}).get("note", "未提供对照字段，不能计算准确率。"))}</p><p>对照来源：{esc(sample["reference_source"])}</p>{comparison}<div class="pictures">{pictures}</div><details><summary>识别文字（私有报告）</summary><pre>{esc(text)}</pre></details></article>')
        page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\'; style-src \'unsafe-inline\'; base-uri \'none\'"><title>MediaKit 标准版小样测试</title><style>body{font:15px system-ui,sans-serif;margin:32px;color:#20314a;background:#f2f5f9}article{background:white;padding:22px;margin:22px 0;border-radius:12px}.pictures{display:flex;gap:20px;align-items:flex-start}figure{margin:0;flex:1;min-width:0}img{width:100%;border:1px solid #d0d8e2}figcaption{padding:6px}pre{white-space:pre-wrap;word-break:break-word}table{border-collapse:collapse;margin:16px 0}td,th{border:1px solid #ccd6e0;padding:8px 16px}@media(max-width:800px){.pictures{display:block}}</style>'
        page += f'<h1>MediaKit 标准版小样测试</h1><p>模式：{esc(report["mode"])}；本轮OCR尝试 {report["ocr_attempts_this_run"]}，成功 {report["successful_calls_this_run"]}；累计尝试 {report["ledger"]["attempts"]}/{report["max_cumulative_attempts"]}，累计成功 {report["ledger"]["successful_calls"]}。</p><p>本轮成功调用估算 ¥{esc(report["estimated_success_cost_cny_this_run"])}；累计成功调用估算 ¥{esc(report["estimated_success_cost_cny_total"])}；累计全部尝试估算上限 ¥{esc(report["estimated_possible_cost_cny_total"])}。{esc(report["cost_note"])}</p><p>字段命中不是完整字符准确率。图片、文字仅保存在本机私有测试目录。</p>' + "".join(sections) + '</html>'
        (self.folder / "report.html").write_text(page, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="独立MediaKit标准版OCR小样测试，默认离线。")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--cloud", action="store_true", help="显式允许上传及OCR网络调用")
    parser.add_argument("--max-calls", type=int, default=8, help="私有ledger累计OCR尝试上限，最大10")
    parser.add_argument("--retry-failed", action="store_true", help="显式允许再次尝试以前失败的同图，仍计总预算")
    args = parser.parse_args(argv)
    probe = None
    try:
        samples = read_manifest(args.manifest)
        probe = Probe(ROOT / "instance" / "mediakit-test", cloud=args.cloud, api_key=load_key() if args.cloud else "", max_calls=args.max_calls, retry_failed=args.retry_failed)
        report = probe.run(samples)
        print(json.dumps({"mode": report["mode"], "samples_checked": report["samples_checked"], "ocr_attempts": report["ocr_attempts_this_run"], "successful_calls": report["successful_calls_this_run"], "report": str(probe.folder / "report.html")}, ensure_ascii=False))
        return 1 if any(sample["state"] == "FAILED" for sample in report["samples"]) else 0
    except ProbeError as error:
        print(json.dumps({"error_code": error.code, "message": error.message}, ensure_ascii=False))
        return 2
    finally:
        if probe:
            probe.close()


if __name__ == "__main__":
    raise SystemExit(main())
