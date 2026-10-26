"""Fragmented numeric identifiers must never escape into public evidence."""
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw

from app import db, materials
from test_materials import material_app, create_claim, upload


def _fragmented_crop(client, api, *, missing_geometry=False):
    claim, year, classroom = create_claim(client)
    file = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", file["pages"][0]["id"])
        run = db.add(conn, "recognition_runs", page_id=page["id"], provider="LOCAL_PDF" if api == materials.NATIVE_TEXT_API else "TENCENT", api_name=api, parameters_hash="synthetic-fragment-redaction", status="SUCCEEDED", result_json={"usable": True, "text": "编号1234567890"} if api == materials.NATIVE_TEXT_API else {}, started_at=None, finished_at=db.now())
        boxes = [[20, 20, 25, 12], [48, 20, 25, 12], [76, 20, 35, 12]]
        for index, (text, box) in enumerate(zip(("123", "456", "7890"), boxes)):
            db.add(conn, "recognition_regions", run_id=run["id"], region_no=index, kind="TEXT", text=text, confidence=None if api == materials.NATIVE_TEXT_API else Decimal("1"), polygon_json=[] if missing_geometry and index == 1 else materials._polygon(box), extra_json={})
        crop = materials.create_crop(conn, revision, page, "VALIDITY", [0, 0, 240, 90], source="STUDENT", metadata={"validity_kind": "NAME"})
    return claim, year, classroom, crop, boxes


@pytest.mark.parametrize("api", [materials.NATIVE_TEXT_API, "GeneralAccurateOCR"])
def test_each_short_numeric_fragment_gets_covered_in_public_asset(material_app, api):
    _, login = material_app
    _, _, _, crop, boxes = _fragmented_crop(login("student"), api)
    assert crop["public_asset_status"] == "READY"
    assert len(crop["masks_json"]["automatic"]) == 3
    with Image.open(materials.asset_path(crop["public_asset_uri"])) as image:
        for box in boxes:
            assert image.getpixel((box[0]+2, box[1]+2)) == (37, 50, 68)
        assert image.getpixel((190, 70)) == (255, 255, 255)


@pytest.mark.parametrize("api", [materials.NATIVE_TEXT_API, "GeneralAccurateOCR"])
def test_fragment_without_reliable_geometry_blocks_public_asset(material_app, api):
    _, login = material_app
    _, _, _, crop, _ = _fragmented_crop(login("student"), api, missing_geometry=True)
    assert crop["public_asset_status"] == "BLOCKED"
    assert crop["public_asset_uri"] is None


@pytest.mark.parametrize("coverage,expected_status", [("automatic", 200), ("manual_union", 200), ("missing", 404), ("partial", 404)])
def test_old_ready_asset_is_read_only_rechecked_before_public_get(material_app, coverage, expected_status):
    _, login = material_app
    student = login("student")
    claim, year, classroom, crop, boxes = _fragmented_crop(student, materials.NATIVE_TEXT_API)
    with db.write() as conn:
        metadata = dict(crop["masks_json"])
        if coverage == "manual_union":
            metadata["automatic"] = []
            metadata["manual"] = [part for x, y, w, h in boxes for part in ([x, y, w//2, h], [x+w//2, y, w-w//2, h])]
        elif coverage == "missing":
            metadata["automatic"] = []
        elif coverage == "partial":
            metadata["automatic"] = metadata["automatic"][:1]
        db.change(conn, "evidence_crops", crop["id"], masks_json=metadata)
    if coverage in ("missing", "partial"):
        # Reproduce an old unsafe asset before freezing: its bytes really lack
        # some masks, rather than merely asserting against inconsistent metadata.
        image = Image.new("RGB", (240, 90), "white")
        draw = ImageDraw.Draw(image)
        for x, y, width, height in metadata["automatic"]:
            draw.rectangle((x, y, x+width, y+height), fill="#253244")
        materials._write_asset(crop["public_asset_uri"], materials._png(image))
    # An old successful asset keeps the same version; no broad version cutoff.
    assert crop["redaction_version"] == materials.REDACTION_VERSION
    assert student.put(f"/api/applications/{claim['id']}", json={"title": "虚构活动参与证明", "requested_score": "0.30", "payload_json": {}}).status_code == 200
    assert student.post(f"/api/applications/{claim['id']}/submit", json={}).status_code == 200
    reviewer = login("reviewer1")
    assert reviewer.post(f"/api/applications/{claim['id']}/review", json={"action": "APPROVE"}).status_code == 200
    counselor = login("counselor")
    opened = counselor.put("/api/publicity", json={"class_id": classroom, "year_id": year, "is_open": True})
    assert opened.status_code == 200, opened.text
    with db.read() as conn:
        before = db.one(conn, "evidence_crops", crop["id"])
        revision = db.one(conn, "application_revisions", before["revision_id"])
        assert revision["content_frozen_at"]
    asset = materials.asset_path(crop["public_asset_uri"])
    before_bytes = asset.read_bytes()
    assert login("student2").get(f"/api/crops/{crop['id']}/public").status_code == expected_status
    with db.read() as conn:
        assert db.one(conn, "evidence_crops", crop["id"]) == before
    assert asset.read_bytes() == before_bytes
