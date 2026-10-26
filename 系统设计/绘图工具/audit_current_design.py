"""Validate current UML/ER against static SQLAlchemy metadata, never the business DB."""
from pathlib import Path
import hashlib
import json
import math
import sqlite3
import sys
import xml.etree.ElementTree as ET
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(PROJECT))
from app import db
from PIL import Image, ImageDraw, ImageFont


def main():
    source = ROOT / '源文件/当前版本'
    er = json.loads((source / 'er-model.json').read_text(encoding='utf-8'))
    uml = json.loads((source / 'uml-model.json').read_text(encoding='utf-8'))
    index = json.loads((source / 'diagram-index.json').read_text(encoding='utf-8'))
    assert db.engine is None
    assert {table['id'] for table in er['tables']} == set(db.tables)
    for table in er['tables']:
        actual = db.tables[table['id']]
        assert {field['name'] for field in table['fields']} == set(actual.c.keys())
        for field in table['fields']:
            column = actual.c[field['name']]
            assert field['nullable'] == column.nullable
            assert field['pk'] == column.primary_key
            assert field['type'] == str(column.type)
            assert field['fk'] == next((fk.target_fullname for fk in column.foreign_keys), None)
    real_fk = {(table.name, column.name, fk.target_fullname) for table in db.tables.values()
               for column in table.c for fk in column.foreign_keys}
    model_fk = {(rel['from_table'], rel['from_field'], rel['to_table'] + '.' + rel['to_field'])
                for rel in er['relationships']}
    assert real_fk == model_fk and len(real_fk) == 84
    unique = sum(bool(item.unique) for table in db.tables.values() for item in table.indexes)
    assert unique == 18
    selected = [identifier for view in er['views'] for identifier in view['relationship_ids']]
    assert len(selected) == len(set(selected)) == 84
    assert set(selected) == {rel['id'] for rel in er['relationships']}
    assert len(index) == 9 and all(item['kind'] in ('class', 'erd') for item in index)
    assert len(uml['diagrams']) == 4
    for diagram in uml['diagrams']:
        ids = {cls['id'] for cls in diagram['classes']}
        assert len(ids) == len(diagram['classes'])
        assert all(rel['source'] in ids and rel['target'] in ids for rel in diagram['relationships'])
    checks = []
    namespace = {'s': 'http://www.w3.org/2000/svg'}
    for item in index:
        svg = ROOT / '图稿' / (item['id'] + '.svg')
        png = svg.with_suffix('.png')
        tree = ET.parse(svg).getroot()
        nodes = len(tree.findall('.//s:g[@class="node"]', namespace))
        edges = len(tree.findall('.//s:g[@class="edge"]', namespace))
        assert (nodes, edges) == (item['nodes'], item['edges']), item['id']
        with Image.open(png) as image:
            assert image.width > 500 and image.height > 200
            image.verify()
        checks.append({'id': item['id'], 'nodes': nodes, 'edges': edges, 'svg_and_png_valid': True})
    audit = (source / 'native-schema-audit.txt').read_text(encoding='utf-8')
    rows = [line for line in audit.splitlines() if ' expected=' in line]
    assert len(rows) == 37 and all('duplicateNames=false' in row and 'nameSetMatches=true' in row for row in rows)
    assert 'TOTAL tables=37 fields=333 foreignKeys=84 uniqueIndexes=18' in audit
    native = ROOT / 'VisualParadigm/德育分统计系统-设计评审.vpp'
    with closing(sqlite3.connect(native.as_uri() + '?mode=ro', uri=True)) as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    native_rows = (source / 'native-generation-report.txt').read_text(encoding='utf-8').splitlines()
    assert len(native_rows) == 9 and not any('dfd' in row.lower() for row in native_rows)
    report = {'status': 'passed', 'implementation_baseline': er['source_commit'], 'diagrams': checks,
              'tables': 37, 'fields': 333, 'physical_foreign_keys': 84, 'unique_indexes': unique,
              'detail_diagrams_cover_all_foreign_keys': True, 'native_project_integrity': 'ok',
              'native_sha256': hashlib.sha256(native.read_bytes()).hexdigest(),
              'business_database_opened': False, 'cloud_calls': 0}
    (source / '设计制品校验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / '源文件/设计制品校验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    sheet = Image.new('RGB', (1860, 1440), '#f1f5f9')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
    small = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
    draw.text((28, 18), '德育分统计系统 · 当前 UML 与 ER 图册', fill='#203b50', font=font)
    draw.text((28, 56), '4 张 UML · 5 张 ER · 37 表 / 333 字段 / 84 外键 · 原图及可编辑工程见图册', fill='#637f92', font=small)
    for position, item in enumerate(sorted(index, key=lambda d: d['id'])):
        x = (position % 3) * 620 + 12
        y = (position // 3) * 440 + 100
        draw.rounded_rectangle((x, y, x + 596, y + 420), radius=10, fill='white', outline='#d0dde7')
        with Image.open(ROOT / '图稿' / (item['id'] + '.png')) as image:
            image = image.convert('RGB')
            image.thumbnail((566, 330))
            sheet.paste(image, (x + (596 - image.width)//2, y + 12 + (330 - image.height)//2))
        draw.text((x + 15, y + 354), item['id'], fill='#286281', font=small)
        draw.text((x + 15, y + 383), item['name'][:29], fill='#203b50', font=small)
    sheet.save(ROOT / '设计图预览.png')
    print('Validated: 9 UML/ER diagrams, 37 tables, 333 fields, 84 FK, 18 unique indexes; native project OK.')


if __name__ == '__main__':
    main()
