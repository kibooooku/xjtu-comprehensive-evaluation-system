'use strict';

/* Category submissions keep the existing immutable application/review kernel.
 * This file owns the upload-first, multi-item workspace and persistent job UI. */
Object.assign(S,{submissions:[],expandedSubmission:null,selectedItem:null,checkedItems:new Set(),jobs:[],jobTimer:null,jobSignature:'',workspaceDirty:false,historyYear:'',reviewYear:'',uploadGroups:[]});
const activeYear=()=>S.boot?.active_year || S.boot?.current_year || S.boot?.years?.[0];
const itemState=i=>i.state || i.round?.state || 'DRAFT';
const itemEditable=i=>['DRAFT','REJECTED','WITHDRAWN'].includes(itemState(i));
const groupItems=g=>g?.items||[];
const jobName=k=>k==='OCR'?'文字与证据识别':'AI 条目分析';
const taskNames={QUEUED:'排队中',RUNNING:'处理中',SUCCEEDED:'已完成',FAILED:'失败，可重试',STALE:'已保留建议'};
const analysisLabels={UNANALYZED:'未分析',NOT_ANALYZED:'未分析',PENDING:'未分析',RUNNING:'AI 分析中',QUEUED:'排队中',SUCCEEDED:'已分析',SUCCESS:'已分析',COMPLETED:'已分析',FAILED:'分析失败',STALE:'建议待核对'};
function itemTask(i){
  const jobs=S.jobs.filter(j=>j.application_id===i.id);if(i.latest_job&&!jobs.some(j=>j.id===i.latest_job.id))jobs.push(i.latest_job);
  const runningOcr=jobs.find(j=>j.kind==='OCR'&&j.state==='RUNNING');
  const activeAi=jobs.find(j=>j.kind==='AI'&&j.state==='RUNNING')||jobs.find(j=>j.kind==='AI'&&j.state==='QUEUED');
  if(runningOcr){const progress=Number(runningOcr.total)>0?` ${Number(runningOcr.progress||0)}/${Number(runningOcr.total)}`:'';return {label:`OCR 识别中${progress}${activeAi?.state==='QUEUED'?' · AI 等待 OCR':activeAi?' · AI 分析中':''}`,color:'info',busy:true};}
  if(activeAi)return {label:activeAi.state==='RUNNING'?'AI 分析中':waitingForOcr(activeAi)?'AI 等待 OCR':'AI 排队中',color:'info',busy:true};
  const queuedOcr=jobs.find(j=>j.kind==='OCR'&&j.state==='QUEUED');if(queuedOcr)return {label:'OCR 排队中',color:'info',busy:true};
  const latest=jobs.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')))[0]||i.latest_job;
  if(latest?.state==='FAILED')return {label:(latest.kind==='OCR'?'OCR':'分析')+'失败',color:'danger'};
  if(latest?.state==='STALE')return {label:'建议待核对',color:'purple'};
  if(['SUCCEEDED','SUCCESS','COMPLETED'].includes(i.analysis_status))return {label:'已分析',color:'success'};
  if(['SUCCEEDED','SUCCESS','COMPLETED'].includes(i.ocr_status))return {label:'OCR 完成 · 未分析',color:''};
  return {label:analysisLabels[i.analysis_status]||'未分析',color:''};
}
function waitingForOcr(job){return job.kind==='AI'&&job.state==='QUEUED'&&Boolean(job.waiting_for_ocr||job.wait_for_ocr_job_id||job.result_json?.wait_for_ocr_job_id||S.jobs.some(j=>j.application_id===job.application_id&&j.kind==='OCR'&&['QUEUED','RUNNING'].includes(j.state)));}
function taskTag(i){const p=itemTask(i);return `<span class="tag ${p.color}" data-item-progress="${esc(i.id)}">${p.busy?'<span class="tiny-spinner"></span>':''}${esc(p.label)}</span>`;}

function setupWorkspaceShell(){
  if(isStudent()){
    const select=$('#year-selector');if(select){const span=document.createElement('span');span.className='current-year-label';span.id='current-year-label';span.title='当前填报学年由管理员指定';span.textContent=activeYear()?.name||'当前学年';select.replaceWith(span);}
  }
  if(isAdmin()){
    const button=document.createElement('button');button.className='nav-item';button.dataset.action='v2-statistics-year';button.innerHTML=icon('calendar')+'<span>当前统计学年</span>';$('.nav-list')?.append(button);
  }
  const existing=$('#background-jobs');if(existing)existing.remove();
  if(isStudent()){
    const bar=document.createElement('section');bar.id='background-jobs';bar.className='jobs-dock hidden';bar.setAttribute('aria-live','polite');bar.innerHTML='<div id="jobs-summary"></div><div id="jobs-list" class="jobs-list hidden"></div>';$('.shell')?.append(bar);
    bar.onclick=e=>{const button=e.target.closest('[data-job-action]');if(!button)return;if(button.dataset.jobAction==='expand')$('#jobs-list').classList.toggle('hidden');if(button.dataset.jobAction==='item')openJobItem(button.dataset.id);if(button.dataset.jobAction==='retry')enqueueItem(button.dataset.id,button.dataset.kind,true).catch(err=>toast(err.message,true));};
  }
  if(S.jobTimer)clearInterval(S.jobTimer);S.jobTimer=null;
  if(isStudent()){pollJobs(true);S.jobTimer=setInterval(()=>pollJobs(),2200);}
}
async function pollJobs(initial=false){
  if(!S.user||!isStudent()||S.pollingJobs)return;S.pollingJobs=true;
  try{
    const response=await api('/analysis-jobs?active_only=false');if(!S.user)return;
    const jobs=arr(response.jobs||response);const signature=JSON.stringify(jobs.map(j=>[j.id,j.state,j.progress,j.stage,j.finished_at]));const changed=signature!==S.jobSignature;
    S.jobs=jobs.sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));S.jobSignature=signature;drawJobs();
    if(changed&&!initial&&['applications','review','classApplications','submission','history'].includes(S.view))await refreshWorkspaceData();
  }catch(e){if(e.status!==404&&e.status!==401)console.debug('任务状态暂时不可用');}finally{S.pollingJobs=false;}
}
function drawJobs(){
  const bar=$('#background-jobs');if(!bar)return;
  const active=S.jobs.filter(j=>['QUEUED','RUNNING'].includes(j.state)).sort((a,b)=>{const rank=j=>j.state==='RUNNING'?(j.kind==='OCR'?0:1):2;return rank(a)-rank(b)||String(b.created_at||'').localeCompare(String(a.created_at||''));});
  const activeIds=new Set(active.map(j=>j.id)),recent=[...active,...S.jobs.filter(j=>!activeIds.has(j.id)).slice(0,6)];
  if(!recent.length){bar.classList.add('hidden');document.body.classList.remove('has-jobs');return;}
  bar.classList.remove('hidden');document.body.classList.add('has-jobs');
  $('#jobs-summary').innerHTML=`<button class="jobs-summary" data-job-action="expand"><span class="row gap6">${active.length?'<span class="tiny-spinner"></span>':icon('check')}<strong>${active.length?`${active.length} 个后台任务正在进行`:'后台任务已处理'}</strong><span class="muted small">切换页面或刷新后继续运行</span></span><span class="row gap6 small">查看进度 ${icon('chevron')}</span></button>`;
  $('#jobs-list').innerHTML=recent.map(j=>{const title=findItem(j.application_id)?.title||j.title||j.application_title||'加分小项';const ratio=Math.min(100,Number(j.progress||0)/Math.max(1,Number(j.total||1))*100);return `<div class="job-row" data-job-id="${esc(j.id)}" data-job-kind="${esc(j.kind)}"><div class="flex1"><button class="text-action" data-job-action="item" data-id="${esc(j.application_id)}">${esc(title)}</button><p class="small muted">${jobName(j.kind)} · ${esc(waitingForOcr(j)?(j.stage?.includes('OCR')?j.stage:'等待OCR完成（原OCR任务继续执行）'):(j.stage||taskNames[j.state]||j.state))}${j.state==='RUNNING'?` · ${Number(j.progress||0)}/${Number(j.total||0)}`:''}</p>${['QUEUED','RUNNING'].includes(j.state)?`<div class="progress-track mt8"><span style="width:${ratio}%"></span></div>`:''}${j.error_message?`<p class="small danger-text mt8">${esc(j.error_message)}</p>`:''}</div><span class="tag ${j.state==='SUCCEEDED'?'success':j.state==='FAILED'?'danger':'info'}">${esc(waitingForOcr(j)?'等待 OCR':taskNames[j.state]||j.state)}</span>${j.state==='FAILED'?`<button class="text-action" data-job-action="retry" data-id="${esc(j.application_id)}" data-kind="${esc(j.kind)}">重试</button>`:''}</div>`;}).join('');
  $$('[data-item-progress]').forEach(el=>{const item=findItem(el.dataset.itemProgress);if(!item)return;const task=itemTask(item);el.className='tag '+task.color;el.innerHTML=(task.busy?'<span class="tiny-spinner"></span>':'')+esc(task.label);});
}
function findItem(id){return S.submissions.flatMap(g=>groupItems(g)).find(i=>i.id===id);}
async function openJobItem(id){
  try{const item=findItem(id);if(item){const group=S.submissions.find(g=>groupItems(g).some(i=>i.id===id));S.expandedSubmission=group.id;S.selectedItem=id;await go('applications');return;}await go('detail',{id,from:'applications'});}catch(e){toast(e.message,true);}
}

