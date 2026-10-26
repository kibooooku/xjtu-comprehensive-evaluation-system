"""Associate an outcome with an exact participant, never the first grade in a roster.

Text comes from existing native/OCR regions. Table borders, when available, are
read from the already rendered page; this module performs no OCR or networking.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from PIL import Image

from . import db
from .award_scope import PROVINCIAL_QUALIFIER, scope_levels as _scope_levels


OUTCOMES = {
    "特等奖": ("AWARD", "SPECIAL"), "一等奖": ("AWARD", "FIRST"),
    "二等奖": ("AWARD", "SECOND"), "三等奖": ("AWARD", "THIRD"),
    "优秀奖": ("AWARD", "EXCELLENT"), "优秀": ("ASSESSMENT", "EXCELLENT"),
    "良好": ("ASSESSMENT", "GOOD"), "合格": ("ASSESSMENT", "PASS"),
    "不合格": ("ASSESSMENT", "FAIL"),
    "优": ("ASSESSMENT", "EXCELLENT"), "良": ("ASSESSMENT", "GOOD"),
    **{f"第{i}名": ("AWARD", f"PLACE_{i}") for i in range(1, 9)},
    **{f"第{word}名": ("AWARD", f"PLACE_{i}") for i, word in enumerate("一二三四五六七八", 1)},
}
_RESULT = re.compile("|".join(sorted(map(re.escape, OUTCOMES), key=len, reverse=True)))


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _box(points):
    from .materials import polygon_box
    try:
        value = polygon_box(points)
        return value if value[2] >= 2 and value[3] >= 2 else None
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def _union(boxes):
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[0]+b[2] for b in boxes), max(b[1]+b[3] for b in boxes)
    return [left, top, right-left, bottom-top]


def _same_row(a, b):
    overlap = min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1])
    return overlap >= min(a[3], b[3])*.45


def _segments(region):
    """Split positioned PDF rows into real, horizontally separated cells."""
    words = (region.get("extra_json") or {}).get("words") or []
    pieces, current = [], []
    for word in words:
        text, box = compact(word.get("text")), _box(word.get("polygon"))
        if not text or not box:
            continue
        if current:
            previous = current[-1][1]
            gap = box[0]-previous[0]-previous[2]
            if not _same_row(previous, box) or gap > max(4, min(previous[3], box[3])*.8) or box[0] < previous[0]:
                pieces.append(("".join(value[0] for value in current), _union([value[1] for value in current])))
                current = []
        current.append((text, box))
    if current:
        pieces.append(("".join(value[0] for value in current), _union([value[1] for value in current])))
    if not pieces:
        box = _box(region.get("polygon_json"))
        if box:
            pieces = [(compact(region.get("text")), box)]
    return [{"text": text, "box": box, "region_id": region["id"]} for text, box in pieces]


def _grade_tokens(region):
    from .materials import _identity_boxes
    values = []
    # Exact span mapping reuses the identity code so two grades in the same row
    # cannot accidentally share the first occurrence's box.
    normalized = compact(region.get("text"))
    segments = _segments(region)
    for match in _RESULT.finditer(normalized):
        text = match.group()
        if OUTCOMES[text][0] == "ASSESSMENT":
            cell = any(segment["text"] == text for segment in segments)
            labelled = re.search(r"(?:考核|考评|评定|评级|评估|等第|等级)(?:结果|成绩)?(?:为|是|[:：])?"+re.escape(text)+r"(?:[。，；;!！的]|证明|$)", normalized)
            if not cell and not labelled:
                continue  # ‘优秀团干部/优秀论文’ is an honor/title, not a rating.
        boxes = _identity_boxes(region, text, False)
        for box in boxes:
            values.append({"text": text, "kind": OUTCOMES[text][0], "value": OUTCOMES[text][1], "box": box, "region_id": region["id"]})
    unique = {(value["text"], tuple(value["box"])): value for value in values}
    return list(unique.values())


def _page_image(page):
    if not page.get("image_uri"):
        return None
    root = Path(db.storage_root).resolve()
    path = (root / page["image_uri"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    try:
        with Image.open(path) as image:
            if image.width*image.height > 36_000_000:
                return None
            return image.convert("L")
    except (OSError, ValueError):
        return None


def _table_cell(image, box):
    """Read the actual surrounding grid, including a vertically merged cell."""
    if image is None:
        return None
    width, height = image.size
    x, y, w, h = box
    if not (0 <= x < x+w <= width and 0 <= y < y+h <= height):
        return None
    pixels = image.load()
    top_sample, bottom_sample = max(0, y-5), min(height, y+h+5)
    if bottom_sample-top_sample < 8:
        return None
    def vertical(column):
        return sum(pixels[column, row] < 100 for row in range(top_sample, bottom_sample))/(bottom_sample-top_sample) >= .88
    left = next((column for column in range(max(0, x-3), max(-1, x-min(450, width//2)), -1) if vertical(column)), None)
    right = next((column for column in range(min(width-1, x+w+3), min(width, x+w+min(450, width//2))) if vertical(column)), None)
    if left is None or right is None or right-left < w+4:
        return None
    def horizontal(row):
        return sum(pixels[column, row] < 100 for column in range(left+1, right))/(right-left-1) >= .86
    top = next((row for row in range(max(0, y-2), -1, -1) if horizontal(row)), None)
    bottom = next((row for row in range(min(height-1, y+h+2), height) if horizontal(row)), None)
    if top is None or bottom is None or bottom-top < h+3:
        return None
    return [left, top, right-left, bottom-top]


def _row_regions(regions, identity):
    return [region for region in regions if (box := _box(region.get("polygon_json"))) and _same_row(box, identity["box"])]


def _row_identifiers(regions):
    numbers = set()
    for region in regions:
        raw = unicodedata.normalize("NFKC", str(region.get("text") or ""))
        numbers.update(re.findall(r"(?<![A-Za-z0-9])\d{6,18}(?![A-Za-z0-9])", raw))
        numbers.update(segment["text"] for segment in _segments(region) if re.fullmatch(r"[0-9]{6,18}", segment["text"]))
    return numbers


def _same_table_record(image, identity_box, grade_cell):
    """Merged-cell membership needs a shared grid, not merely the same height."""
    identity_cell = _table_cell(image, identity_box)
    if identity_cell is None:
        return False
    a, b = sorted((identity_cell, grade_cell), key=lambda value: value[0])
    start, end = a[0]+a[2], b[0]
    if start > end+3:
        return False
    if end-start <= 4:
        return True
    pixels = image.load()
    for baseline in (identity_cell[1], identity_cell[1]+identity_cell[3]):
        if not grade_cell[1] <= baseline <= grade_cell[1]+grade_cell[3]:
            continue
        longest, gap = 0, 0
        for x in range(start, end+1):
            dark = any(pixels[x, y] < 100 for y in range(max(0, baseline-2), min(image.height, baseline+3)))
            gap = 0 if dark else gap+1
            longest = max(longest, gap)
        if longest <= 4:
            return True
    return False


def _scope(text):
    found = _scope_levels(text)
    return next(iter(found)) if len(found) == 1 else None


def _certificate_levels(regions):
    """Resolve located certificate lines without trusting OCR response order.

    A provincial qualifier can wrap from ``...陕西`` to ``赛区一等奖``.
    Join only nearby, left-aligned paragraph lines; separate columns and far
    apart labels must not create a qualifier merely by string concatenation.
    """
    lines, levels = [], set()
    for region in regions:
        text = compact(region.get("text"))
        levels.update(_scope_levels(text))
        box = _box(region.get("polygon_json"))
        if text and box:
            lines.append((text, box))
    # Preserve the existing explicit national-final check when these two words
    # are returned on separate certificate lines. A national event brand alone
    # still cannot establish the level of its provincial award.
    text = "\n".join(compact(region.get("text")) for region in regions)
    if "全国" in text and "总决赛" in text:
        levels.add("NATIONAL")
    for before, a in lines:
        if not re.search(PROVINCIAL_QUALIFIER + r"$", before):
            continue
        for after, b in lines:
            height = max(a[3], b[3])
            gap = b[1] - a[1] - a[3]
            if (after.startswith("赛区") and b[1] >= a[1] + a[3] * .5
                    and -.15 * height <= gap <= 1.5 * height
                    and abs(a[0] - b[0]) <= 1.25 * height):
                levels.add("PROVINCIAL")
    return levels


def _event_label(text):
    """A document title can link attachments, but never establishes a level."""
    value = re.sub(r"\.(?:pdf|png|jpe?g|webp)$", "", str(text or ""), flags=re.I)
    value = re.sub(r"[^A-Za-z\u4e00-\u9fff]", "", compact(value))
    for word in ("西安交通大学", "大学生", "学年度", "学年", "春季", "秋季", "夏季", "冬季", "学期",
                 "综合素质", "具体名单", "获奖名单", "考核结果", "考评结果", "文体竞赛", "社会实践",
                 "竞赛", "比赛", "活动", "证明材料", "证明", "材料", "名单", "获奖", "附件", "考核", "考评", "实践"):
        value = value.replace(word, "")
    return re.sub(r"^[春夏秋冬]", "", value).casefold()


def _context_regions(data, headings_only=False):
    """Exclude personal table rows before offering shared background to policy."""
    selected = []
    personal_rows = [box for region in data["regions"]
                     if re.search(r"(?<!\d)\d{6,18}(?!\d)", compact(region.get("text")))
                     and (box := _box(region.get("polygon_json")))]
    for region in data["regions"]:
        text, box = compact(region.get("text")), _box(region.get("polygon_json"))
        if not text or not box or any(_same_row(box, personal_box) for personal_box in personal_rows):
            continue
        if headings_only and (box[1] > data["page"]["height_px"]*.25 or not re.search(r"竞赛|比赛|活动|培训|实践|项目|考核|考评|任职|名单|志愿|见习", text)):
            continue
        selected.append(region)
    selected.sort(key=lambda region: ((_box(region.get("polygon_json")) or [0, 0])[1], (_box(region.get("polygon_json")) or [0])[0]))
    return selected


def _personal_contexts(records):
    contexts, seen = [], set()
    for identity, data, row in records:
        if not row:
            continue
        regions = row if data["roster"] else data["regions"]
        text = "\n".join(region.get("text") or "" for region in regions)[:6000]
        key = (data["page"]["id"], text)
        if not text.strip() or key in seen:
            continue
        seen.add(key)
        contexts.append({"text": text, "file_id": data["page"]["file_id"], "page_id": data["page"]["id"],
                         "association": "SAME_ROW" if data["roster"] else "CERTIFICATE"})
    return contexts


def _common_contexts(conn, information, records):
    """Bind genuine headings or a named, explicitly attached roster cover.

    A cover's explicit level can apply to its list even when every personal row
    contains only an award. A filename is a relationship clue only: its text is
    never passed to the level parser or used as a scoring fact.
    """
    by_file = {}
    for data in information.values():
        by_file.setdefault(data["page"]["file_id"], []).append(data)
    for values in by_file.values():
        values.sort(key=lambda data: data["page"]["page_index"])
    files = {file_id: db.one(conn, "file_objects", file_id) or {} for file_id in by_file}
    targets = {data["page"]["file_id"] for _, data, _ in records}
    contexts, seen = [], set()

    def append(data, text, target_id, association, regions):
        key = (data["page"]["id"], target_id, association)
        if not text or key in seen:
            return
        seen.add(key)
        contexts.append({"text": text[:6000], "file_id": data["page"]["file_id"], "page_id": data["page"]["id"],
                         "target_file_id": target_id, "association": association, "award_levels": sorted(_certificate_levels(regions))})

    for target_id in targets:
        target_pages = by_file[target_id]
        first = target_pages[0]
        heading_regions = _context_regions(first, headings_only=True)
        heading = "\n".join(region.get("text") or "" for region in heading_regions)
        first_regions = _context_regions(first)
        first_body = "\n".join(region.get("text") or "" for region in first_regions)
        if not _row_identifiers(first["regions"]) and _attachment_reference(first_body):
            # The first page may be an actual cover with its level below the
            # top-quarter title area and the personal list on later pages.
            heading = first_body
            heading_regions = first_regions
        if heading:
            append(first, heading, target_id, "FILE_HEADING", heading_regions)
        # A bare list needs an explicit cover reference and a substantial event
        # name found in the cover's *body*. Unrelated attached certificates and
        # a school logo cannot provide the applicant's competition level.
        target_label = _event_label(files[target_id].get("original_name"))
        if len(target_label) < 4:
            continue
        target_has_roster = any(data["roster"] for data in target_pages)
        if not target_has_roster:
            continue
        for source_id, source_pages in by_file.items():
            if source_id == target_id:
                continue
            for source in source_pages:
                regions = _context_regions(source)
                text = "\n".join(region.get("text") or "" for region in regions)
                value = compact(text)
                if not _attachment_reference(value):
                    continue
                source_label = _event_label(files[source_id].get("original_name"))
                body_label = _event_label(text)
                shared = target_label if target_label in source_label else (source_label if source_label in target_label else "")
                if len(shared) < 4 or shared not in body_label:
                    continue
                append(source, text, target_id, "ATTACHED_CONTEXT", regions)
    return contexts


def _attachment_reference(text):
    return bool(re.search(r"(?:名单.{0,12}(?:见|详见|参见|附件)|(?:见|详见|参见).{0,6}(?:附件|名单))", compact(text)))


def _prize_system(text):
    value = compact(text)
    return bool(re.search(r"(?:竞赛|比赛|奖次|奖项)", value) and
                (re.search(r"一(?:等奖|等)?[、，,/与和及]二(?:等奖|等)?[、，,/与和及]三等奖", value)
                 or re.search(r"一等奖.{0,12}二等奖.{0,12}三等奖", value)))


def _heading_outcome(data, identity, information):
    """A genuine list section heading can label a following/continued roster."""
    page = data["page"]
    sources = [data]
    if re.search(r"续表|接上页|续上页", compact(data["text"])):
        sources += [other for other in information.values() if other["page"]["file_id"] == page["file_id"] and other["page"]["page_index"] == page["page_index"]-1]
    headings = []
    for source in sources:
        for region in source["regions"]:
            text = compact(region.get("text"))
            tokens = _grade_tokens(region)
            if len(tokens) != 1:
                continue
            value = tokens[0]
            box = _box(region.get("polygon_json"))
            if not box:
                continue
            labelled = bool(re.fullmatch(r"(?:国际级|国家级|全国|省级|校级)?(?:获奖|奖励|考核|考评|评定|评级|结果|等第|考核结果|考评结果)?[:：]?"+re.escape(value["text"])+r"(?:获奖)?名单", text))
            centered_heading = text == value["text"] and .25 <= (box[0]+box[2]/2)/page["width_px"] <= .75 and box[3] >= identity["box"][3]*1.3
            if not labelled and not centered_heading:
                continue
            if source is data and box[1]+box[3] > identity["box"][1]:
                continue
            headings.append(({**value, "page_id": source["page"]["id"], "page_number": source["page"]["page_index"], "file_id": source["page"]["file_id"], "award_level": _scope(text)}, source["page"]["page_index"], box[1]))
    return max(headings, key=lambda value: (value[1], value[2]))[0] if headings else None


def _team_outcome(data, identity, information):
    """Join pages only through an explicit team identifier and team outcome label."""
    page, regions = data["page"], data["regions"]
    image = None
    team_keys = set()
    for region in regions:
        for segment in _segments(region):
            keys = re.findall(r"XJTU\d{8,14}", segment["text"], flags=re.I)
            if not keys:
                continue
            related = _same_row(segment["box"], identity["box"])
            if not related:
                if image is None:
                    image = _page_image(page)
                cell = _table_cell(image, segment["box"])
                related = bool(cell and cell[1] < identity["box"][1]+identity["box"][3]/2 < cell[1]+cell[3])
            if related:
                team_keys.update(key.casefold() for key in keys)
    linked = []
    for source in information.values():
        if source["page"]["file_id"] != page["file_id"]:
            continue
        for region in source["regions"]:
            text = compact(region.get("text"))
            keys = [key for key in team_keys if key in text.casefold()]
            if not keys or not re.search(r"团队(?:考核|考评|评级|获奖|等第|结果)|团队编号.{0,60}(?:考核结果|考评结果|考评等第)", text):
                continue
            tokens = _grade_tokens(region)
            if len({(value["kind"], value["value"]) for value in tokens}) == 1:
                for value in tokens:
                    linked.append({**value, "page_id": source["page"]["id"], "page_number": source["page"]["page_index"], "file_id": source["page"]["file_id"], "award_level": _scope(text)})
    return linked


def linked_outcome(conn, pages, student, student_user, class_name, automatic=True, identity_hits=None):
    """Return only participant-bound facts, plus their original-page anchors."""
    from .materials import identity_matches, _automatic_regions, _page_regions, _file_policy
    matches = identity_hits if identity_hits is not None else identity_matches(conn, pages, student, student_user, class_name, automatic=automatic)
    exact = [match for match in matches if match["field"] == "STUDENT_NUMBER"]
    # A class match alone never identifies a person. An exact number displaces
    # same-name matches elsewhere in that file, but not name-only certificates.
    number_files = {next(page["file_id"] for page in pages if page["id"] == match["page_id"]) for match in exact}
    identities = [match for match in matches if match["field"] == "STUDENT_NUMBER" or (match["field"] == "NAME" and next(page["file_id"] for page in pages if page["id"] == match["page_id"]) not in number_files)]
    information, all_anchors = {}, []
    roster = False
    policies = {}
    for page in pages:
        if automatic and page["file_id"] not in policies:
            policies[page["file_id"]] = _file_policy(conn, page["file_id"])
        regions = (_automatic_regions(conn, page, policies[page["file_id"]]) if automatic else _page_regions(conn, page))
        regions = [region for region in regions if region["kind"] == "TEXT"]
        text = "\n".join(region.get("text") or "" for region in regions)
        numbers = set(re.findall(r"(?<!\d)\d{8,12}(?!\d)", compact(text)))
        is_roster = bool(re.search(r"名单|名册", text) or len(numbers) >= 3 or sum(len(_grade_tokens(region)) for region in regions) >= 4)
        roster |= is_roster
        information[page["id"]] = {"page": page, "regions": regions, "text": text, "roster": is_roster}
    seen, validated_identities, personal_records = set(), [], []
    for identity in identities:
        data = information[identity["page_id"]]
        page, regions = data["page"], data["regions"]
        row = _row_regions(regions, identity)
        row_identifiers = _row_identifiers(row)
        if identity["field"] == "STUDENT_NUMBER":
            row_identifiers.add(compact(student.get("student_number")))
        if len(row_identifiers) > 1:
            continue  # Two independent person records on one baseline are not one row.
        if identity["field"] == "NAME" and data["roster"]:
            row_numbers = set(re.findall(r"(?<!\d)\d{8,12}(?!\d)", compact(" ".join(region.get("text") or "" for region in row))))
            if row_numbers and compact(student.get("student_number")) not in row_numbers:
                continue
            names = [value for value in identities if value["field"] == "NAME" and information[value["page_id"]]["page"]["file_id"] == page["file_id"]]
            if len(names) > 1:
                continue
        validated_identities.append(identity)
        personal_records.append((identity, data, row))
        tokens = [value for region in row for value in _grade_tokens(region)]
        row_keys = {(value["kind"], value["value"]) for value in tokens}
        chosen = []
        # Exactly one result on this participant's text baseline is safe. An
        # adjacent participant's distinct result makes the row ambiguous.
        if len(row_keys) == 1 and len(tokens) == 1:
            chosen = [(value, "SAME_ROW") for value in tokens if _same_row(value["box"], identity["box"])]
        if not chosen:
            image = _page_image(page)
            for region in regions:
                segments = _segments(region)
                for value in _grade_tokens(region):
                    if not any(segment["text"] == value["text"] and _same_row(segment["box"], value["box"]) and abs(segment["box"][0]-value["box"][0]) <= 4 for segment in segments):
                        continue
                    cell = _table_cell(image, value["box"])
                    center = identity["box"][1]+identity["box"][3]/2
                    if cell and cell[1] < center < cell[1]+cell[3] and _same_table_record(image, identity["box"], cell):
                        chosen.append((value, "TABLE_CELL"))
            if len({tuple(value[0]["box"]) for value in chosen}) > 1:
                chosen = []
        if not chosen and not data["roster"]:
            candidates = [value for region in regions for value in _grade_tokens(region)]
            kinds = {(value["kind"], value["value"]) for value in candidates}
            if len(kinds) == 1 and len(candidates) <= 2:
                chosen = [(value, "CERTIFICATE") for value in candidates]
        if not chosen:
            heading = _heading_outcome(data, identity, information)
            if heading:
                chosen = [(heading, "SECTION")]
        if not chosen:
            chosen = [(value, "TEAM") for value in _team_outcome(data, identity, information)]
        for value, association in chosen:
            source_page = information[value["page_id"]]["page"] if value.get("page_id") else page
            key = (source_page["id"], value["kind"], value["value"], tuple(value["box"]))
            if key in seen:
                continue
            seen.add(key)
            level = value.get("award_level")
            scope_conflict = False
            if association in ("SAME_ROW", "TABLE_CELL", "CERTIFICATE") and not data["roster"]:
                certificate_levels = _certificate_levels(regions)
                level = next(iter(certificate_levels)) if len(certificate_levels) == 1 else None
                scope_conflict = len(certificate_levels) > 1
            elif association in ("SAME_ROW", "TABLE_CELL"):
                row_levels = _scope_levels("\n".join(region.get("text") or "" for region in row))
                level = next(iter(row_levels)) if len(row_levels) == 1 else None
                scope_conflict = len(row_levels) > 1
            all_anchors.append({**value, "award_level": level, "award_level_conflict": scope_conflict, "page_id": source_page["id"], "page_number": source_page["page_index"], "file_id": source_page["file_id"], "identity_page_id": identity["page_id"], "identity_box": identity["box"], "association": association})
    personal = _personal_contexts(personal_records)
    common = _common_contexts(conn, information, personal_records)
    identity_files = {identity["page_id"]: data["page"]["file_id"] for identity, data, _ in personal_records}
    for anchor in all_anchors:
        background = [context for context in common if context["target_file_id"] == identity_files.get(anchor["identity_page_id"])]
        levels = {level for context in background for level in context["award_levels"]}
        if anchor.get("award_level"):
            levels.add(anchor["award_level"])
        if levels:
            conflict = len(levels) > 1 or anchor.get("award_level_conflict", False)
            anchor.update(award_level=next(iter(levels)) if len(levels) == 1 and not conflict else None, award_level_conflict=conflict)
    context_metadata = {"personal_contexts": personal, "common_contexts": common}
    kinds = {(value["kind"], value["value"]) for value in all_anchors}
    # Do not let a certificate and an unrelated roster dictate two different
    # awards for a single small item. Mixed outcomes require human confirmation.
    if len(kinds) > 1:
        return {"status": "AMBIGUOUS", "facts": {}, "anchors": [], "warnings": ["找到多个不同的本人奖次或考评，请对照原件确认。"], "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities), **context_metadata}
    if not all_anchors:
        return {"status": "NOT_FOUND", "facts": {}, "anchors": [], "warnings": ["未能可靠对应本人奖次或考评，请在原件中定位并核对。"], "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities), **context_metadata}
    kind, value = next(iter(kinds))
    # Prefer an anchor actually sharing the selected identity row.
    all_anchors.sort(key=lambda anchor: (anchor["association"] != "SAME_ROW", anchor["page_number"], anchor["box"][1]))
    facts = {"award_grade" if kind == "AWARD" else "assessment_result": value}
    levels = {anchor["award_level"] for anchor in all_anchors if anchor.get("award_level")}
    scope_conflict = len(levels) > 1 or any(anchor.get("award_level_conflict") for anchor in all_anchors)
    if len(levels) == 1 and not scope_conflict:
        facts["award_level"] = next(iter(levels))
    anchor_files = {identity_files.get(anchor["identity_page_id"]) for anchor in all_anchors}
    if kind == "AWARD" and any(_prize_system(context["text"]) for context in common if context["target_file_id"] in anchor_files):
        facts["award_system"] = "PRIZE"
    warnings = ["材料中的赛事级别有冲突，请核对本人比赛轮次。"] if scope_conflict else []
    return {"status": "MATCHED", "facts": facts, "anchors": all_anchors, "warnings": warnings, "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities), "award_level_checked": True, **context_metadata}
