"""Large certificate provider copies and one complete activity-name crop."""
import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image

from app import db, integrations, materials
from app.integrations import ProviderError
from test_materials import material_app, create_claim, png, upload, _text_run


CONFIG = {"secret_id": "synthetic", "secret_key": "synthetic", "region": "ap-shanghai"}


def test_large_page_copy_keeps_resolution_and_sends_one_bounded_jpeg(monkeypatch):
    original = png(900, 1200) + b" " * 7_200_000
    digest = hashlib.sha256(original).hexdigest()
    calls = []
    client = httpx.Client

    def respond(request):
        payload = json.loads(request.content)
        encoded = payload["ImageBase64"]
        assert len(encoded) <= integrations.OCR_BASE64_BUDGET and payload["IsWords"]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            assert image.format == "JPEG" and image.size == (900, 1200)
        calls.append(request)
        return httpx.Response(200, json={"Response": {"TextDetections": [{"DetectedText": "证书中的具体活动", "Polygon": [{"X": 40, "Y": 50}], "Confidence": 99}]}})

    monkeypatch.setattr(integrations.httpx, "Client", lambda *args, **kwargs: client(transport=httpx.MockTransport(respond)))
    result = integrations.tencent_ocr("GeneralAccurateOCR", original, CONFIG)
    assert len(calls) == 1 and result["TextDetections"][0]["Polygon"] == [{"X": 40, "Y": 50}]
    assert hashlib.sha256(original).hexdigest() == digest


def test_smaller_rasters_are_used_only_when_quality_compression_is_insufficient(monkeypatch):
    image = Image.effect_noise((1600, 2000), 100).convert("RGB")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    monkeypatch.setattr(integrations, "OCR_BASE64_BUDGET", 550_000)
    data, scale_x, scale_y = integrations.prepare_ocr_image(stream.getvalue())
    with Image.open(io.BytesIO(data)) as prepared:
        assert prepared.width < image.width and prepared.height < image.height
        assert min(prepared.size) >= 600
        assert scale_x == image.width / prepared.width and scale_y == image.height / prepared.height
    assert 4*((len(data)+2)//3) <= 550_000


def test_provider_polygons_words_and_both_seal_shapes_return_to_original_pixels(monkeypatch):
    monkeypatch.setattr(integrations, "prepare_ocr_image", lambda image: (b"bounded-image", 2.0, 3.0))
    value = {"TextDetections": [{"DetectedText": "证书标题", "Confidence": 98, "Polygon": [{"X": 5, "Y": 7}], "ItemPolygon": {"X": 5, "Y": 7, "Width": 30, "Height": 11}, "Words": [{"Character": "证", "Confidence": 98}], "WordCoordPoint": [{"WordCoordinate": [{"X": 8, "Y": 9}]}]}], "SealInfos": [{"Location": {"X": 12, "Y": 13, "Width": 40, "Height": 30}}], "Location": {"X": 12, "Y": 13, "Width": 40, "Height": 30}}
    client = httpx.Client
    monkeypatch.setattr(integrations.httpx, "Client", lambda *args, **kwargs: client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"Response": value}))))
    result = integrations.tencent_ocr("GeneralAccurateOCR", b"source", CONFIG)
    line = result["TextDetections"][0]
    assert line["Polygon"] == [{"X": 10, "Y": 21}] and line["Confidence"] == 98
    assert line["ItemPolygon"] == {"X": 10, "Y": 21, "Width": 60, "Height": 33}
    assert line["WordCoordPoint"][0]["WordCoordinate"] == [{"X": 16, "Y": 27}]
    assert result["SealInfos"][0]["Location"] == result["Location"] == {"X": 24, "Y": 39, "Width": 80, "Height": 90}


def test_unreadable_oversized_input_stops_without_a_network_request(monkeypatch):
    monkeypatch.setattr(integrations, "OCR_BASE64_BUDGET", 30)
    monkeypatch.setattr(integrations.httpx, "Client", lambda *args, **kwargs: pytest.fail("must not call provider"))
    with pytest.raises(ProviderError) as caught:
        integrations.tencent_ocr("SealOCR", b"not-an-image" * 30, CONFIG)
    assert caught.value.code == "ImageTooLarge"


def test_successful_source_cache_survives_provider_copy_preparation(material_app, monkeypatch):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = upload(client, claim["id"])
    with db.write() as conn:
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        fingerprint = hashlib.sha256((page["render_sha256"] + "GeneralAccurateOCR" + "v2-words").encode()).hexdigest()
        run = db.add(conn, "recognition_runs", page_id=page["id"], provider="TENCENT", api_name="GeneralAccurateOCR", parameters_hash=fingerprint, status="SUCCEEDED", result_json={"TextDetections": []}, finished_at=db.now())
    monkeypatch.setattr(materials, "tencent_ocr", lambda *args: pytest.fail("successful source must stay cached"))
    assert materials._ocr_page(page, "GeneralAccurateOCR", {})["id"] == run["id"]