async function renderSubmissionWorkspace(seq){
  const mine=['applications','history','submission'].includes(S.view)&&(!S.params.scope||S.params.scope==='mine');
  const historical=S.view==='history';
  if(isStudent()&&!historical)S.yearId=activeYear()?.id||S.yearId;
  const scope=S.view==='review'?'review':S.view==='classApplications'?'class':S.params.scope||'mine';
  const year=historical?(S.historyYear||S.boot.years.find(y=>y.id!==activeYear()?.id)?.id||activeYear()?.id):scope==='review'&&isStudent()?(S.reviewYear||activeYear()?.id):S.yearId;
  const list=arr(await api('/submissions?'+new URLSearchParams({year_id:year||'',class_id:S.classId||'',scope,...(historical?{include_deleted:'true'}:{})})));if(seq!==S.renderSequence)return;
  S.submissions=list;S.workspaceScope=scope;S.workspaceYear=year;S.workspaceHistorical=historical;S.workspaceDirty=false;
  if(S.params.id && S.view==='submission')S.expandedSubmission=S.params.id;
  if(!list.some(g=>g.id===S.expandedSubmission))S.expandedSubmission=list[0]?.id||null;
  const headings=historical?['历史申报记录','旧学年材料、计分与审核记录保留，可查看，学生不在此新增或修改。']:scope==='mine'?['我的申报','先整理上传材料，再核对各小项。每个加分类别保留一份申报。']:scope==='review'?['待审申请','按大类查看材料与小项，逐项比对和审核，已通过项目保持锁定。']:['班级申报','按学生和类别查看完整小项，核查申报、审核与识别进度。'];
  const actions=scope==='mine'&&!historical?`<button class="btn" data-action="v2-bulk-analyze">${icon('spark')}一键分析未分析项</button><button class="btn primary" data-action="new-application">${icon('upload')}上传加分材料</button>`:hasPermission('export.read')&&!historical?`<button class="btn" data-action="export">${icon('download')}导出汇总</button>`:'';
  $('#page').innerHTML=pageTitle(...headings,actions)+(historical?`<div class="toolbar"><label class="row small">历史学年 <select id="history-year-selector">${options(S.boot.years,year)}</select></label><span class="tag">学生只读</span></div>`:scope==='mine'?`<div class="workspace-year-note">${icon('calendar')}<span>当前填报：<strong>${esc(activeYear()?.name||'管理员指定学年')}</strong></span><span class="muted">由管理员指定；旧学年请进入“历史记录”</span></div><div class="category-grid mb16">${S.boot.categories.map((c,i)=>`<button class="category-entry" data-action="new-application" data-category="${esc(c.id)}"><span class="cat-icon c${i%7}">${icon(catIcons[i%7])}</span><div><h3>${esc(c.name)}</h3><p>上传材料 · 上限 ${Number(c.default_cap)} 分</p></div></button>`).join('')}</div><div class="notice mb16">${icon('info')}<span>每个主文件默认一个小项，可为该项补充多份附件。确认上传后开始后台 OCR，再点击分析生成条目建议；同一证明的多次活动仍合并计分。</span></div>`:'')+`<div class="toolbar mt24"><div class="row wrap"><span class="muted small" id="workspace-count">${list.length} 份大类申报 · ${list.reduce((n,g)=>n+groupItems(g).length,0)} 个小项</span></div><input id="workspace-search" class="search-input" placeholder="搜索类别、学生或小项名称" type="search" aria-label="搜索申报"></div><div id="submission-groups"></div>`;
  if(historical)$('#history-year-selector').onchange=e=>{S.historyYear=e.target.value;go('history');};
  if(scope==='review'&&isStudent()){
    const filter=document.createElement('div');filter.className='toolbar';filter.innerHTML=`<label class="row small">审查学年 <select id="review-year-selector">${options(S.boot.years,year)}</select></label><span class="small muted">仅筛选待审记录，不改变本人填报的当前学年</span>`;$('#workspace-search').closest('.toolbar').before(filter);$('#review-year-selector').onchange=e=>{S.reviewYear=e.target.value;go('review');};
  }
  $('#workspace-search').oninput=drawSubmissionGroups;await drawSubmissionGroups();
}
async function renderHistoryWorkspace(seq){return renderSubmissionWorkspace(seq);}
async function drawSubmissionGroups(){
  const search=($('#workspace-search')?.value||'').toLowerCase();const groups=S.submissions.filter(g=>(`${g.category_name} ${g.student_name} ${groupItems(g).map(i=>i.title).join(' ')}`).toLowerCase().includes(search));
  const el=$('#submission-groups');if(!el)return;
  el.innerHTML=groups.length?groups.map(g=>groupCard(g)).join(''):`<section class="card">${empty(S.workspaceHistorical?'该学年暂无申报记录':'尚未上传加分材料',S.workspaceScope==='mine'&&!S.workspaceHistorical?'点击上方加分类别，先整理并确认上传材料。':'当前筛选范围内没有申报。')}</section>`;
  $$('[data-item-check]',el).forEach(c=>c.onchange=()=>{if(c.checked)S.checkedItems.add(c.dataset.itemCheck);else S.checkedItems.delete(c.dataset.itemCheck);updateCheckedCount();});
  $$('[data-group-check]',el).forEach(c=>c.onchange=()=>{const group=S.submissions.find(g=>g.id===c.dataset.groupCheck);groupItems(group).filter(itemEditable).forEach(i=>c.checked?S.checkedItems.add(i.id):S.checkedItems.delete(i.id));drawSubmissionGroups();});
  const expanded=S.submissions.find(g=>g.id===S.expandedSubmission);if(expanded && S.selectedItem && groupItems(expanded).some(i=>i.id===S.selectedItem))await loadWorkspaceItem(S.selectedItem).catch(e=>toast(e.message,true));
}
function groupCard(g){
  const expanded=g.id===S.expandedSubmission;const items=groupItems(g),draft=items.filter(itemEditable).length,pending=items.filter(i=>['PENDING_PEER','PENDING_COUNSELOR'].includes(itemState(i))).length,approved=items.filter(i=>itemState(i)==='APPROVED').length;
  const mine=g.student_user_id===S.user.id;const edit=mine&&!S.workspaceHistorical&&g.year_id===activeYear()?.id;
  const active=items.filter(i=>itemTask(i).busy).length;
  return `<section class="card submission-group ${expanded?'expanded':''}" data-group-id="${esc(g.id)}"><header class="submission-head"><button class="submission-toggle" data-action="v2-toggle-group" data-id="${esc(g.id)}"><span class="cat-icon">${icon(catIcons[S.boot.categories.findIndex(c=>c.id===g.category_id)]||'file')}</span><span><strong>${esc(g.category_name)}</strong><small>${esc(g.student_name)} · ${esc(g.class_name)} · ${esc(g.year_name)}</small></span></button><div class="submission-head-stats"><span>${items.length} <small>小项</small></span><span class="success-text">${approved} <small>已通过</small></span><span class="score-num">${fmt(g.approved_total)} <small>已通过分</small></span>${active?`<span class="tag info"><span class="tiny-spinner"></span>${active} 项识别中</span>`:''}${edit?`<button class="btn sm" data-action="v2-add-files" data-id="${esc(g.id)}" data-category="${esc(g.category_id)}">${icon('plus')}增补材料</button>`:''}<button class="icon-btn" data-action="v2-toggle-group" data-id="${esc(g.id)}" aria-label="展开小项">${icon(expanded?'back':'chevron')}</button></div></header>${expanded?`<div class="submission-overview"><span>${draft} 项可编辑 · ${pending} 项待审 · ${approved} 项通过</span>${edit?`<span class="row"><button class="text-action" data-action="v2-bulk-analyze" data-id="${esc(g.id)}">${icon('spark')}分析未分析项</button><button class="btn sm primary" data-action="v2-submit-selected" data-id="${esc(g.id)}">提交选中小项 <span data-checked-count="${esc(g.id)}">${items.filter(i=>S.checkedItems.has(i.id)&&itemEditable(i)).length}</span></button></span>`:''}</div><div class="table-wrap"><table class="items-table"><thead><tr>${edit?`<th><input type="checkbox" data-group-check="${esc(g.id)}" aria-label="选择本类可编辑小项"></th>`:''}<th>证明材料</th><th>具体加分条目</th><th>申报分值</th><th>识别进度</th><th>审核状态</th><th>操作</th></tr></thead><tbody>${items.map(i=>itemRow(g,i,edit)).join('')}</tbody></table></div><div class="workspace-comparison" id="comparison-${esc(g.id)}">${S.selectedItem&&items.some(i=>i.id===S.selectedItem)?loading():`<div class="comparison-empty">${icon('eye')}<span>点击小项“对照核对”，在左侧查看材料，右侧核对条目与证据。</span></div>`}</div>`:''}</section>`;
}
function itemRow(g,i,edit){
  const fileNames=(i.files||[]).map(f=>f.original_name).filter(Boolean);const state=itemState(i);const editable=edit&&itemEditable(i);const selected=S.selectedItem===i.id;
  return `<tr class="${selected?'selected':''}" data-item-row="${esc(i.id)}">${edit?`<td><input type="checkbox" data-item-check="${esc(i.id)}" ${S.checkedItems.has(i.id)?'checked':''} ${editable?'':'disabled'} aria-label="选择 ${esc(i.title||'此小项')}"></td>`:''}<td class="item-file-cell"><div class="row gap6">${icon('file')}<span>${esc(fileNames[0] || i.primary_file_name || i.file_name || '证明材料')}</span></div><small>${fileNames.length>1?`另 ${fileNames.length-1} 份附件`:`${i.file_count||fileNames.length||1} 份附件`}</small></td><td class="title-cell"><div class="table-title">${esc(i.title||'等待分析，请核对填写')}</div>${i.student_modified_score?'<div class="table-sub">学生手动修改过计分</div>':''}${i.possible_duplicates?.length?`<div class="table-sub duplicate-hint" title="${esc(i.possible_duplicates.map(x=>x.reason).join('；'))}">疑似重复，请人工核对</div>`:''}</td><td><span class="score-num">${fmt(i.requested_score)}</span> <small>分</small></td><td>${taskTag(i)}</td><td>${statusTag(state)}${i.deleted_at?'<span class="tag">已删除</span>':''}</td><td><div class="table-actions"><button class="text-action" data-action="v2-select-item" data-id="${esc(i.id)}" data-group="${esc(g.id)}">${S.workspaceScope==='review'?'比对审核':'对照核对'}</button>${editable?`<button class="text-action danger-text" data-action="v2-delete-item" data-id="${esc(i.id)}" data-group="${esc(g.id)}">删除</button>`:''}</div></td></tr>`;
}
function updateCheckedCount(){$$('[data-checked-count]').forEach(el=>{const g=S.submissions.find(x=>x.id===el.dataset.checkedCount);el.textContent=groupItems(g).filter(i=>itemEditable(i)&&S.checkedItems.has(i.id)).length;});}
async function refreshWorkspaceData(){
  if(!$('#submission-groups'))return;
  const list=arr(await api('/submissions?'+new URLSearchParams({year_id:S.workspaceYear||'',class_id:S.classId||'',scope:S.workspaceScope||'mine',...(S.workspaceHistorical?{include_deleted:'true'}:{})})));
  const oldDirty=S.workspaceDirty;S.submissions=list;
  if(!oldDirty){await drawSubmissionGroups();return;}
  for(const g of list)for(const i of groupItems(g)){
    const row=$(`[data-item-row="${i.id}"]`);if(!row)continue;
    const title=$('.table-title',row);if(title)title.textContent=i.title||'等待分析，请核对填写';const tag=$('[data-item-progress]',row);if(tag){const t=itemTask(i);tag.className='tag '+t.color;tag.textContent=t.label;}
  }
}

