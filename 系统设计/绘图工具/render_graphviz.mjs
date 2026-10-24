// Independent original diagram rendering from the shared model, not from VP image exports.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { instance } = await import(new URL('../.work/viz/package/dist/viz.js', import.meta.url));
const viz = await instance();
const read = async name => JSON.parse(await fs.readFile(path.join(root, '源文件', name), 'utf8'));
const uml = await read('uml.json'), dfd = await read('dfd.json'), slices = await read('dfd_views.json'), schema = await read('schema.json');
await fs.mkdir(path.join(root, '图稿'), { recursive: true });
await fs.mkdir(path.join(root, '源文件', 'dot'), { recursive: true });
const layouts = {}, index = [], presentation = {};
const esc = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const q = text => JSON.stringify(String(text));
const font = 'Microsoft YaHei';
const palette = { rbac: '#2b5d8b', evidence: '#237260', workflow: '#93602b', catalog: '#6f4c91' };

function wrap(text, length = 18) {
  const lines = []; let current = '', units = 0;
  for (const ch of String(text)) {
    if (ch === '\n' || units + (ch.charCodeAt(0) > 255 ? 2 : 1) > length) { lines.push(current); current = ''; units = 0; }
    if (ch !== '\n') { current += ch; units += ch.charCodeAt(0) > 255 ? 2 : 1; }
  }
  if (current) lines.push(current);
  return lines.join('\n');
}
function graphStart(name, subtitle, direction = 'TB') {
  return ['digraph Design {',
    `graph [rankdir=${direction}, bgcolor="white", pad="0.40", nodesep="0.55", ranksep="1.1", splines=polyline, overlap=false, forcelabels=true, outputorder=edgesfirst, fontname=${q(font)}, fontsize=22];`,
    `node [fontname=${q(font)}, fontsize=12, color="#38536b", penwidth=1.15];`,
    `edge [fontname=${q(font)}, fontsize=10, color="#6e8599", fontcolor="#38536b", arrowsize=0.65, penwidth=1.0, labeldistance=1.8];`];
}
function classLabel(node) {
  const service = node.stereotype === 'service'; const color = service ? '#69508d' : '#285d87';
  const split = node.name.indexOf(' '); const english = split < 0 ? node.name : node.name.slice(0, split); const chinese = split < 0 ? '' : node.name.slice(split + 1);
  const attrs = node.attributes.flatMap(a => { const colon = a.indexOf(':'); return colon >= 0 && a.slice(0, colon).includes(' / ') ? a.slice(0, colon).split(' / ').map(n => n.trim() + a.slice(colon)) : [a]; });
  const lines = [`<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" COLOR="${color}">`,
    `<TR><TD WIDTH="340" BGCOLOR="${color}" ALIGN="CENTER"><FONT COLOR="white" POINT-SIZE="11">«${esc(node.stereotype || 'entity')}»</FONT><BR/><FONT COLOR="white" POINT-SIZE="15"><B>${esc(english)}</B></FONT>${chinese ? `<BR/><FONT COLOR="white" POINT-SIZE="13">${esc(chinese)}</FONT>` : ''}</TD></TR>`];
  for (const a of attrs) lines.push(`<TR><TD ALIGN="LEFT" BGCOLOR="#f6f9fc"><FONT POINT-SIZE="12">− ${esc(a)}</FONT></TD></TR>`);
  if (node.operations?.length) lines.push('<HR/>');
  for (const o of node.operations || []) lines.push(`<TR><TD ALIGN="LEFT" BGCOLOR="#f3f0f8"><FONT POINT-SIZE="12">+ ${esc(o)}</FONT></TD></TR>`);
  lines.push('</TABLE>'); return lines.join('');
}
function makeClass(d) {
  const lines = graphStart(d.name, 'UML 类图 · RBAC / 班级作用域 / 不可变提交证据 · 设计评审版');
  for (const n of d.nodes) lines.push(`${q(n.id)} [shape=plain, margin=0, label=<${classLabel(n)}>];`);
  d.edges.forEach((e, i) => {
    const a = [`id=${q('e' + i)}`, `label=${q(wrap(e.label, 24))}`];
    if (e.type === 'dependency') a.push('style=dashed', 'arrowhead=vee', 'color="#8c79a2"');
    else if (e.type === 'generalization') a.push('arrowhead=empty');
    else {
      a.push(`taillabel=${q(e.fromMultiplicity || '')}`, `headlabel=${q(e.toMultiplicity || '')}`, 'dir=both', 'arrowhead=none');
      a.push(e.type === 'composition' ? 'arrowtail=diamond' : 'arrowtail=none');
    }
    lines.push(`${q(e.from)} -> ${q(e.to)} [${a.join(',')}];`);
  });
  return { dot: lines.concat('}').join('\n'), nodes: d.nodes, edges: d.edges };
}
function makeDFD(d) {
  const subtitle = d.id.startsWith('05a') || d.id.startsWith('05b') ? '同一级 DFD 的互补审阅切片；两图合并覆盖完整一级图 73 条流' : '逻辑数据流图 · 箭头表示数据内容，不表示控制顺序 · 设计评审版';
  const lines = graphStart(d.name, subtitle, 'TB');
  for (const n of d.nodes) {
    const label = q(n.id + '\n' + wrap(n.name, 20));
    if (n.type === 'store') {
      const body=wrap(n.name,22).split('\n').map(esc).join('<BR/>');
      lines.push(`${q(n.id)} [shape=plain, margin=0, label=<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="8" COLOR="#8b754b"><TR><TD BORDER="1" SIDES="LRTB" BGCOLOR="#fff3d8">${esc(n.id)}</TD><TD WIDTH="180" BORDER="1" SIDES="TB" BGCOLOR="#fff3d8">${body}</TD></TR></TABLE>>];`);
    }
    else if (n.type === 'external' || n.type === 'boundary') lines.push(`${q(n.id)} [shape=box, style="filled", fillcolor="#eaf1f8", width=2.2, margin="0.16,0.14", label=${label}];`);
    else lines.push(`${q(n.id)} [shape=box, style="rounded,filled", fillcolor="#e4f3ec", color="#286b54", width=2.4, margin="0.18,0.16", label=${label}];`);
  }
  // Keep core process rows intelligible; response flows need not add extra hierarchy ranks.
  const processes = d.nodes.filter(n => n.type === 'process').map(n => n.id);
  d.edges.forEach((e, i) => {
    const from = d.nodes.find(n => n.id === e.from), to = d.nodes.find(n => n.id === e.to);
    const storage = from.type === 'store' || to.type === 'store';
    const flowId=e.flowId || (d.id==='05_dfd_level1'?'L1':d.id==='04_dfd_context'?'C':d.id.startsWith('06')?'P2':'P3')+'-F'+String(i+1).padStart(2,'0');
    const contextNames={ 'E1>P0':'学生申报、确认与审查资料','P0>E1':'建议、审核回执及公示成绩','E2>P0':'审核、资格、公示与导出资料','P0>E2':'待审材料、办理结果及报表','E3>P0':'账号、组织与权限配置','P0>E3':'身份与配置处理结果','P0>E4':'待识别材料及接口类型','E4>P0':'文字、印章及位置结果','P0>E5':'识别文本、规则与活动候选','E5>P0':'条目、分值及匹配建议' };
    let short=d.id==='04_dfd_context'?contextNames[e.from+'>'+e.to]:e.label.split(/[；;]/)[0];
    if(!short)short=e.label;
    if([...short].length>18)short=[...short].slice(0,17).join('')+'…';
    const label=flowId+'\n'+wrap(short,24);
    const htmlLabel='<TABLE BORDER="0" CELLBORDER="0" CELLPADDING="2" BGCOLOR="white">'+label.split('\n').map(t=>'<TR><TD WIDTH="150"><FONT POINT-SIZE="10">'+esc(t)+'</FONT></TD></TR>').join('')+'</TABLE>';
    presentation[d.id]??={};presentation[d.id]['e'+i]={flowId,displayLabel:label,fullLabel:e.label,from:e.from,to:e.to};
    lines.push(`${q(e.from)} -> ${q(e.to)} [id=${q('e' + i)}, label=<${htmlLabel}>, tooltip=${q(e.label)}, color=${q(storage ? '#a08e68' : '#326a80')}, fontcolor=${q(storage ? '#7c6944' : '#294f64')}${(from.type === 'process' && to.type === 'external') || storage && from.type === 'store' ? ',constraint=false' : ''}];`);
  });
  return { dot: lines.concat('}').join('\n'), nodes: d.nodes, edges: d.edges };
}
function tableLabel(t, core, overview) {
  const color = core ? palette[t.group] : '#7c8994';
  const shown = overview ? [] : core ? t.fields : t.fields.filter(f => f.pk);
  const width = overview ? 215 : core ? 385 : 250;
  const lines = [`<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" COLOR="${color}">`,
    `<TR><TD WIDTH="${width}" BGCOLOR="${color}"><FONT COLOR="white" POINT-SIZE="13"><B>${esc(t.id)}</B></FONT><BR/><FONT COLOR="white" POINT-SIZE="12">${esc(t.name)}${!core ? '（引用）' : ''}</FONT></TD></TR>`];
  for (const f of shown) lines.push(`<TR><TD ALIGN="LEFT" BGCOLOR="${f.pk ? '#eaf0f5' : '#f8fafc'}"><FONT POINT-SIZE="11">${f.pk ? '<B>PK</B>' : f.fk ? '<B>FK</B>' : '　'} ${esc(f.name)} : ${esc(f.type)}${f.nullable ? ' ?' : ''}</FONT></TD></TR>`);
  lines.push('</TABLE>'); return lines.join('');
}
function erGraph(d) {
  const overview = d.group === 'all';
  const core = new Set(schema.tables.filter(t => overview || t.group === d.group).map(t => t.id));
  if (d.group === 'catalog') { core.add('class_publicity'); core.add('publicity_events'); }
  if (d.group === 'workflow') { core.delete('class_publicity'); core.delete('publicity_events'); }
  const shown = new Set(core);
  schema.relationships.forEach(r => { if (core.has(r.to)) shown.add(r.from); });
  const nodes = schema.tables.filter(t => shown.has(t.id));
  const edges = schema.relationships.filter(r => shown.has(r.from) && shown.has(r.to) && (overview || core.has(r.to)));
  const lines = graphStart(d.name, overview ? '33 张逻辑表 · 颜色区分领域 · 完整字段见四张分模块 ER 图与数据字典' : 'Crow’s Foot 关系 · PK 主键 / FK 外键 / ? 可空 · 引用表仅显示主键', 'LR');
  for (const n of nodes) lines.push(`${q(n.id)} [shape=plain, margin=0, label=<${tableLabel(n, core.has(n.id), overview)}>];`);
  edges.forEach((e, i) => {
    const child = schema.tables.find(t => t.id === e.to), field = child.fields.find(f => f.name === e.toField);
    const childMark = e.cardinality.includes('0..1') ? 'teeodot' : e.cardinality.endsWith(':1') ? 'teetee' : 'crowodot';
    const parentMark = field?.nullable ? 'teeodot' : 'teetee';
    lines.push(`${q(e.from)} -> ${q(e.to)} [id=${q('e' + i)}, dir=both, arrowtail=${parentMark}, arrowhead=${childMark}, label=${q(overview ? e.label : wrap(e.toField, 28))}, color=${q(palette[child.group])}];`);
  });
  return { dot: lines.concat('}').join('\n'), nodes, edges };
}
function geometry(raw, data) {
  const bb = raw.bb.split(',').map(Number), height = bb[3];
  const nodes = {};
  for (const obj of raw.objects || []) if (obj.pos && obj.width) {
    const [cx, cy] = obj.pos.split(',').map(Number), w = Number(obj.width) * 72, h = Number(obj.height) * 72;
    nodes[obj.name] = { x: Math.round(cx - w / 2 + 35), y: Math.round(height - cy - h / 2 + 100), w: Math.ceil(w), h: Math.ceil(h) };
  }
  const edges = {};
  for (const edge of raw.edges || []) {
    if (!edge.id || !edge.pos) continue;
    const points = []; let start = null, end = null;
    for (const token of edge.pos.split(/[ ;]+/)) {
      const s = token.split(',');
      if (s.length === 3) { const p = [Number(s[1]) + 35, height - Number(s[2]) + 100]; if (s[0] === 's') start = p; else if (s[0] === 'e') end = p; }
      else if (s.length === 2) points.push([Number(s[0]) + 35, height - Number(s[1]) + 100]);
    }
    const route = (start ? [start] : []).concat(points, end ? [end] : []);
    const dedup = route.filter((p, i) => i === 0 || Math.abs(p[0] - route[i - 1][0]) + Math.abs(p[1] - route[i - 1][1]) > 0.5);
    edges[edge.id] = { points: dedup.map(p => p.map(Math.round)), label: edge.lp ? edge.lp.split(',').map(Number).map((n, i) => Math.round(i === 0 ? n + 35 : height - n + 100)) : null };
  }
  return { nodes, edges, width: Math.ceil(bb[2] + 70), height: Math.ceil(height + 160) };
}
const diagrams = [...uml.diagrams, ...dfd.diagrams, ...slices.diagrams];
for (const [id, name, group] of [
  ['08-er-overview', '数据库 ER 总览：33 张逻辑表', 'all'],
  ['09-er-rbac', '数据库 ER：账号、组织与 RBAC', 'rbac'],
  ['10-er-evidence', '数据库 ER：文件、识别与证据', 'evidence'],
  ['11-er-workflow', '数据库 ER：版本、审核与奖项替代', 'workflow'],
  ['12-er-catalog', '数据库 ER：活动资格、计分规则与公示', 'catalog']
]) diagrams.push({ id, name, type: 'erd', group });
for (const d of diagrams) {
  const data = d.type === 'class' ? makeClass(d) : d.type === 'dfd' ? makeDFD(d) : erGraph(d);
  const result = viz.render(data.dot, { format: 'svg', engine: 'dot' });
  if (result.status !== 'success') throw new Error(d.id + ': ' + JSON.stringify(result.errors));
  const opening=result.output.match(/<svg\b[^>]*viewBox="([^"]+)"[^>]*>/);
  const body=result.output.slice(result.output.indexOf(opening[0])+opening[0].length,result.output.lastIndexOf('</svg>'));
  const vb=opening[1].split(/\s+/).map(Number); const sw=Math.max(vb[2],850);
  const sub=d.type==='class'?'UML 类图 · 权限、领域对象及多重性':d.type==='erd'?'逻辑 ER · PK 主键 / FK 外键 / ? 可空；完整字段约束见数据字典':'逻辑 DFD · 流编号对应完整数据流字典；箭头表示数据内容';
  const clean=`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${sw}px" height="${vb[3]+95}px" viewBox="0 0 ${sw} ${vb[3]+95}"><rect width="100%" height="100%" fill="white"/><text x="28" y="32" font-family="Microsoft YaHei" font-size="21" font-weight="bold" fill="#203b50">${esc(d.name)}</text><text x="28" y="61" font-family="Microsoft YaHei" font-size="12" fill="#5e778b">${esc(sub)} · 设计评审版 / 2026-10-24</text><g transform="translate(0,85)">${body}</g></svg>`;
  await fs.writeFile(path.join(root, '图稿', d.id + '.svg'), clean, 'utf8');
  await fs.writeFile(path.join(root, '源文件', 'dot', d.id + '.dot'), data.dot, 'utf8');
  const raw = viz.renderJSON(data.dot, { engine: 'dot' });
  layouts[d.id] = geometry(raw, data);
  index.push({ id: d.id, name: d.name, kind: d.type, nodes: data.nodes.length, edges: data.edges.length, width: layouts[d.id].width, height: layouts[d.id].height, notes: d.notes || [], warnings: result.errors || [] });
  console.log(d.id, data.nodes.length, data.edges.length, layouts[d.id].width, layouts[d.id].height);
}
await fs.writeFile(path.join(root, '源文件', 'graph-layouts.json'), JSON.stringify(layouts, null, 2));
await fs.writeFile(path.join(root, '源文件', 'presentation-labels.json'), JSON.stringify(presentation,null,2));
await fs.writeFile(path.join(root, '源文件', 'diagram-index.json'), JSON.stringify(index, null, 2));
await fs.writeFile(path.join(root, '源文件', 'renderer-provenance.json'), JSON.stringify({ authoring: 'Shared semantic JSON to independent Graphviz SVG, no VP image transformation', tool: '@viz-js/viz', packageVersion: '3.31.0', graphvizVersion: viz.graphvizVersion, generatedAt: '2026-10-24', diagrams: index.length }, null, 2));
let flowDoc='# 数据流编号与完整数据字典\n\n图中采用流编号与简名，完整内容如下；05a、05b沿用完整一级图L1编号，合并覆盖全部73条流。\n\n';
for(const d of diagrams.filter(d=>d.type==='dfd'&&!d.id.startsWith('05a')&&!d.id.startsWith('05b'))){flowDoc+='## '+d.name+'\n\n| 编号 | 来源 → 去向 | 完整数据内容 |\n| --- | --- | --- |\n';for(const p of Object.values(presentation[d.id]))flowDoc+='| '+p.flowId+' | '+p.from+' → '+p.to+' | '+p.fullLabel.replaceAll('|','\\|').replaceAll('\n','；')+' |\n';flowDoc+='\n';}await fs.writeFile(path.join(root,'数据流字典.md'),flowDoc,'utf8');
