"""Document and verify design artifacts only; never opens the application secrets."""
from pathlib import Path
import hashlib
import json
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'源文件'

def text(value): return str(value).replace('|','\\|').replace('\n','；')

def main():
    schema=json.loads((SRC/'schema.json').read_text(encoding='utf-8'))
    index=json.loads((SRC/'diagram-index.json').read_text(encoding='utf-8'))
    by_id={t['id']:t for t in schema['tables']}
    lines=['# 数据库完整逻辑数据字典','', '设计稿；未执行建表或迁移。逻辑类型与约束为设计依据，数据库产品尚未确定。', '', '共33张表。ER图展示主要关系，以下保留全部字段及外键。通用审计／访问控制要求见《数据库设计说明.md》。', '']
    field_count=0; foreign_key_count=0
    for t in schema['tables']:
        lines += [f"## {t['id']} — {t['name']}",'', '| 字段 | 逻辑类型 | 键 | 可空 | 引用 | 含义 |','| --- | --- | --- | --- | --- | --- |']
        assert len({f['name'] for f in t['fields']})==len(t['fields'])
        for f in t['fields']:
            field_count+=1
            if f.get('fk'):
                foreign_key_count+=1; target,column=f['fk'].rsplit('.',1)
                assert target in by_id and any(x['name']==column for x in by_id[target]['fields']),f['fk']
            lines.append('| '+' | '.join(map(text,[f['name'],f['type'],'PK'+('/FK' if f.get('fk') else '') if f['pk'] else 'FK' if f.get('fk') else '', '是' if f['nullable'] else '否',f.get('fk') or '—',f.get('note','')]))+' |')
        lines += ['','约束：','']+['- '+c for c in t.get('constraints',[])]+['']
    (ROOT/'数据字典.md').write_text('\n'.join(lines),encoding='utf-8')
    checks=[]
    ns={'s':'http://www.w3.org/2000/svg'}
    for item in index:
        svg=ROOT/'图稿'/(item['id']+'.svg'); png=svg.with_suffix('.png')
        element=ET.parse(svg).getroot()
        actual_nodes=len(element.findall('.//s:g[@class="node"]',ns)); actual_edges=len(element.findall('.//s:g[@class="edge"]',ns))
        assert actual_nodes==item['nodes'],(item['id'],'nodes',actual_nodes)
        assert actual_edges==item['edges'],(item['id'],'edges',actual_edges)
        with Image.open(png) as im:
            assert im.width>500 and im.height>200
            im.verify()
        checks.append({'id':item['id'],'nodes':actual_nodes,'edges':actual_edges,'svg_parsed':True,'png_verified':True})
    native=ROOT/'VisualParadigm'/'德育分统计系统-设计评审.vpp'
    assert native.exists() and native.stat().st_size>100000
    with native.open('rb') as stream: magic=stream.read(16)
    container={'exists':True,'bytes':native.stat().st_size,'sha256':hashlib.sha256(native.read_bytes()).hexdigest()}
    if magic==b'SQLite format 3\x00':
        connection=sqlite3.connect(native.as_uri()+'?mode=ro',uri=True)
        container['readonly_integrity_check']=connection.execute('PRAGMA integrity_check').fetchone()[0]
        assert container['readonly_integrity_check']=='ok'
        names=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        container['internal_tables']=names
        connection.close()
    native_audit=(SRC/'native-schema-audit.txt').read_text(encoding='utf-8').splitlines()
    assert len(native_audit)==33 and all('duplicateNames=false' in row and 'nameSetMatches=true' in row for row in native_audit)
    source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [SRC/'uml.json',SRC/'dfd.json',SRC/'dfd_views.json',SRC/'schema.json']}
    report={'status':'passed','design_only':True,'diagram_count':len(index),'table_count':len(schema['tables']),'field_count':field_count,'all_foreign_key_fields':foreign_key_count,'main_er_relationships':len(schema['relationships']),'diagram_checks':checks,'native_project':container,'native_table_audit_count':len(native_audit),'source_sha256':source_hashes,'cloud_api_calls':0,'secrets_read':False}
    (SRC/'设计制品校验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    readme=['# 德育分统计系统设计稿','', '日期：2026-10-24；状态：等待设计确认，业务实现未开始。', '', '- [打开设计图总览](设计图总览.html)：14张图，支持缩放。','- [总设计说明与待审阅细节](设计评审说明.md)。','- [可编辑Visual Paradigm工程](VisualParadigm/德育分统计系统-设计评审.vpp)。','- [完整数据库字段字典](数据字典.md)。','- [RBAC与领域设计](RBAC与领域设计说明.md)。','- [数据库设计与一致性约束](数据库设计说明.md)。','- [数据流层次分析](数据流分析说明.md)及[完整流编号字典](数据流字典.md)。','', '## 图稿索引','', '| 图号 | 名称 | SVG | PNG |','| --- | --- | --- | --- |']
    for item in index: readme.append(f"| {item['id']} | {item['name']} | [矢量图](图稿/{item['id']}.svg) | [图片](图稿/{item['id']}.png) |")
    readme+=['','## 交付与验证','',f'- 33张表、{field_count}个字段、{foreign_key_count}个字段级外键；ER总览展示42条主要关系。','- 14张SVG均校验节点／关系数量，14张PNG均可解析；33张原生表字段数量与源模型一致、无重复字段名。','- 原生VP工程保存成功；其容器以只读方式检查，未创建业务数据库。','- Visual Paradigm试用版的原生水印导出保存在VisualParadigm/原生导出；清晰图从相同语义模型独立绘制，不是去水印处理。','- 源文件保留JSON、DOT、统一模型XML、布局和制图工具；制图工具不是业务应用。','- 本次未读取密钥或调用DeepSeek／腾讯云接口。','', '## 阅读顺序','', '建议先看01（RBAC）、04（系统边界）、08（ER全貌），再看02与07（版本和替代恢复）以及对应ER分图。一级数据流完整图较宽，可以使用浏览页缩放，05a与05b为互补切片。','', '设计评审说明第7节列出四项设计细化，确认设计后仍等待明确实现指令。']
    (ROOT/'README.md').write_text('\n'.join(readme),encoding='utf-8')
    plugin=ROOT/'.work'/'plugin-classes'/'deyu'/'design'/'DesignPlugin.class'
    if plugin.exists():
        with zipfile.ZipFile(ROOT/'绘图工具'/'VisualParadigm制图插件.zip','w',zipfile.ZIP_DEFLATED) as z:
            z.write(plugin,'classes/deyu/design/DesignPlugin.class');z.write(ROOT/'绘图工具'/'plugin.xml','plugin.xml')
    print(json.dumps({'status':'passed','diagrams':len(index),'tables':len(schema['tables']),'fields':field_count,'foreign_keys':foreign_key_count,'native_integrity':container.get('readonly_integrity_check','not-sqlite')},ensure_ascii=False))

if __name__=='__main__': main()