async function handleWorkspaceAction(action,button){
  if(action==='new-application'){uploadOrganizerModal(button.dataset.category);return true;}
  if(action==='v2-add-files'){uploadOrganizerModal(button.dataset.category,button.dataset.id);return true;}
  if(action==='v2-toggle-group'){S.expandedSubmission=S.expandedSubmission===button.dataset.id?null:button.dataset.id;S.selectedItem=null;S.workspaceDirty=false;drawSubmissionGroups();return true;}
  if(action==='v2-select-item'){if(S.workspaceDirty&&!window.confirm('当前小项有未保存修改。切换后将放弃这些修改，是否继续？'))return true;S.expandedSubmission=button.dataset.group;S.selectedItem=button.dataset.id;S.workspaceDirty=false;await loadWorkspaceItem(button.dataset.id);$$('[data-item-row]').forEach(r=>r.classList.toggle('selected',r.dataset.itemRow===S.selectedItem));$('.comparison-header')?.scrollIntoView({behavior:'smooth',block:'start'});return true;}
  if(action==='v2-bulk-analyze'){await bulkAnalyze(button,button.dataset.id);return true;}
  if(action==='v2-submit-selected'){await submitSelected(button.dataset.id);return true;}
  if(action==='v2-delete-item'){deleteItemModal(button.dataset.group,button.dataset.id);return true;}
  if(action==='v2-save-item'){await buttonTask(button,async()=>{await saveWorkspaceItem();toast('小项已保存');await refreshWorkspaceData();});return true;}
  if(action==='v2-analyze-item'){await buttonTask(button,async()=>{if(S.workspaceDirty)await saveWorkspaceItem();await enqueueItem(button.dataset.id,'AI',button.dataset.force==='true');});return true;}
  if(action==='v2-ocr-item'){await buttonTask(button,async()=>enqueueItem(button.dataset.id,'OCR',true));return true;}
  if(action==='v2-supplement-item'){supplementItemModal(button.dataset.id);return true;}
  if(action==='v2-review-item'){await reviewWorkspaceItem(button.dataset.id);return true;}
  if(action==='v2-withdraw-item'){await withdrawWorkspaceItem(button.dataset.id);return true;}
  if(action==='v2-crop-page'){cropModal(button.dataset.page);return true;}
  if(action==='v2-evidence-source'){openEvidenceSource(button.dataset.page,button.dataset.crop);return true;}
  if(action==='v2-statistics-year'){statisticsYearModal();return true;}
  return false;
}

