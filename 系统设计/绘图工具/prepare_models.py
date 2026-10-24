"""Prepare diagram inputs for Visual Paradigm. This is artifact tooling, not app code."""
from pathlib import Path
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '源文件'

def add(parent, tag, text=None, **attrs):
    node = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        node.text = str(text)
    return node

def main():
    design = ET.Element('design', {'name': '德育分统计系统—设计评审版', 'output': str(ROOT)})
    layout_path = SRC / 'graph-layouts.json'
    layouts = json.loads(layout_path.read_text(encoding='utf-8')) if layout_path.exists() else {}
    presentation_path = SRC / 'presentation-labels.json'
    presentation = json.loads(presentation_path.read_text(encoding='utf-8')) if presentation_path.exists() else {}
    manifest = []
    for filename in ['uml.json', 'dfd.json', 'dfd_views.json']:
        data = json.loads((SRC / filename).read_text(encoding='utf-8'))
        for item in data['diagrams']:
            d = add(design, 'diagram', id=item['id'], name=item['name'], kind=item['type'])
            layout = layouts.get(item['id'], {})
            for note in item.get('notes', []):
                add(d, 'note', note)
            for item_node in item['nodes']:
                n = add(d, 'node', id=item_node['id'], name=item_node['name'], kind=item_node.get('type', 'class'), stereotype=item_node.get('stereotype', ''))
                for key, value in layout.get('nodes', {}).get(item_node['id'], {}).items():
                    n.set(key, str(value))
                for attr in item_node.get('attributes', []):
                    add(n, 'attribute', attr)
                for operation in item_node.get('operations', []):
                    add(n, 'operation', operation)
            for edge_index, edge in enumerate(item['edges']):
                e = add(d, 'edge', **{k: edge.get(k, '') for k in ['from', 'to', 'type', 'fromMultiplicity', 'toMultiplicity', 'label', 'flowId']})
                display = presentation.get(item['id'], {}).get('e' + str(edge_index), {}).get('displayLabel')
                if display:
                    e.set('displayLabel', display)
                for x, y in layout.get('edges', {}).get('e' + str(edge_index), {}).get('points', []):
                    add(e, 'point', x=x, y=y)
            manifest.append({'id': item['id'], 'name': item['name'], 'kind': item['type'], 'nodes': len(item['nodes']), 'edges': len(item['edges'])})

    schema = json.loads((SRC / 'schema.json').read_text(encoding='utf-8'))
    db = add(design, 'schema')
    for table in schema['tables']:
        t = add(db, 'table', id=table['id'], name=table['name'], group=table['group'])
        for field in table['fields']:
            add(t, 'field', name=field['name'], type=field['type'], pk=str(field.get('pk', False)).lower(), fk=field.get('fk') or '', nullable=str(field.get('nullable', True)).lower(), note=field.get('note', ''))
        for c in table.get('constraints', []):
            add(t, 'constraint', c)
    for rel in schema['relationships']:
        add(db, 'relation', **{k: rel.get(k, '') for k in ['from', 'to', 'fromField', 'toField', 'cardinality', 'label']})
    views = [('08-er-overview', '数据库ER总览—33张逻辑表', 'all'), ('09-er-rbac', '数据库ER—账号、组织与RBAC', 'rbac'), ('10-er-evidence', '数据库ER—文件、识别与证据', 'evidence'), ('11-er-workflow', '数据库ER—版本、审核与奖项替代', 'workflow'), ('12-er-catalog', '数据库ER—活动资格、计分规则与公示', 'catalog')]
    for identifier, name, group in views:
        v = add(db, 'view', id=identifier, name=name, group=group)
        for node_id, position in layouts.get(identifier, {}).get('nodes', {}).items():
            add(v, 'position', id=node_id, **position)
        for edge_id, position in layouts.get(identifier, {}).get('edges', {}).items():
            e = add(v, 'edgePosition', id=edge_id)
            for x, y in position.get('points', []):
                add(e, 'point', x=x, y=y)
        core = [t['id'] for t in schema['tables'] if group == 'all' or t['group'] == group]
        manifest.append({'id': identifier, 'name': name, 'kind': 'erd', 'coreTables': core})
    ET.indent(design, space='  ')
    ET.ElementTree(design).write(SRC / 'design-model.xml', encoding='utf-8', xml_declaration=True)
    if not layouts:
        (SRC / 'diagram-index.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'diagrams': len(manifest), 'tables': len(schema['tables']), 'main_relationships': len(schema['relationships'])}))

if __name__ == '__main__':
    main()
