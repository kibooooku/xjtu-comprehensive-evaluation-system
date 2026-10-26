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
    value = compact(text)
    found = set()
    for pattern, level in ((r"国际(?:级|赛|赛事|比赛|竞赛|一等|二等|三等|特等)|世界(?:赛|大赛|比赛)", "INTERNATIONAL"),
                           (r"国家(?:级|赛|一等|二等|三等|特等)|全国(?:总决赛|赛|大赛|竞赛|比赛|一等|二等|三等|特等)", "NATIONAL"),
                           (r"省(?:级|赛)|省级比赛|省赛", "PROVINCIAL"), (r"校级|校赛", "SCHOOL")):
        if re.search(pattern, value):
            found.add(level)
    if "全国" in value and "总决赛" in value:
        found.add("NATIONAL")
    if re.search(r"(?:北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆)(?:省|市|自治区)?赛区", value):
        found.add("PROVINCIAL")
    return next(iter(found)) if len(found) == 1 else None


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
    seen, validated_identities = set(), []
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
            if association in ("SAME_ROW", "TABLE_CELL"):
                level = _scope("\n".join(region.get("text") or "" for region in row))
            elif association == "CERTIFICATE":
                level = _scope(data["text"])
            all_anchors.append({**value, "award_level": level, "page_id": source_page["id"], "page_number": source_page["page_index"], "file_id": source_page["file_id"], "identity_page_id": identity["page_id"], "identity_box": identity["box"], "association": association})
    kinds = {(value["kind"], value["value"]) for value in all_anchors}
    # Do not let a certificate and an unrelated roster dictate two different
    # awards for a single small item. Mixed outcomes require human confirmation.
    if len(kinds) > 1:
        return {"status": "AMBIGUOUS", "facts": {}, "anchors": [], "warnings": ["找到多个不同的本人奖次或考评，请对照原件确认。"], "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities)}
    if not all_anchors:
        return {"status": "NOT_FOUND", "facts": {}, "anchors": [], "warnings": ["未能可靠对应本人奖次或考评，请在原件中定位并核对。"], "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities)}
    kind, value = next(iter(kinds))
    # Prefer an anchor actually sharing the selected identity row.
    all_anchors.sort(key=lambda anchor: (anchor["association"] != "SAME_ROW", anchor["page_number"], anchor["box"][1]))
    facts = {"award_grade" if kind == "AWARD" else "assessment_result": value}
    levels = {anchor["award_level"] for anchor in all_anchors if anchor.get("award_level")}
    if len(levels) == 1:
        facts["award_level"] = next(iter(levels))
    return {"status": "MATCHED", "facts": facts, "anchors": all_anchors, "warnings": [], "is_roster": roster, "identity_count": len(validated_identities), "identity_matched": bool(validated_identities)}