function uploadOrganizerModal(categoryId='',submissionId=null){
  if(!isStudent())return;
  S.uploadGroups=[];S.uploadSubmissionId=submissionId;S.uploadCategory=categoryId;S.uploading=false;
  showModal('整理本次上传材料',`<div class="upload-organizer"><div class="organizer-intro"><span class="step-num">01</span><div><h3>先按小项整理文件</h3><p>每个主文件默认一个小项。若一个小项有多份支撑材料，请在对应行点击“补充附件”。此窗口不会进行 OCR。</p></div></div><div class="form-grid mt16"><div class="field"><label for="organizer-category">加分类别</label><select id="organizer-category" ${submissionId?'disabled':''}>${options(S.boot.categories,categoryId,'name','请选择加分类别')}</select></div><div class="field"><label>所属学年</label><div class="current-year-field">${esc(activeYear()?.name||'当前统计学年')} <span class="tag">管理员指定</span></div></div></div><label class="upload-zone mt16" id="organizer-drop">${icon('upload')}<p>选择主文件，每个文件建立一个加分小项</p><small>支持 PDF、JPG、JPEG、PNG · 单文件≤200MB，PDF≤500页 · 可一次多选</small><input type="file" id="organizer-main-files" class="hidden" accept=".pdf,.png,.jpg,.jpeg" multiple></label><div id="organizer-groups" class="mt16"></div><div class="notice mt16">${icon('clock')}<span>点击“确认上传”后才会上传文件并开始后台 OCR。完成后仍留在大类小项表格，可一键分析并逐项核对。</span></div></div>`,`<span class="hint" id="organizer-count">尚未选择文件</span><button class="btn" data-action="close-modal" id="organizer-cancel">取消</button><button class="btn primary" id="organizer-confirm" disabled>${icon('upload')}确认上传</button>`,true);
  $('#organizer-main-files').onchange=e=>{addMainFiles(Array.from(e.target.files));e.target.value='';};
  $('#organizer-category').onchange=e=>S.uploadCategory=e.target.value;
  const drop=$('#organizer-drop');drop.ondragover=e=>{e.preventDefault();drop.classList.add('dragover');};drop.ondragleave=()=>drop.classList.remove('dragover');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('dragover');addMainFiles(Array.from(e.dataTransfer.files));};
  $('#organizer-confirm').onclick=e=>confirmOrganizedUpload(e.currentTarget);
  $('#organizer-groups').onclick=async e=>{
    const btn=e.target.closest('[data-organizer-action]');if(!btn||S.uploading)return;const row=S.uploadGroups.find(g=>g.id===btn.dataset.group);if(!row)return;
    if(btn.dataset.organizerAction==='supplement'){$(`[data-supplement-input="${row.id}"]`).click();return;}
    if(btn.dataset.organizerAction==='remove'){
      if(row.confirmed){toast('该项已确认上传，请在主表格中操作。',true);return;}
      if(row.applicationId && S.uploadSubmissionId)try{await api(`/submissions/${S.uploadSubmissionId}/items/${row.applicationId}`,{method:'DELETE'});}catch(err){toast(err.message,true);return;}
      S.uploadGroups=S.uploadGroups.filter(g=>g.id!==row.id);drawOrganizer();
    }
  };
  $('#organizer-groups').onchange=e=>{if(!e.target.matches('[data-supplement-input]'))return;const g=S.uploadGroups.find(x=>x.id===e.target.dataset.supplementInput);if(g&&!g.confirmed){g.files.push(...validLocalFiles(Array.from(e.target.files)));drawOrganizer();}};
}
function validLocalFiles(files){return files.filter(f=>{if(!/\.(pdf|png|jpe?g)$/i.test(f.name)){toast(`${f.name}：仅支持 PDF / JPG / PNG`,true);return false;}if(f.size>200*1024*1024){toast(`${f.name}：单个文件不能超过200MB`,true);return false;}return true;});}
function addMainFiles(files){if(S.uploading)return;for(const f of validLocalFiles(files))S.uploadGroups.push({id:crypto.randomUUID(),files:[f],uploaded:new Set(),applicationId:null,confirmed:false,error:''});drawOrganizer();}
function drawOrganizer(){
  const el=$('#organizer-groups');if(!el)return;
  el.innerHTML=S.uploadGroups.length?`<div class="organizer-table"><div class="organizer-table-head"><span>加分小项与证明文件</span><span>附件安排</span></div>${S.uploadGroups.map((g,index)=>`<div class="organizer-row ${g.confirmed?'done':''}"><div class="organizer-row-main"><span class="organizer-index">${index+1}</span><div class="flex1"><strong>${esc(g.files[0]?.name)}</strong><p>${g.files.length} 份材料 · ${Math.ceil(g.files.reduce((n,f)=>n+f.size,0)/1024)} KB</p>${g.files.length>1?`<div class="organizer-attachments">${g.files.slice(1).map(f=>`<span>${icon('file')}${esc(f.name)}</span>`).join('')}</div>`:''}${g.error?`<p class="danger-text mt8">${esc(g.error)}</p>`:''}</div></div><div class="row gap6">${g.confirmed?'<span class="tag success">已确认，OCR 已入队</span>':`<button class="btn sm" data-organizer-action="supplement" data-group="${g.id}">${icon('plus')}补充附件</button><button class="icon-btn" data-organizer-action="remove" data-group="${g.id}" aria-label="移除此小项">${icon('trash')}</button><input type="file" class="hidden" data-supplement-input="${g.id}" accept=".pdf,.png,.jpg,.jpeg" multiple>`}</div></div>`).join('')}</div>`:empty('还没有选择材料','选择多个主文件，会在这里按行整理为多个小项。');
  $('#organizer-count').textContent=`${S.uploadGroups.length} 个小项 · ${S.uploadGroups.reduce((n,g)=>n+g.files.length,0)} 份附件`;
  $('#organizer-confirm').disabled=!S.uploadGroups.some(g=>!g.confirmed)||S.uploading;
}
async function confirmOrganizedUpload(button){
  if(S.uploading)return;const category=$('#organizer-category').value;if(!category){modalError('请先选择加分类别。');return;}S.uploadCategory=category;S.uploading=true;$('#organizer-cancel').disabled=true;$('#organizer-category').disabled=true;
  const old=button.innerHTML;button.disabled=true;
  try{
    if(!S.uploadSubmissionId){const g=await api('/submissions',{method:'POST',body:{category_id:category}});S.uploadSubmissionId=g.id || g.submission?.id;}
    const ready=[];
    for(let index=0;index<S.uploadGroups.length;index++){
      const g=S.uploadGroups[index];if(g.confirmed)continue;g.error='';button.innerHTML=`<span class="tiny-spinner"></span>上传第 ${index+1}/${S.uploadGroups.length} 项`;
      try{
        if(!g.applicationId){const created=await api(`/submissions/${S.uploadSubmissionId}/items`,{method:'POST',body:{staged:true}});g.applicationId=created.id || created.application?.id || created.item?.id;}
        for(let fileIndex=0;fileIndex<g.files.length;fileIndex++){
          if(g.uploaded.has(fileIndex))continue;
          const body=new FormData();body.append('file',g.files[fileIndex]);await api(`/applications/${g.applicationId}/files`,{method:'POST',body});g.uploaded.add(fileIndex);
        }
        ready.push(g.applicationId);
      }catch(e){g.error=e.message;}
    }
    let result=null;
    if(ready.length){button.innerHTML='<span class="tiny-spinner"></span>确认小项，安排后台 OCR';result=await api(`/submissions/${S.uploadSubmissionId}/confirm-upload`,{method:'POST',body:{item_ids:ready}});const confirmed=result.confirmed_item_ids||ready;S.uploadGroups.forEach(g=>{if(confirmed.includes(g.applicationId))g.confirmed=true;});}
    const remaining=S.uploadGroups.filter(g=>!g.confirmed);
    if(!remaining.length){const groupId=S.uploadSubmissionId;S.uploading=false;closeModal();S.expandedSubmission=groupId;S.selectedItem=null;S.workspaceDirty=false;await go('applications');toast('材料已确认上传，后台 OCR 正在处理，可继续操作其他页面。');if(result?.ocr_errors?.length)toast('部分OCR未能入队，可在小项中重试或手工填写。',true);await pollJobs(true);}
    else {drawOrganizer();modalError(`已确认 ${S.uploadGroups.filter(g=>g.confirmed).length} 项，${remaining.length} 项未完成。请按行内提示处理；已确认项不会重复上传。`);}
  }catch(e){modalError(e.message);}finally{S.uploading=false;if(button.isConnected){button.innerHTML=old;button.disabled=!S.uploadGroups.some(g=>!g.confirmed);$('#organizer-cancel').disabled=false;}drawOrganizer();}
}

