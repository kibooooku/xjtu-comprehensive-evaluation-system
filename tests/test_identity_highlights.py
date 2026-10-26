"""Exact account identifiers map to their own glyphs, without numeric cell joins."""
import pytest
from PIL import Image

from app import db, materials
from test_materials import material_app, create_claim, upload


NUMBER = "2024001234"


def _region(text, *, space=6):
    words, x, y = [], 10, 10
    for char in text:
        if char in "\r\n":
            x, y = 10, y + 40
        elif char.isspace():
            x += space
        else:
            words.append({"text": char, "polygon": materials._polygon([x, y, 9, 16])})
            x += 10
    return {"text": text, "extra_json": {"words": words}, "polygon_json": materials._polygon([0, 0, 800, 150])}


def test_class_number_space_preserves_student_identifier_boundary_and_its_box():
    region = _region("软件2401 " + NUMBER, space=40)
    boxes = materials._identity_boxes(region, NUMBER, True)
    assert boxes == [[110, 10, 99, 16]]


@pytest.mark.parametrize("text", ["9" + NUMBER, NUMBER + "8", "2O24001234"])
def test_longer_or_lookalike_number_is_not_an_account_match(text):
    assert not materials._identity_boxes(_region(text), NUMBER, True)


def test_fullwidth_spaced_digits_use_canonical_identifier_and_tight_glyph_geometry():
    text = " ".join(chr(ord("０") + int(char)) for char in NUMBER)
    assert materials._identity_boxes(_region(text), NUMBER, True) == [[10, 10, 153, 16]]


@pytest.mark.parametrize("text", [" ".join(NUMBER + "9"), " ".join("9" + NUMBER), NUMBER + " 9", "9 " + NUMBER])
def test_spaced_longer_identifiers_cannot_expose_an_account_prefix_or_suffix(text):
    assert not materials._identity_boxes(_region(text), NUMBER, True)


def test_separate_numeric_table_columns_are_allowed_only_with_a_clear_geometric_gap():
    region = _region("2401 " + NUMBER + " 30", space=80)
    assert materials._identity_boxes(region, NUMBER, True) == [[130, 10, 99, 16]]


def test_complete_occurrence_after_a_longer_number_gets_its_own_coordinates():
    # The old boundary search accepted the second occurrence while text.find
    # selected the first occurrence's box inside somebody else's longer number.
    region = _region("9" + NUMBER + "\n" + NUMBER)
    assert materials._identity_boxes(region, NUMBER, True) == [[10, 50, 99, 16]]


@pytest.mark.parametrize("polygon", [[], materials._polygon([20, 10, 0, 16]), [{"X": float("nan"), "Y": 10}]])
def test_incomplete_glyph_geometry_uses_known_enclosing_bounds_not_a_partial_number(polygon):
    region = _region(NUMBER)
    region["extra_json"]["words"][3]["polygon"] = polygon
    assert materials._identity_boxes(region, NUMBER, True) == [[0, 0, 800, 150]]


@pytest.mark.parametrize("region", [_region(NUMBER[:5] + "\n" + NUMBER[5:]), _region(NUMBER[:5] + "    " + NUMBER[5:], space=50), {"text": NUMBER[:5] + " " + NUMBER[5:], "extra_json": {}, "polygon_json": materials._polygon([0, 0, 200, 30])}])
def test_separate_rows_cells_or_unpositioned_digit_groups_never_form_an_identifier(region):
    assert not materials._identity_boxes(region, NUMBER, True)


def _claim_with_identity(client):
    claim, _, _ = create_claim(client)
    uploaded = upload(client, claim["id"])
    with db.write() as conn:
        context = materials._app_context(conn, claim["id"])
        revision = db.one(conn, "application_revisions", context["application"]["working_revision_id"])
        page = db.one(conn, "file_pages", uploaded["pages"][0]["id"])
        text = context["class"]["name"] + " " + context["student"]["student_number"]
        value = _region(text, space=40)
        run = db.add(conn, "recognition_runs", page_id=page["id"], provider="TENCENT", api_name="GeneralAccurateOCR", parameters_hash="synthetic-identity", status="SUCCEEDED", result_json={}, started_at=db.now(), finished_at=db.now())
        region = db.add(conn, "recognition_regions", run_id=run["id"], region_no=0, kind="TEXT", confidence=1, text=text, polygon_json=value["polygon_json"], extra_json=value["extra_json"])
    return claim, context, revision, page, region


def test_local_suggestions_prioritize_number_and_refresh_same_bounds_automatic_red_frames(material_app):
    _, login = material_app
    claim, context, revision, page, region = _claim_with_identity(login("student"))
    with db.write() as conn:
        suggestions = materials._evidence_suggestions(conn, [page], context["student"], context["user"], context["class"]["name"])
        participation = next(item for item in suggestions if item["evidence_type"] == "PARTICIPATION")
        assert participation["highlights"][0]["field"] == "STUDENT_NUMBER"
        assert participation["box"][2:] == [page["width_px"], round(page["height_px"] / 4)]
        previous = materials.create_crop(conn, revision, page, "PARTICIPATION", participation["box"], source="AI", region_id=region["id"], metadata={"highlights": []})
        validity = materials.create_crop(conn, revision, page, "VALIDITY", [0, 0, 50, 20], source="AI", metadata={"validity_kind": "NAME"})
        materials._apply_evidence(conn, revision, [participation])
        crops = db.rows(conn, "evidence_crops", revision_id=revision["id"])
        current = next(crop for crop in crops if crop["evidence_type"] == "PARTICIPATION")
        assert current["id"] != previous["id"]
        assert db.one(conn, "evidence_crops", validity["id"])
        assert len([crop for crop in crops if crop["evidence_type"] == "PARTICIPATION"]) == 1
        assert current["masks_json"]["evidence"]["highlights"][0]["field"] == "STUDENT_NUMBER"
        number_box = participation["highlights"][0]["box"]
        origin = materials.polygon_box(current["polygon_json"])
        with Image.open(materials.asset_path(current["private_asset_uri"])) as image:
            assert image.getpixel((number_box[0] - origin[0], number_box[1] - origin[1])) == (224, 36, 36)
        materials._apply_evidence(conn, revision, [participation])
        assert db.one(conn, "evidence_crops", current["id"])  # Identical results stay stable.


@pytest.mark.parametrize("protection", ["frozen", "manual_flag", "legacy_manual"])
def test_new_number_suggestions_never_replace_protected_participation(material_app, protection):
    _, login = material_app
    claim, context, revision, page, region = _claim_with_identity(login("student"))
    with db.write() as conn:
        previous = materials.create_crop(conn, revision, page, "PARTICIPATION", [0, 50, page["width_px"], round(page["height_px"] / 4)], source="STUDENT" if protection == "legacy_manual" else "AI")
        if protection == "frozen":
            revision = db.change(conn, "application_revisions", revision["id"], content_frozen_at=db.now())
        elif protection == "manual_flag":
            revision = db.change(conn, "application_revisions", revision["id"], payload_json={"evidence_manually_edited": True})
        suggestions = materials._evidence_suggestions(conn, [page], context["student"], context["user"], context["class"]["name"])
        materials._apply_evidence(conn, revision, suggestions)
        assert db.one(conn, "evidence_crops", previous["id"]) == previous
