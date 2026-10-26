/** Deterministic UML/ER artifacts from static source metadata; no business DB or network. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=path.join(root,'源文件','当前版本');
const {instance}=await import(new URL('../../.tools/design/node_modules/@viz-js/viz/dist/viz.js',import.meta.url));
const viz=await instance();
const er=JSON.parse(await fs.readFile(path.join(source,'er-model.json'),'utf8'));
const uml=JSON.parse(await fs.readFile(path.join(source,'uml-model.json'),'utf8'));
const output=path.join(root,'图稿');
const dotDir=path.join(source,'dot');
await fs.mkdir(output,{recursive:true});await fs.mkdir(dotDir,{recursive:true});
const esc=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const quote=x=>JSON.stringify(String(x));
const colors={rbac:'#285c84',workflow:'#a06826',evidence:'#237467',catalog:'#725096',legacy:'#737e89',service:'#725096',reference:'#697d8e',entity:'#285c84'};
function wrap(value,limit=52){
  const lines=[];let line='',n=0;
  for(const char of String(value)){const size=char.codePointAt(0)>255?2:1;if(char==='\n'||n+size>limit){lines.push(line);line='';n=0;}if(char!=='\n'){line+=char;n+=size;}}
  if(line)lines.push(line);return lines;
}
const br=(value,limit)=>wrap(value,limit).map(esc).join('<BR ALIGN="LEFT"/>');
function start(direction='TB'){
  return ['digraph CurrentDesign {',`graph [rankdir=${direction},bgcolor="white",pad="0.36",nodesep="0.55",ranksep="1.18",splines=polyline,overlap=false,outputorder=edgesfirst,fontname="Arial",fontsize=12];`,
    'node [shape=plain,fontname="Arial",fontsize=12,margin=0];',
    'edge [fontname="Arial",fontsize=10,color="#7b8e9f",fontcolor="#334c62",arrowsize=0.7,penwidth=1.1,labeldistance=2.0];'];
}
function classLabel(node){
  const color=colors[node.kind]||colors.entity,split=node.name.indexOf(' '),name=split<0?node.name:node.name.slice(0,split),label=split<0?'':node.name.slice(split+1);
  const rows=[`<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="7" COLOR="${color}"><TR><TD WIDTH="400" BGCOLOR="${color}"><FONT COLOR="white" POINT-SIZE="11">«${esc(node.kind)}»</FONT><BR/><FONT COLOR="white" POINT-SIZE="16"><B>${esc(name)}</B></FONT>${label?`<BR/><FONT COLOR="white" POINT-SIZE="13">${esc(label)}</FONT>`:''}</TD></TR>`];
  for(const attr of node.attributes||[])rows.push(`<TR><TD ALIGN="LEFT" BGCOLOR="#f5f8fb"><FONT POINT-SIZE="12">− ${br(attr,53)}</FONT></TD></TR>`);
  if(node.operations?.length)rows.push('<HR/>');
  for(const operation of node.operations||[])rows.push(`<TR><TD ALIGN="LEFT" BGCOLOR="#f1edf7"><FONT POINT-SIZE="12">+ ${br(operation,53)}</FONT></TD></TR>`);
  if(node.table)rows.push(`<TR><TD ALIGN="LEFT" BGCOLOR="#edf3f7"><FONT COLOR="#526b7d" POINT-SIZE="10">table: ${esc(node.table)}</FONT></TD></TR>`);
  rows.push('</TABLE>');return rows.join('');
}
function classGraph(diagram){
  const lines=start();
  for(const node of diagram.classes)lines.push(`${quote(node.id)} [label=<${classLabel(node)}>];`);
  diagram.relationships.forEach((edge,index)=>{
    const attrs=[`id="e${index}"`,`label=${quote(wrap(edge.label||'',26).join('\n'))}`];
    if(edge.kind==='dependency')attrs.push('style=dashed','arrowhead=vee','color="#927ba8"');
    else if(edge.kind==='inheritance')attrs.push('arrowhead=empty');
    else attrs.push('dir=both','arrowhead=none',`arrowtail=${edge.kind==='composition'?'diamond':edge.kind==='aggregation'?'odiamond':'none'}`,`taillabel=${quote(edge.source_cardinality||'')}`,`headlabel=${quote(edge.target_cardinality||'')}`);
    lines.push(`${quote(edge.source)} -> ${quote(edge.target)} [${attrs.join(',')}];`);
  });
  return lines.concat('}').join('\n');
}
function tableLabel(table,core,overview){
  const color=table.legacy?colors.legacy:core?colors[table.group]:colors.reference;
  const rows=[`<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" COLOR="${color}"><TR><TD COLSPAN="2" WIDTH="${overview?265:420}" BGCOLOR="${color}"><FONT COLOR="white" POINT-SIZE="14"><B>${esc(table.id)}</B></FONT><BR/><FONT COLOR="white" POINT-SIZE="12">${esc(table.label)}${core?'':'（引用）'}</FONT>${table.legacy?'<BR/><FONT COLOR="white" POINT-SIZE="10">历史兼容 · 当前不启用</FONT>':''}</TD></TR>`];
  const fields=overview?[]:core?table.fields:table.fields.filter(field=>field.pk);
  for(const field of fields){
    const mark=[field.pk?'PK':'',field.fk?'FK':'',field.unique&&!field.pk?'UK':''].filter(Boolean).join('/');
    rows.push(`<TR><TD BGCOLOR="${field.pk?'#e8eff5':'#f7f9fb'}"><FONT COLOR="${field.fk?'#285c84':'#536a7c'}" POINT-SIZE="10">${mark||' '}</FONT></TD><TD PORT="p_${field.name}" ALIGN="LEFT" BGCOLOR="#f7f9fb"><FONT POINT-SIZE="11">${esc(field.name)} : ${esc(field.type)}${field.nullable?' ?':''}</FONT></TD></TR>`);
  }
  if(overview)rows.push(`<TR><TD COLSPAN="2" BGCOLOR="#f5f8fb"><FONT POINT-SIZE="10">${table.fields.length} fields · ${table.fields.filter(field=>field.fk).length} FK</FONT></TD></TR>`);
  if(!overview&&core)for(const unique of table.unique_constraints||[])if(unique.columns.length>1)rows.push(`<TR><TD COLSPAN="2" ALIGN="LEFT" BGCOLOR="#edf3f7"><FONT COLOR="#526b7d" POINT-SIZE="10">${br('UQ ('+unique.columns.join(', ')+')',63)}</FONT></TD></TR>`);
  rows.push('</TABLE>');return rows.join('');
}
const overviewFields={
  student_profiles:['user_id'],class_years:['class_id','academic_year_id','score_rule_version_id'],enrollments:['student_id','class_year_id'],role_permissions:['role_id','permission_id'],role_assignments:['user_id','role_id','class_id'],counselor_classes:['counselor_id','class_id'],
  category_submissions:['enrollment_id','category_id'],category_items:['submission_id','application_id'],applications:['enrollment_id','category_id'],application_revisions:['application_id','activity_id'],submission_rounds:['application_id','revision_id'],review_decisions:['round_id'],application_events:['application_id'],
  file_objects:['uploaded_by'],file_pages:['file_id'],recognition_runs:['page_id'],recognition_regions:['run_id'],revision_files:['revision_id','file_id'],ai_analyses:['application_id','input_revision_id'],analysis_inputs:['analysis_id','recognition_run_id'],evidence_crops:['revision_id','page_id'],background_jobs:['application_id','owner_id'],
  activity_aliases:['activity_id'],activity_eligibility_versions:['activity_id','category_id','academic_year_id'],competition_projects:['activity_id'],award_supersessions:['new_round_id','old_round_id'],class_publicity:['class_year_id'],publicity_events:['publicity_id'],system_settings:['changed_by']};
const overviewRelationships=er.relationships.filter(rel=>overviewFields[rel.from_table]?.includes(rel.from_field));
const erViews=[{id:'08-er-overview',title:'数据库 ER 总览 · 37 张实际表',group:'all',core_tables:er.tables.map(table=>table.id),external_tables:[],relationship_ids:overviewRelationships.map(rel=>rel.id),notes:[`总览展示 ${overviewRelationships.length} 条主干外键；四张分图完整展示全部 84 条物理外键。`,'灰色表为历史兼容：旧竞赛项目与替代记录保留，当前不运行自动替代。','当前学年、OCR用量归档、AI依赖及文件租约存于JSON，属于逻辑引用，不增加物理外键。']},
  ...er.views.map(view=>({...view,id:({'er-rbac':'09-er-rbac','er-evidence':'10-er-evidence','er-workflow':'11-er-workflow','er-catalog':'12-er-catalog'})[view.id],group:view.id.replace('er-',''),title:'数据库 ER · '+view.label,notes:[`本图按外键所在子表分组，展示 ${view.relationship_ids.length} 条实际外键；跨组父表只显示主键。`,'PK 主键；FK 外键；UK 单列唯一；UQ 联合唯一；? 可空。乌鸦脚表示0..*，单线加圆圈表示0..1。',...(view.id==='er-workflow'?['灰色历史替代表及旧项目字段保留兼容，当前竞赛重复由审查员判断。']:[])]}))];
function erGraph(view){
  const overview=view.group==='all',core=new Set(view.core_tables),shown=new Set([...core,...view.external_tables]);
  const lines=start('LR');
  for(const table of er.tables.filter(table=>shown.has(table.id)))lines.push(`${quote(table.id)} [label=<${tableLabel(table,core.has(table.id),overview)}>];`);
  const selected=view.relationship_ids.map(id=>er.relationships.find(rel=>rel.id===id));
  selected.forEach((rel,index)=>{
    const childPort=overview?'':`:p_${rel.from_field}`,parentPort=overview?'':`:p_${rel.to_field}`;
    lines.push(`${quote(rel.to_table)}${parentPort} -> ${quote(rel.from_table)}${childPort} [id="e${index}",dir=both,arrowtail=${rel.nullable?'teeodot':'teetee'},arrowhead=${rel.unique?'teeodot':'crowodot'},color=${quote(rel.legacy?'#a0a8af':colors[er.tables.find(t=>t.id===rel.from_table).group])},${rel.legacy?'style=dashed,':''}label=${quote(overview?'':rel.from_field)},tooltip=${quote(rel.from_table+'.'+rel.from_field+' → '+rel.to_table+'.'+rel.to_field)}];`);
  });
  return lines.concat('}').join('\n');
}
function geometry(raw){
  const box=raw.bb.split(',').map(Number),height=box[3],nodes={},edges={};
  for(const node of raw.objects||[])if(node.pos&&node.width){const [cx,cy]=node.pos.split(',').map(Number),w=Number(node.width)*72,h=Number(node.height)*72;nodes[node.name]={x:Math.round(cx-w/2+35),y:Math.round(height-cy-h/2+100),w:Math.ceil(w),h:Math.ceil(h)};}
  for(const edge of raw.edges||[])if(edge.id&&edge.pos){const points=[];let begin=null,end=null;for(const item of edge.pos.split(/[ ;]+/)){const values=item.split(',');if(values.length===3){const point=[Number(values[1])+35,height-Number(values[2])+100];if(values[0]==='s')begin=point;else if(values[0]==='e')end=point;}else if(values.length===2)points.push([Number(values[0])+35,height-Number(values[1])+100]);}edges[edge.id]={points:[...(begin?[begin]:[]),...points,...(end?[end]:[])].map(point=>point.map(Math.round))};}
  return {nodes,edges,width:Math.ceil(box[2]+70),height:Math.ceil(height+180)};
}
const index=[],layouts={};
for(const diagram of [...uml.diagrams.map(d=>({...d,kind:'class'})),...erViews.map(d=>({...d,kind:'erd'}))]){
  const dot=diagram.kind==='class'?classGraph(diagram):erGraph(diagram);
  const result=viz.render(dot,{format:'svg',engine:'dot'});
  if(result.status!=='success')throw new Error(diagram.id+': '+JSON.stringify(result.errors));
  const raw=viz.render(dot,{format:'json',engine:'dot'});if(raw.status!=='success')throw new Error('Geometry failure: '+diagram.id);
  layouts[diagram.id]=geometry(JSON.parse(raw.output));
  const match=result.output.match(/<svg\b[^>]*viewBox="([^"]+)"[^>]*>/),box=match[1].split(/\s+/).map(Number),width=Math.max(box[2],1100),height=box[3]+170;
  const body=result.output.slice(result.output.indexOf(match[0])+match[0].length,result.output.lastIndexOf('</svg>'));
  const subtitle=diagram.kind==='class'?'当前实现的领域模型 · entity 实体 / service 模块职责 / reference 跨图引用':'当前物理数据库 · PK/FK/UK/UQ 与实际可空性 · 详细字段可放大查看';
  const svg=`<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${width}px" height="${height}px" viewBox="0 0 ${width} ${height}"><style>text{font-family:Arial,"Microsoft YaHei",sans-serif}</style><rect width="100%" height="100%" fill="white"/><text x="28" y="34" font-size="24" font-weight="bold" fill="#203b50">${esc(diagram.title)}</text><text x="28" y="60" font-size="13" fill="#5c7486">${esc(subtitle)} · 2026-10-26</text><g transform="translate(0,86)">${body}</g><text x="28" y="${height-25}" font-size="12" fill="#62798a">${esc((diagram.notes||[])[0]||'当前版本 · 已按代码与静态模型核对')}</text></svg>`;
  await fs.writeFile(path.join(dotDir,diagram.id+'.dot'),dot);
  await fs.writeFile(path.join(output,diagram.id+'.svg'),svg);
  index.push({id:diagram.id,name:diagram.title,kind:diagram.kind,nodes:diagram.kind==='class'?diagram.classes.length:diagram.core_tables.length+diagram.external_tables.length,edges:diagram.kind==='class'?diagram.relationships.length:diagram.relationship_ids.length,width,height,notes:diagram.notes||[]});
  console.log(diagram.id,Math.ceil(width)+'×'+Math.ceil(height));
}
await fs.writeFile(path.join(source,'graph-layouts.json'),JSON.stringify(layouts,null,2));
await fs.writeFile(path.join(source,'diagram-index.json'),JSON.stringify(index,null,2));
await fs.writeFile(path.join(root,'源文件','diagram-index.json'),JSON.stringify(index,null,2));
// Portable editable class sources in addition to the native Visual Paradigm model.
for(const diagram of uml.diagrams){
  const lines=['@startuml','skinparam defaultFontName Microsoft YaHei','skinparam classAttributeIconSize 0','hide empty members','title '+diagram.title];
  for(const cls of diagram.classes)lines.push(`class "${cls.name}" as ${cls.id} <<${cls.kind}>> {`,...(cls.attributes||[]).map(value=>'  - '+value),...(cls.operations||[]).map(value=>'  + '+value),'}');
  const marks={composition:'*--',aggregation:'o--',dependency:'..>',association:'--',inheritance:'--|>'};
  for(const rel of diagram.relationships)lines.push(`${rel.source} "${rel.source_cardinality||''}" ${marks[rel.kind]||'--'} "${rel.target_cardinality||''}" ${rel.target} : ${rel.label||''}`);
  lines.push('@enduml');await fs.writeFile(path.join(source,diagram.id+'.puml'),lines.join('\n'));
}
// Build the native generator's explicit XML; never includes DFD elements.
const tag=(name,attrs={},text=null)=>`<${name}${Object.entries(attrs).map(([key,value])=>` ${key}="${esc(value)}"`).join('')}${text===null?'/>' : '>'+esc(text)+'</'+name+'>'}`;
const xml=['<?xml version="1.0" encoding="UTF-8"?>',`<design name="德育分统计系统—当前实现 UML 与 ER" output="${esc(path.join(root,'..','.tools','design','native-current'))}">`];
for(const diagram of uml.diagrams){xml.push(`<diagram id="${diagram.id}" name="${esc(diagram.title)}" kind="class">`);for(const note of diagram.notes||[])xml.push(tag('note',{},note));for(const cls of diagram.classes){xml.push(`<node id="${cls.id}" name="${esc(cls.name)}" kind="class" stereotype="${cls.kind}" ${Object.entries(layouts[diagram.id].nodes[cls.id]).map(([key,value])=>`${key}="${value}"`).join(' ')}>`);for(const attr of cls.attributes||[])xml.push(tag('attribute',{},attr));for(const operation of cls.operations||[])xml.push(tag('operation',{},operation));xml.push('</node>');}diagram.relationships.forEach((rel,i)=>{xml.push(`<edge from="${rel.source}" to="${rel.target}" type="${rel.kind}" fromMultiplicity="${esc(rel.source_cardinality||'')}" toMultiplicity="${esc(rel.target_cardinality||'')}" label="${esc(rel.label||'')}">`);for(const [x,y] of layouts[diagram.id].edges['e'+i]?.points||[])xml.push(tag('point',{x,y}));xml.push('</edge>');});xml.push('</diagram>');}
xml.push('<schema>');
for(const table of er.tables){
  xml.push(`<table id="${table.id}" name="${esc(table.label)}" group="${table.group}">`);
  for(const field of table.fields)xml.push(tag('field',{name:field.name,type:field.type.replace('NUMERIC','DECIMAL'),pk:field.pk,fk:field.fk||'',nullable:field.nullable,note:field.note||'',unique:field.unique}));
  for(const unique of table.unique_constraints||[])xml.push(tag('constraint',{},'UNIQUE('+unique.columns.join(',')+')'));
  for(const index of table.indexes||[]){xml.push(`<index name="${esc(index.name)}" unique="${index.unique}">`);for(const name of index.columns)xml.push(tag('column',{name}));xml.push('</index>');}
  xml.push('</table>');
}
for(const rel of er.relationships)xml.push(tag('relation',{from:rel.to_table,to:rel.from_table,fromField:rel.to_field,toField:rel.from_field,cardinality:rel.parent_per_child+':'+rel.children_per_parent,label:rel.from_field}));
for(const view of erViews){xml.push(`<view id="${view.id}" name="${esc(view.title)}" group="${view.group}">`);for(const id of view.core_tables)xml.push(tag('core',{id}));for(const id of view.relationship_ids)xml.push(tag('relation',{index:er.relationships.findIndex(rel=>rel.id===id)}));for(const [id,position] of Object.entries(layouts[view.id].nodes))xml.push(tag('position',{id,...position}));for(const [id,edge] of Object.entries(layouts[view.id].edges)){xml.push(`<edgePosition id="${id}">`);for(const [x,y] of edge.points)xml.push(tag('point',{x,y}));xml.push('</edgePosition>');}xml.push('</view>');}
xml.push('</schema>','</design>');await fs.writeFile(path.join(source,'design-model.xml'),xml.join('\n'));
console.log('Generated',index.length,'UML/ER diagrams; actual metadata',er.schema_stats.tables,'tables,',er.schema_stats.foreign_keys,'FK.');