async function loadWorkspaceItem(id){
  const group=S.submissions.find(g=>groupItems(g).some(i=>i.id===id));if(!group)return;
  const target=$(`#comparison-${group.id}`);if(!target)return;
  S.selectedItem=id;const entry=groupItems(group).find(i=>i.id===id);const response=await api(`/applications/${id}`+(entry?.deleted_at?'?include_deleted=true':''));if(S.selectedItem!==id||!target.isConnected)return;
  S.detail=response;S.workspaceItem=response;S.workspaceDirty=false;
  const r=response.revision||{},payload=r.payload_json||{};const edit=response.can_edit&&!S.workspaceHistorical&&response.year_id===activeYear()?.id;
  const activities=arr(await api('/activities'));if(S.selectedItem!==id||!target.isConnected)return;S.activities=activities;
  const competition=S.boot.categories.find(c=>c.id===response.category_id)?.requires_eligibility_review;
  target.innerHTML=`<div class="comparison-header"><h3>${esc(response.title||'小项对照核对')}</h3><span class="row gap6">${statusTag(itemState(response))}${response.student_modified_score?'<span class="tag purple">学生手动修改过计分</span>':''}</span></div><div class="comparison-columns"><section class="comparison-materials"><div class="comparison-column-title"><span>${icon('file')}证明材料与证据</span>${edit?`<button class="btn sm" data-action="v2-supplement-item" data-id="${esc(id)}">${icon('plus')}补充附件</button>`:''}</div><div class="comparison-material-body">${edit?`<div class="material-analysis-tools"><button class="btn sm ${!itemTask(response).busy?'primary':''}" data-action="v2-analyze-item" data-id="${esc(id)}" ${itemTask(response).busy?'disabled':''}>${icon('spark')}分析此小项</button><button class="text-action" data-action="v2-analyze-item" data-id="${esc(id)}" data-force="true">重新分析（仅建议）</button><button class="text-action" data-action="v2-ocr-item" data-id="${esc(id)}">重试 OCR</button></div>`:''}<div class="source-file-list">${workspaceFilesHtml(response.files||[],edit)}</div>${workspaceEvidenceHtml(response.crops||[],edit)}</div></section><section class="comparison-information"><div class="comparison-column-title"><span>${icon('edit')}加分信息与审核</span><span class="small muted">${edit?'可核对修改':'已提交内容'}</span></div><div class="comparison-information-body"><form id="workspace-item-form" class="form-grid"><div class="field span2"><label for="workspace-title">具体活动与条目名称</label>${edit?`<input id="workspace-title" name="title" maxlength="255" required value="${esc(r.title||response.title||'')}" placeholder="应包含具体活动名称，不使用泛称">`:`<div class="read-value">${esc(r.title||response.title)}</div>`}</div><div class="field"><label for="workspace-score">申报分值</label>${edit?`<input id="workspace-score" name="requested_score" type="number" min="0" step="0.01" required value="${esc(r.requested_score??response.requested_score)}">`:`<div class="read-value score-num big">${fmt(r.requested_score??response.requested_score)} 分</div>`}</div><div class="field"><label>分类上限</label><div class="read-value">${fmt(S.boot.categories.find(c=>c.id===response.category_id)?.default_cap)} 分</div></div><div class="field span2"><label for="workspace-activity">规范活动匹配</label>${edit?`<select id="workspace-activity" name="activity_id">${options(activities,r.activity_id,'canonical_name','未匹配 / 需要人工认定')}</select>`:`<div class="read-value">${esc(activities.find(a=>a.id===r.activity_id)?.canonical_name||response.activity?.canonical_name||'未匹配活动')}</div>`}</div><div class="field span2"><label for="workspace-subtype">加分细项</label>${edit?`<input id="workspace-subtype" name="subtype" value="${esc(payload.subtype||'')}" maxlength="200" placeholder="活动次数、获奖情况或任职信息">`:`<div class="read-value">${esc(payload.subtype||'—')}</div>`}</div>${competition?competitionFields(payload,edit):''}${response.category_code==='SERVICE'?`<div class="field span2"><label>志愿服务工时（可选）</label>${edit?`<input name="volunteer_hours" type="number" min="0" step="0.01" value="${esc(payload.volunteer_hours??'')}">`:`<div class="read-value">${esc(payload.volunteer_hours??'—')} 小时</div>`}</div>`:''}<div class="field span2"><label for="workspace-note">补充说明</label>${edit?`<textarea id="workspace-note" name="note" maxlength="4000">${esc(payload.note||'')}</textarea>`:`<div class="read-value">${esc(payload.note||'暂无')}</div>`}</div></form>${payload.ai_explanation?`<div class="notice mt16">${icon('spark')}<span>${esc(payload.ai_explanation)}</span></div>`:''}${edit?`<p class="small muted mt16">可以修改AI内容及计分，无需填写修改理由。后续增补不会使其他已通过小项重新审核。</p>`:''}<div class="comparison-actions">${edit?`<button class="btn primary sm" data-action="v2-save-item">${icon('check')}保存此小项</button>`:''}${response.can_withdraw&&!S.workspaceHistorical?`<button class="btn sm" data-action="v2-withdraw-item" data-id="${esc(id)}">撤回此小项</button>`:''}${response.can_review?`<button class="btn ${itemState(response)==='APPROVED'?'danger':'primary'} sm" data-action="v2-review-item" data-id="${esc(id)}">${icon('review')}${itemState(response)==='APPROVED'?'驳回已通过小项':'审核此小项'}</button>`:''}<button class="btn sm" data-action="detail" data-id="${esc(id)}" data-deleted="${response.viewing_history?'true':'false'}">完整版本与历史</button></div><div id="workspace-analysis-suggestion"></div><div class="divider"></div><h3 class="mb16">处理记录</h3>${historyHtml(response)}</div></section></div>`;
  if(edit){$('#workspace-item-form').onsubmit=e=>e.preventDefault();$('#workspace-item-form').oninput=()=>S.workspaceDirty=true;$('#workspace-item-form').onchange=()=>S.workspaceDirty=true;}
  const last=S.jobs.find(j=>j.application_id===id&&j.kind==='AI'&&['SUCCEEDED','STALE'].includes(j.state));if(last)showWorkspaceSuggestion(last);else if(response.latest_analysis?.result_json?.suggestion)showWorkspaceSuggestion({result_json:{...response.latest_analysis.result_json,analysis_id:response.latest_analysis.id,automatically_applied:r.ai_analysis_id===response.latest_analysis.id}});
}
function workspaceFilesHtml(files,edit){
  return files.map(f=>`<article class="workspace-source-file"><div class="row between"><strong class="small">${esc(f.original_name)}</strong>${f.url?`<a class="text-action" href="${urlSafe(f.url)}" target="_blank" rel="noopener">查看原件</a>`:''}</div><div class="source-pages">${(f.pages||[]).map(p=>`<button class="source-page" data-action="v2-crop-page" data-page="${esc(p.id)}"><img loading="lazy" src="${urlSafe(p.url||p.image_url)}" alt="${esc(f.original_name)} 第${Number(p.page_index)||1}页"><span>第 ${Number(p.page_index)||1} 页${edit?' · 框选 / 调整证据':' · 查看原页'}</span></button>`).join('')}</div></article>`).join('')||'<p class="small muted">尚无证明材料。</p>';
}
function workspaceEvidenceHtml(crops,edit){
  const types=[['PARTICIPATION','本人参与证明'],['VALIDITY','有效性证据']];
  return types.map(([kind,label])=>{const list=crops.filter(c=>c.evidence_type===kind);return `<section class="workspace-evidence"><div class="row between"><h4>${label}</h4><span class="small muted">${kind==='PARTICIPATION'?'原页整宽 · 四分之一页上下文':'仅活动名称 / 时间 / 印章'}</span></div>${list.length?list.map(c=>`<figure class="context-evidence"><a href="${urlSafe(c.url||c.private_url)}" target="_blank" rel="noopener"><img loading="lazy" src="${urlSafe(c.url||c.private_url)}" alt="${label}，${esc(c.file_name||'证明文件')}第${Number(c.page_number)||1}页"></a><figcaption><span>${esc(c.file_name||'证明文件')} · 第 ${Number(c.page_number)||1} 页${c.validity_kind?' · '+esc(({NAME:'活动名称',TIME:'活动时间',SEAL:'印章'})[c.validity_kind]||c.validity_kind):''}</span><button class="text-action" data-action="v2-evidence-source" data-page="${esc(c.page_id)}" data-crop="${esc(c.id)}">定位原页</button>${edit?`<button class="text-action" data-action="v2-crop-page" data-page="${esc(c.page_id)}">调整截图</button>`:''}</figcaption>${(c.other_matches||[]).length?`<div class="evidence-other-matches">其他匹配：${c.other_matches.map(m=>`<button class="text-action" data-action="v2-evidence-source" data-page="${esc(m.page_id)}">${esc(m.file_name||'证明文件')}第${Number(m.page_number)||1}页 · ${esc(({NAME:'姓名',CLASS:'班级',STUDENT_NUMBER:'学号'})[m.field]||m.field||'参与信息')}</button>`).join('、')}</div>`:''}</figure>`).join(''):`<div class="evidence-empty">${kind==='PARTICIPATION'?'尚未定位本人参与信息，可查看原页并手动框选。':'未找到具体活动名称、活动时间或印章时，请人工核对原件并补充证据。'}</div>`}</section>`;}).join('');
}
async function saveWorkspaceItem(){
  const form=$('#workspace-item-form');if(!form)return;if(!form.reportValidity())throw new Error('请完善此小项的名称和分值。');
  const payload={...(S.workspaceItem.revision?.payload_json||{})};
  for(const key of ['subtype','note','edition_key','track_key','group_key','work_key','award_level','volunteer_hours'])if(form.elements[key]){const value=form.elements[key].value;if(key==='volunteer_hours'&&!value)delete payload[key];else payload[key]=value;}
  const updated=await api(`/applications/${S.workspaceItem.id}`,{method:'PUT',body:{title:form.elements.title.value.trim(),requested_score:form.elements.requested_score.value,activity_id:form.elements.activity_id.value||null,payload_json:payload}});S.workspaceItem=updated;S.detail=updated;S.workspaceDirty=false;return updated;
}
async function refreshWorkspaceMaterials(){
  if(!S.selectedItem||!$('.comparison-material-body'))return;
  const detail=await api(`/applications/${S.selectedItem}`);if(detail.id!==S.selectedItem)return;S.detail=detail;S.workspaceItem=detail;
  const container=$('.comparison-material-body'),files=$('.source-file-list',container);if(files)files.innerHTML=workspaceFilesHtml(detail.files||[],detail.can_edit&&!S.workspaceHistorical);
  $$('.workspace-evidence',container).forEach(el=>el.remove());container.insertAdjacentHTML('beforeend',workspaceEvidenceHtml(detail.crops||[],detail.can_edit&&!S.workspaceHistorical));
}

