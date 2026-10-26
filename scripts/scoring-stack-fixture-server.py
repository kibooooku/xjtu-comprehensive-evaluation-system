"""An isolated real HTTP/worker fixture, with every external provider hard blocked.

Only run through browser-scoring-stack-smoke.mjs. No production debug endpoints
or real credentials are used; its database must be new and inside the fixture root.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from PIL import Image, ImageDraw, ImageFont


CASES = [
    {"id": 1, "category": "COLLECTIVE", "title": "虚构青禾春季集体活动", "body": "参加本次集体活动2次。", "score": "0.60"},
    {"id": 2, "category": "IDEOLOGY", "title": "虚构青年党团理论研修班", "body": "参加党团理论学习培训1次并完成结业。", "score": "1.00"},
    {"id": 3, "category": "SERVICE", "title": "虚构青禾社区志愿服务", "body": "参加志愿服务，服务工时16小时。", "score": "0.50"},
    {"id": 4, "category": "RESEARCH", "title": "虚构大学生工程设计竞赛", "body": "荣获省级竞赛二等奖。", "score": "6.00"},
    {"id": 5, "category": "ARTS", "title": "虚构校园星光长跑文体竞赛", "body": "校级文体竞赛，设一、二、三等奖。本人获得三等奖。", "score": "1.00"},
    {"id": 6, "category": "POST", "title": "虚构班级学生干部任职考评", "body": "担任班长，任期满一年，考评良好。", "score": "1.50"},
    {"id": 7, "category": "REWARD", "title": "虚构文明学生表彰活动", "body": "获得校级精神文明先进个人荣誉称号。", "score": "2.00"},
    {"id": 8, "category": "RESEARCH", "title": "虚构电子设计竞赛待核对材料", "body": "省级电子设计竞赛二等奖，尚未列明参与学生。", "score": None},
    {"id": 9, "category": "RESEARCH", "title": "虚构创新编程竞赛零分直接提交材料", "body": "省级创新编程竞赛三等奖，尚未列明参与学生。", "score": None},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    work = Path(args.work_dir).resolve()
    allowed = (ROOT / "instance/scoring-stack-isolated").resolve()
    if not work.is_relative_to(allowed) or not work.name.startswith("run-") or not 8760 <= args.port <= 8799:
        raise SystemExit("ISOLATED_STACK requires its own temporary directory and fixture port")
    database = work / "fixture.db"
    if database.exists():
        raise SystemExit("Refusing to reuse any existing database")
    work.mkdir(parents=True, exist_ok=True)
    images = work / "images"
    images.mkdir()
    stats = {"mode": "ISOLATED_STACK", "fake_general": 0, "fake_seal": 0, "fake_model": 0,
             "network_blocked": 0, "actual_cloud_calls": 0, "actual_business_writes": 0,
             "model_always_proposes": "99", "configuration_reads_private_keys": False,
             "active_ocr_files": 0, "peak_ocr_files": 0, "active_ai_items": 0, "peak_ai_items": 0,
             "provider_events": []}
    lock = threading.Lock()

    def persist_stats():
        target = work / "fixture-state.json"
        temporary = work / "fixture-state.tmp"
        temporary.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        # Windows may briefly deny replacement while the Node observer reads
        # the old snapshot. Telemetry must not fail a synthetic provider call.
        for attempt in range(50):
            try:
                temporary.replace(target)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.01)

    def record(key):
        with lock:
            stats[key] += 1
            persist_stats()

    def provider_active(kind, case_id, delta):
        with lock:
            field = 'active_ocr_files' if kind == 'OCR' else 'active_ai_items'
            peak = 'peak_ocr_files' if kind == 'OCR' else 'peak_ai_items'
            stats[field] += delta
            assert 0 <= stats[field] <= 3, f'{kind} per-owner limit violated'
            stats[peak] = max(stats[peak], stats[field])
            stats['provider_events'].append({'kind': kind, 'case_id': case_id,
                                            'state': 'START' if delta > 0 else 'END',
                                            'active': stats[field], 'time': time.time()})
            persist_stats()

    def wait_for_gate(kind):
        # Only the offline provider is delayed. Native queue claims, dependencies
        # and automatic follow-ups still run unchanged in the real application.
        deadline = time.monotonic() + 60
        while not (work / ('open-' + kind.lower() + '-gate')).exists():
            if time.monotonic() > deadline:
                raise AssertionError('Fixture provider gate was not released: ' + kind)
            time.sleep(0.02)

    def report_provider_errors(function):
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception:
                with lock:
                    with (work / 'provider-errors.log').open('a', encoding='utf-8') as stream:
                        stream.write(traceback.format_exc() + '\n')
                raise
        return wrapped

    def blocked(*args, **kwargs):
        record("network_blocked")
        raise AssertionError("ISOLATED_STACK forbids HTTP client provider access")

    async def async_blocked(*args, **kwargs):
        blocked()

    httpx.Client.send = blocked
    httpx.AsyncClient.send = async_blocked
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and (address[0] not in ("127.0.0.1", "::1", "localhost") or address[1] == 8000):
            blocked()
        return original_connect(sock, address)

    socket.socket.connect = local_only
    from app import db, integrations, main as application_main, materials, workflow
    application_main.load_dotenv = lambda *args, **kwargs: False
    config = {"enabled": True, "secret_id": "SYNTHETIC_ID_ONLY", "secret_key": "SYNTHETIC_KEY_ONLY",
              "deepseek_key": "SYNTHETIC_MODEL_ONLY", "deepseek_model": "ISOLATED_STACK_ALWAYS_99-" + work.name,
              "region": "ap-shanghai", "general_limit": 1000, "seal_limit": 1000, "max_analysis_pages": 12}
    integrations.configuration = materials.configuration = lambda: dict(config)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 26) if font_path.exists() else ImageFont.load_default(size=26)
    application = application_main.create_app(database_url="sqlite:///" + database.as_posix(), storage_dir=work / "storage")
    with db.read() as conn:
        user = db.one(conn, "users", username="student")
        profile = db.one(conn, "student_profiles", user_id=user["id"])
    # Exercise the actual recognized-library search and native AI association.
    # These records exist only inside this brand-new fixture SQLite database.
    with db.write() as conn:
        counselor = db.one(conn, "users", username="counselor")
        categories = {category['code']: category['id'] for category in db.rows(conn, 'score_categories')}
        year = workflow.active_year(conn)
        for case in CASES:
            if case['id'] in (4, 5, 6):
                activity = workflow._activity_values(conn, counselor, {'canonical_name': case['title'],
                    'category_id': categories[case['category']], 'academic_year_id': year['id'], 'decision': 'VALID'})
                case['matched_activity_id'] = activity['id']
        invalid = workflow._activity_values(conn, counselor, {'canonical_name': '虚构校园星光体验活动无效认定',
            'category_id': categories['ARTS'], 'academic_year_id': year['id'], 'decision': 'INVALID'})
        CASES[4]['invalid_activity_id'] = invalid['id']
    person = f"姓名：{user['display_name']}  学号：{profile['student_number']}  班级：软件2401"

    def lines(case):
        values = [(case["title"], 60, 65), ("ISOLATED_STACK 虚构材料，仅用于本地验收", 60, 125),
                  ("活动时间：2026年4月12日", 60, 320), (case["body"], 60, 400)]
        if case["score"] is not None:
            values.append((person, 60, 230))
        return values

    def polygon(box):
        x, y, w, h = box
        return [{"X": x, "Y": y}, {"X": x+w, "Y": y}, {"X": x+w, "Y": y+h}, {"X": x, "Y": y+h}]

    def detection(text, x, y):
        words, coordinates, cursor = [], [], float(x)
        for char in text:
            width = max(1, round(font.getlength(char)))
            words.append({"Character": char, "Confidence": 99})
            coordinates.append({"WordCoordinate": polygon([round(cursor), y, width, 35])})
            cursor += font.getlength(char)
        return {"DetectedText": text, "Confidence": 99, "Polygon": polygon([x, y, max(2, round(cursor-x)), 35]),
                "Words": words, "WordCoordPoint": coordinates}

    def fake_ocr(action, image, _config):
        with Image.open(io.BytesIO(image)) as raster:
            case_id, marker, guard = raster.convert("RGB").getpixel((0, 0))
        assert marker == 83 and guard == 17 and 1 <= case_id <= len(CASES), "Not an isolated fixture image"
        case = CASES[case_id-1]
        if action == "GeneralAccurateOCR":
            provider_active('OCR', case_id, 1)
            wait_for_gate('OCR')
            record("fake_general")
            return {"RequestId": "ISOLATED-TEXT-" + str(case_id), "TextDetections": [detection(*line) for line in lines(case)]}
        assert action == "SealOCR"
        record("fake_seal")
        provider_active('OCR', case_id, -1)
        return {"RequestId": "ISOLATED-SEAL-" + str(case_id), "SealInfos": [{"SealBody": "虚构测评验收用章", "Location": {"X": 700, "Y": 1150, "Width": 200, "Height": 200}}]}

    def fake_model(context, _config):
        text = "\n".join(str(record.get("text") or "") for record in context.get("untrusted_materials", []))
        case = next(case for case in CASES if case["title"] in text)
        candidates = context.get('activity_catalog') or []
        expected_activity = case.get('matched_activity_id')
        assert not expected_activity or any(item['id'] == expected_activity for item in candidates), 'Recognized match missing from real retrieval'
        provider_active('AI', case['id'], 1)
        wait_for_gate('AI')
        record("fake_model")
        result = {"title": case["title"], "requested_score": "99", "activity_id": expected_activity,
                "payload_json": {"award_level": "NATIONAL", "award_grade": "FIRST"},
                "explanation": "ISOLATED_STACK intentionally wrong model amount 99; policy must replace or defer it."}
        provider_active('AI', case['id'], -1)
        return result

    integrations.tencent_ocr = materials.tencent_ocr = report_provider_errors(fake_ocr)
    integrations.deepseek_suggestion = materials.deepseek_suggestion = report_provider_errors(fake_model)
    # The application deliberately exposes only safe errors to students. Capture
    # synthetic fixture stack traces locally so a concurrency regression remains
    # diagnosable without weakening that production error boundary.
    materials.run_ocr = report_provider_errors(materials.run_ocr)
    materials.run_ai = report_provider_errors(materials.run_ai)
    materials.apply_ocr_evidence = report_provider_errors(materials.apply_ocr_evidence)
    manifest = []
    for case in CASES:
        raster = Image.new("RGB", (1000, 1600), "white")
        draw = ImageDraw.Draw(raster)
        for text, x, y in lines(case):
            draw.text((x, y), text, font=font, fill="#17354d")
        draw.ellipse((700, 1150, 900, 1350), outline="#c83333", width=7)
        draw.text((720, 1225), "虚构用章", font=font, fill="#c83333")
        raster.putpixel((0, 0), (case["id"], 83, 17))
        path = images / (str(case["id"]) + ".png")
        raster.save(path)
        manifest.append({**case, "path": str(path)})
    (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "fixture-state.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print("ISOLATED_STACK_READY", flush=True)
    import uvicorn
    uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