def _region(identifier, text, box, kind="TEXT"):
    return {"id": identifier, "kind": kind, "text": text, "polygon_json": materials._polygon(box)}


def test_one_activity_name_image_contains_complete_multiline_title_not_watermarks():
    page = {"width_px": 1200, "height_px": 1800}
    regions = [_region("logo", "蓝桥杯大赛", [300, 100, 550, 80]), _region("watermark", "蓝桥杯", [100, 450, 200, 100]),
               _region("first", "荣获第十七届蓝桥杯全国大学生软件和信息", [200, 800, 800, 50]),
               _region("second", "技术大赛软件赛Python程序设计大学A组全国", [160, 880, 850, 60]),
               _region("third", "总决赛二等奖", [160, 965, 420, 60]),
               _region("footer", "蓝桥杯大赛组委会", [650, 1500, 350, 50]), _region("seal-word", "蓝桥杯全国大学生", [650, 1430, 300, 50]),
               _region("seal", "印章", [620, 1390, 380, 300], "SEAL")]
    candidate, _ = materials._activity_name_block(page, regions, ["第十七届蓝桥杯全国大学生软件和信息技术大赛软件赛Python程序设计大学A组全国总决赛"])
    assert candidate["box"] == [150, 792, 870, 241]
    assert candidate["region_id"] == "first"


def test_title_continuations_do_not_join_another_named_activity_or_another_page():
    page = {"width_px": 1000, "height_px": 1600}
    regions = [_region("one", "仲英书院周末广场舞活动", [80, 100, 600, 40]), _region("two", "仲英书院理论学习讲座活动", [80, 160, 600, 40])]
    value, _ = materials._activity_name_block(page, regions, [])
    assert value["box"][3] == 56


@pytest.mark.parametrize("box", [[1100, 100, 500, 40], [100, 1800, 600, 40]])
def test_activity_name_region_outside_original_page_is_skipped(box):
    page = {"width_px": 1000, "height_px": 1600}
    regions = [_region("outside", "第十七届蓝桥杯全国大赛", box)]
    assert materials._activity_name_block(page, regions, []) is None


@pytest.mark.parametrize("protection", [None, "duplicate_current", "frozen", "manual_flag", "manual_name"])
def test_only_old_automatic_name_snippets_are_replaced_with_one_block(material_app, protection):
    _, login = material_app
    client = login("student")
    claim, _, _ = create_claim(client)
    file = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        run = _text_run(conn, page, [("蓝桥杯大赛", [30, 10, 180, 15]), ("第十七届蓝桥杯视觉艺术设计赛", [20, 80, 220, 20])])
        regs = db.rows(conn, "recognition_regions", run_id=run["id"])
        old = []
        for index, region in enumerate(regs):
            old.append(materials.create_crop(conn, revision, page, "VALIDITY", materials.polygon_box(region["polygon_json"]), source="STUDENT" if protection == "manual_name" and index == 0 else "AI", region_id=region["id"], metadata={"validity_kind": "NAME"}))
        suggestions = materials._evidence_suggestions(conn, [page], context["student"], context["user"], context["class"]["name"])
        assert len([item for item in suggestions if item.get("validity_kind") == "NAME"]) == 1
        if protection == "duplicate_current":
            desired = next(item for item in suggestions if item.get("validity_kind") == "NAME")
            for _ in range(2):
                old.append(materials.create_crop(conn, revision, page, "VALIDITY", desired["box"], source="AI", region_id=desired["region_id"], metadata={"validity_kind": "NAME"}))
        if protection == "frozen":
            revision = db.change(conn, "application_revisions", revision["id"], content_frozen_at=db.now())
        if protection == "manual_flag":
            revision = db.change(conn, "application_revisions", revision["id"], payload_json={"evidence_manually_edited": True})
        materials._apply_evidence(conn, revision, suggestions)
        current = db.rows(conn, "evidence_crops", revision_id=revision["id"])
        if protection in ("frozen", "manual_flag"):
            assert {item["id"] for item in current} == {item["id"] for item in old}
        elif protection == "manual_name":
            assert len(current) == 1 and current[0]["id"] == old[0]["id"]
        else:
            assert len(current) == 1 and current[0]["source"] == "AI"
            assert materials.polygon_box(current[0]["polygon_json"]) == next(item["box"] for item in suggestions if item.get("validity_kind") == "NAME")