async function enqueueItem(id,kind='AI',force=false){
  const result=await api(`/applications/${id}/jobs`,{method:'POST',body:{kind,force}});
  if(result.job){S.jobs=[result.job,...S.jobs.filter(j=>j.id!==result.job.id)];drawJobs();}
  toast(kind==='OCR'?'OCR 已加入后台队列，可切换页面继续操作。':'AI 分析已加入后台队列，已有人工修改时仅生成建议。');await pollJobs(true);return result;
}
async function bulkAnalyze(button,submissionId=null){
  await buttonTask(button,async()=>{if(S.workspaceDirty)await saveWorkspaceItem();const body={kind:'AI'};if(submissionId)body.submission_id=submissionId;const result=await api('/analysis-jobs/bulk',{method:'POST',body});toast(`已安排 ${result.jobs?.length||0} 项后台分析${result.skipped?.length?`，跳过 ${result.skipped.length} 项已分析或锁定项`:''}。${(result.jobs||[]).some(waitingForOcr)?'原OCR继续执行，AI等待其完成。':''}人工稿仅生成建议，不覆盖已填写信息。`);await pollJobs(true);await refreshWorkspaceData();});
}
function showWorkspaceSuggestion(job){
  const out=$('#workspace-analysis-suggestion');if(!out)return;const result=job.result_json||{};const suggestion=result.suggestion||result.analysis?.result_json?.suggestion;const analysisId=result.analysis_id||result.analysis?.id;
  if(result.auto_applied||result.automatically_applied){out.innerHTML='<div class="notice success mt16">'+icon('check')+'<span>首次分析已填入本小项，请核对具体活动、分值与证据。</span></div>';return;}
  if(!suggestion)return;
  out.innerHTML=`<div class="analysis-result mt16"><h3>后台分析建议</h3><p class="mt8">${esc(suggestion.explanation||result.message||'已有人工修改，建议尚未覆盖当前内容。')}</p><div class="selection-box mt12">${esc(suggestion.title||suggestion.suggested_title||'')}<br>建议分值：${fmt(suggestion.requested_score??suggestion.score??suggestion.suggested_score)} 分</div>${analysisId&&S.workspaceItem?.can_edit?`<button class="btn sm primary mt12" id="workspace-apply-suggestion">应用此建议</button>`:''}</div>`;
  if($('#workspace-apply-suggestion'))$('#workspace-apply-suggestion').onclick=e=>buttonTask(e.currentTarget,async()=>{if(S.workspaceDirty)throw new Error('当前有未保存修改。请先保存，重新分析后再核对此建议，避免覆盖新稿。');await api(`/applications/${S.workspaceItem.id}/apply-analysis`,{method:'POST',body:{analysis_id:analysisId}});toast('建议已应用，请继续核对。');await refreshWorkspaceData();});
}
async function submitSelected(groupId){
  if(S.workspaceDirty)await saveWorkspaceItem();const group=S.submissions.find(g=>g.id===groupId);const selected=groupItems(group).filter(i=>itemEditable(i)&&S.checkedItems.has(i.id));
  if(!selected.length){toast('请先勾选需要正式提交的小项。',true);return;}
  showModal('提交选中的加分小项',`<p class="small muted">本次只提交选中的 ${selected.length} 个小项。已通过及待审小项保持原状态，每个小项独立审核。</p><div class="batch-submit-list mt16">${selected.map(i=>`<div class="row between"><span>${esc(i.title||'待核对小项')}</span><span class="score-num">${fmt(i.requested_score)} 分</span></div>`).join('')}</div><div class="notice mt16">${icon('info')}<span>请核对具体活动名称、分值与两类证据。更高优先级竞赛奖项正式提交后，会立即替代原项目申请。</span></div>`,`<button class="btn" data-action="close-modal">返回核对</button><button class="btn primary" id="batch-submit-confirm">确认提交 ${selected.length} 项</button>`);
  $('#batch-submit-confirm').onclick=e=>buttonTask(e.currentTarget,async()=>{try{const result=await api(`/submissions/${groupId}/submit`,{method:'POST',body:{item_ids:selected.map(i=>i.id)}});const results=result.results||[];const passed=results.filter(r=>r.ok),failed=results.filter(r=>!r.ok);passed.forEach(r=>S.checkedItems.delete(r.id));await refreshWorkspaceData();if(!failed.length){closeModal();toast(`已提交 ${passed.length} 个小项，等待审核。`);}else{modalError(`已提交 ${passed.length} 项，${failed.length} 项未提交：\n`+failed.map(r=>`${selected.find(i=>i.id===r.id)?.title||'小项'}：${typeof r.detail==='string'?r.detail:r.detail?.message||JSON.stringify(r.detail)}`).join('\n'));$('#batch-submit-confirm').disabled=true;}}catch(err){modalError(err.message);}});
}
function deleteItemModal(groupId,id){
  showModal('删除此加分小项','<p class="small muted">删除后不再展示在当前申报表格。原件、内容版本和审核历史仍会保留，已通过或待审小项不能直接删除。</p>',`<button class="btn" data-action="close-modal">取消</button><button class="btn danger" id="confirm-delete-item">删除此项</button>`);
  $('#confirm-delete-item').onclick=e=>buttonTask(e.currentTarget,async()=>{try{await api(`/submissions/${groupId}/items/${id}`,{method:'DELETE'});closeModal();S.checkedItems.delete(id);if(S.selectedItem===id)S.selectedItem=null;S.workspaceDirty=false;toast('小项已删除，历史记录保留。');await refreshWorkspaceData();}catch(err){modalError(err.message);}});
}
function supplementItemModal(id){
  showModal('为此小项补充证明附件','<p class="small muted">选择的附件会归入当前小项，不创建其他小项。单文件≤200MB，PDF≤500页。确认后上传并安排后台OCR。</p><div class="field mt16"><label for="supplement-files">补充附件</label><input id="supplement-files" type="file" accept=".pdf,.png,.jpg,.jpeg" multiple></div>',`<button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="supplement-confirm">确认补传</button>`);
  $('#supplement-confirm').onclick=e=>buttonTask(e.currentTarget,async()=>{const files=validLocalFiles(Array.from($('#supplement-files').files));if(!files.length){modalError('请选择至少一份有效文件。');return;}try{if(S.workspaceDirty)await saveWorkspaceItem();for(const file of files){const body=new FormData();body.append('file',file);await api(`/applications/${id}/files`,{method:'POST',body});}await enqueueItem(id,'OCR',true);closeModal();await refreshWorkspaceData();toast('附件已归入此小项，OCR在后台继续。');}catch(err){modalError(err.message);}});
}
async function withdrawWorkspaceItem(id){
  const detail=S.workspaceItem?.id===id?S.workspaceItem:await api(`/applications/${id}`);
  showModal('撤回此小项','<p class="small muted">仅撤回这个待审小项，其他小项保持原状态。修改后重新提交通常仍从学生初审开始。</p>',`<button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="workspace-withdraw-confirm">确认撤回</button>`);
  $('#workspace-withdraw-confirm').onclick=e=>buttonTask(e.currentTarget,async()=>{try{await api(`/applications/${id}/withdraw`,{method:'POST',body:{expected_version:detail.lock_version}});closeModal();toast('此小项已撤回，可继续修改。');await refreshWorkspaceData();}catch(err){modalError(err.message);}});
}
async function reviewWorkspaceItem(id){S.detail=S.workspaceItem?.id===id?S.workspaceItem:await api(`/applications/${id}`);return reviewModal(id);}
function openEvidenceSource(pageId,cropId){
  if(!S.detail)return;cropModal(pageId);
  const crop=(S.detail.crops||[]).find(c=>c.id===cropId);if(crop&&$('#crop-kind')){$('#crop-kind').value=crop.evidence_type;$('#crop-kind').dispatchEvent(new Event('change'));if(crop.validity_kind&&$('#crop-validity-kind'))$('#crop-validity-kind').value=crop.validity_kind;}
}

async function statisticsYearModal(){
  const data=await api('/admin/statistics-year');
  showModal('设置当前统计学年',`<div class="notice">${icon('calendar')}<span>学生只在管理员指定的当前学年新增、修改和正式提交；旧学年记录保留只读，原待审仍可继续审核。</span></div><form id="statistics-year-form" class="mt16"><div class="field"><label for="statistics-year-select">当前统计学年</label><select id="statistics-year-select">${options(data.years||S.boot.years,data.active_year?.id||activeYear()?.id)}</select></div></form><div class="divider"></div><h3>新建学年（可选）</h3><div class="form-grid mt12"><div class="field"><label for="academic-year-code">学年编码</label><input id="academic-year-code" placeholder="例如：2027-2028" pattern="[0-9]{4}-[0-9]{4}"></div><div class="field"><label for="academic-year-name">显示名称</label><input id="academic-year-name" placeholder="例如：2027-2028学年"></div></div><button class="btn sm mt12" id="create-academic-year">新建学年</button>`,`<button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="save-statistics-year">保存当前学年</button>`);
  $('#save-statistics-year').onclick=e=>buttonTask(e.currentTarget,async()=>{try{await api('/admin/statistics-year',{method:'PUT',body:{year_id:$('#statistics-year-select').value}});closeModal();await loadBootstrap();renderShell();await go('admin');toast('当前统计学年已更新，历史归属保留。');}catch(err){modalError(err.message);}});
  $('#create-academic-year').onclick=e=>buttonTask(e.currentTarget,async()=>{const code=$('#academic-year-code').value.trim();if(!/^\d{4}-\d{4}$/.test(code)){modalError('学年编码格式为 YYYY-YYYY。');return;}try{await api('/admin/academic-years',{method:'POST',body:{code,name:$('#academic-year-name').value.trim()||code+'学年'}});await loadBootstrap();await statisticsYearModal();toast('新学年已创建，可选择为当前统计学年。');}catch(err){modalError(err.message);}});
}

function cropModal(pageId){
  const file=(S.detail?.files||[]).find(f=>(f.pages||[]).some(p=>p.id===pageId));const page=file?.pages.find(p=>p.id===pageId);if(!page)return;
  const canEdit=S.detail.can_edit===true&&S.detail.year_id===activeYear()?.id;
  showModal(canEdit?'原页证据定位与调整':'证明原页',`<p class="muted small mb16">${esc(file.original_name)} · 原件第 ${Number(page.page_index)||1} 页</p>${canEdit?`<div class="crop-controls"><select id="crop-kind" aria-label="证据类别"><option value="PARTICIPATION">本人参与证明</option><option value="VALIDITY">有效性证据</option></select><select id="crop-validity-kind" class="hidden" aria-label="有效性证据子类"><option value="NAME">具体活动名称</option><option value="TIME">活动时间</option><option value="SEAL">印章</option></select><button class="btn sm primary" id="crop-select-mode">${icon('crop')}定位截图</button><button class="btn sm" id="crop-highlight-mode">红框标注</button><select id="crop-highlight-field" aria-label="红框信息类型"><option value="STUDENT_NUMBER">学号</option><option value="NAME">姓名</option><option value="CLASS">班级</option></select><button class="btn sm" id="crop-mask-mode">${icon('shield')}额外遮挡</button><button class="btn sm" id="crop-reset">清除选区</button></div>`:''}<div class="crop-canvas-wrap"><canvas id="crop-canvas" aria-label="证明原页，可拖动定位证据"></canvas></div><p class="crop-helper" id="crop-mode-help">${canEdit?'参与证明保留原页整宽及四分之一页高。拖动确定上下文区域，可用红框标注学号、姓名或班级。':'原件仅向授权用户开放。证据截图显示其对应原件页码，不拼接不同页面。'}</p><div class="crop-preview-label" id="crop-coordinates">正在加载原件页…</div>`,`<button class="btn" data-action="close-modal">关闭</button>${canEdit?'<button class="btn primary" id="crop-save" disabled>保存证据截图</button>':''}`,true);
  const canvas=$('#crop-canvas'),ctx=canvas.getContext('2d'),image=new Image();let box=null,masks=[],highlights=[],start=null,drag=null,mode='box';
  const point=e=>{const bounds=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(canvas.width,(e.clientX-bounds.left)*canvas.width/bounds.width)),y:Math.max(0,Math.min(canvas.height,(e.clientY-bounds.top)*canvas.height/bounds.height))};};
  const rect=(a,b)=>[Math.round(Math.min(a.x,b.x)),Math.round(Math.min(a.y,b.y)),Math.round(Math.abs(a.x-b.x)),Math.round(Math.abs(a.y-b.y))];
  const normalized=b=>{if($('#crop-kind')?.value!=='PARTICIPATION')return b;const height=Math.round(canvas.height/4),middle=b[1]+b[3]/2;return [0,Math.max(0,Math.min(canvas.height-height,Math.round(middle-height/2))),canvas.width,height];};
  function paint(){ctx.clearRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,0,0);if(box){ctx.fillStyle='#102a4340';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,...box,...box);ctx.strokeStyle='#168b82';ctx.lineWidth=Math.max(2,canvas.width/450);ctx.strokeRect(...box);}for(const mask of masks){ctx.fillStyle='#173c5790';ctx.fillRect(...mask);}for(const h of highlights){ctx.strokeStyle='#e13e3e';ctx.lineWidth=Math.max(2,canvas.width/400);ctx.strokeRect(...h.box);}if(drag&&start){const b=mode==='box'?normalized(rect(start,drag)):rect(start,drag);ctx.strokeStyle=mode==='highlight'?'#e13e3e':mode==='mask'?'#375c78':'#168b82';ctx.lineWidth=Math.max(2,canvas.width/450);ctx.strokeRect(...b);}}
  function updateText(){if($('#crop-save'))$('#crop-save').disabled=!box;$('#crop-coordinates').textContent=box?`截图 ${box[2]} × ${box[3]} 像素 · ${highlights.length} 处红框 · ${masks.length} 处额外遮挡`:'请在原页上拖动定位截图区域。';}
  image.onload=()=>{canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;paint();updateText();};image.onerror=()=>$('#crop-coordinates').textContent='原件页加载失败，请关闭后重试。';image.src=page.url||page.image_url;
  if(!canEdit)return;
  canvas.onpointerdown=e=>{start=point(e);drag=start;canvas.setPointerCapture(e.pointerId);e.preventDefault();};canvas.onpointermove=e=>{if(start){drag=point(e);paint();}};canvas.onpointerup=e=>{if(!start)return;const b=rect(start,point(e));if(b[2]>=3&&b[3]>=3){if(mode==='mask')masks.push(b);else if(mode==='highlight')highlights.push({box:b,field:$('#crop-highlight-field').value});else box=normalized(b);}start=null;drag=null;paint();updateText();};canvas.onpointercancel=()=>{start=null;drag=null;paint();};
  function changeMode(next){mode=next;[['#crop-select-mode','box'],['#crop-highlight-mode','highlight'],['#crop-mask-mode','mask']].forEach(([id,key])=>$(id).classList.toggle('primary',next===key));}
  $('#crop-select-mode').onclick=()=>changeMode('box');$('#crop-highlight-mode').onclick=()=>changeMode('highlight');$('#crop-mask-mode').onclick=()=>changeMode('mask');$('#crop-reset').onclick=()=>{box=null;masks=[];highlights=[];paint();updateText();};
  $('#crop-kind').onchange=e=>{$('#crop-validity-kind').classList.toggle('hidden',e.target.value!=='VALIDITY');$('#crop-mode-help').textContent=e.target.value==='PARTICIPATION'?'参与证明保留原页整宽和四分之一页高，拖动调整上下文位置。':'仅截取具体活动名称、活动时间或印章。不把“特此证明”等套话当作有效性证据。';if(box){box=normalized(box);paint();updateText();}};
  $('#crop-save').onclick=e=>buttonTask(e.currentTarget,async()=>{try{const payload={page_id:pageId,evidence_type:$('#crop-kind').value,box,masks,highlights};if(payload.evidence_type==='VALIDITY')payload.validity_kind=$('#crop-validity-kind').value;await api(`/applications/${S.detail.id}/crops`,{method:'POST',body:payload});closeModal();toast('证据截图已保存，原件及页码保持关联。');if($('#submission-groups')){if(S.workspaceDirty)await refreshWorkspaceMaterials();await refreshWorkspaceData();}else await refresh();}catch(error){modalError(error.message);}});
}

init();
