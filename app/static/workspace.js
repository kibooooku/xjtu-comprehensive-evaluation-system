'use strict';

/* Category submissions keep the existing immutable application/review kernel.
 * This file owns the upload-first, multi-item workspace and persistent job UI. */
Object.assign(S,{submissions:[],expandedSubmission:null,selectedItem:null,checkedItems:new Set(),jobs:[],jobTimer:null,jobSignature:'',completedJobKeys:new Set(),jobsLoaded:false,comparisonRequest:0,workspaceDirty:false,workspaceScoreTouched:false,workspaceFormVersion:null,workspaceFormBasis:null,workspaceFormEdits:0,historyYear:'',reviewYear:'',uploadGroups:[]});
const activeYear=()=>S.boot?.active_year || S.boot?.current_year || S.boot?.years?.[0];
Object.assign(S,{uploadBatches:[],uploadWorkers:0,uploadQueueExpanded:true});
const itemState=i=>i.state || i.round?.state || 'DRAFT';
const itemEditable=i=>['DRAFT','REJECTED','WITHDRAWN'].includes(itemState(i));
const groupItems=g=>g?.items||[];
const jobName=k=>k==='OCR'?'文字与证据识别':'AI 条目分析';
const taskNames={QUEUED:'排队中',RUNNING:'处理中',SUCCEEDED:'已完成',FAILED:'失败，可重试',STALE:'已保留建议',MANUAL_REQUIRED:'需手动处理',SKIPPED:'待手动处理'};
const analysisLabels={UNANALYZED:'未分析',NOT_ANALYZED:'未分析',PENDING:'未分析',RUNNING:'AI 分析中',QUEUED:'排队中',SUCCEEDED:'已分析',SUCCESS:'已分析',COMPLETED:'已分析',FAILED:'分析失败',STALE:'建议待核对'};
const scoreFactLabels={award_level:'赛事级别',award_grade:'获奖等第或名次',competition_kind:'文艺或体育类别',research_type:'科研成果类型',project_level:'项目级别',publication_level:'论文类别',author_rank:'本人作者顺序',assessment_result:'本人考评结果',position_tier:'任职岗位层级',tenure_months:'任期',practice_type:'实践类型',volunteer_hours:'志愿服务工时',recognition_level:'表彰机构级别',record_level:'破纪录级别',identity:'本人身份',own_identity:'本人身份',activity_count:'活动次数',attendance_count:'参加次数',participation_count:'参加次数',activity_type:'活动类型',theory_level:'培训层级',excellent_trainee:'优秀学员情况',award_system:'奖次制或名次制',recognition_kind:'奖励依据类型',placement_level:'见习或挂职层级'};
function scoreMissingLabels(resolution){return [...new Set((resolution?.missing_facts||[]).map(f=>typeof f==='string'?(scoreFactLabels[f]||(/[\u4e00-\u9fff]/.test(f)?f:'计分依据')):f?.label||scoreFactLabels[f?.code]||'计分依据').filter(Boolean))];}
function analysisScoreResolution(result={}){
  const suggestion=result.suggestion||result.analysis?.result_json?.suggestion||{},explicit=result.score_resolution||suggestion.score_resolution;
  if(explicit&&typeof explicit==='object')return explicit;
  if(suggestion.score_requires_confirmation===true||(suggestion.score_requires_confirmation!==false&&(result.score_policy?.status==='NEEDS_REVIEW'||result.needs_review===true)))return {status:'NEEDS_REVIEW',retained_value:suggestion.requested_score,missing_facts:[],message:''};
  if(result.score_policy?.status==='RULE_APPLIED')return {status:'RULE_APPLIED',value:suggestion.requested_score,missing_facts:[],message:''};
  return null;
}
function currentScoreResolution(item={}){
  const revision=item.revision||{},retained=revision.requested_score??item.requested_score??'0.00',explicit=item.score_resolution||revision.score_resolution;
  if(item.student_modified_score||revision.student_modified_score||itemState(item)==='APPROVED')return {status:'MANUAL',value:retained,retained_value:retained,missing_facts:[],message:itemState(item)==='APPROVED'?'保留已审核分值。':'保留学生已填写的分值。',source:itemState(item)==='APPROVED'?'FROZEN':'MANUAL'};
  if(explicit&&typeof explicit==='object')return {...explicit,retained_value:retained};
  const analysis=item.latest_analysis;
  if(analysis?.result_json&&(!revision.ai_analysis_id||revision.ai_analysis_id===analysis.id)){const result=analysisScoreResolution(analysis.result_json);if(result)return {...result,retained_value:retained};}
  const applied=itemJobs(item,'AI').find(job=>{const result=job.result_json||{};return result.automatically_applied||result.auto_applied||(revision.ai_analysis_id&&revision.ai_analysis_id===(result.analysis_id||result.analysis?.id));});
  const legacy=applied&&analysisScoreResolution(applied.result_json||{});return legacy?{...legacy,retained_value:retained}:{status:'LEGACY',value:retained,retained_value:retained,missing_facts:[],message:''};
}
const unresolvedScore=resolution=>['NEEDS_REVIEW','UNANALYZED'].includes(resolution?.status);
function scoreResolutionMessage(resolution){
  const labels=scoreMissingLabels(resolution);
  return labels.length?'未能自动确定分值：'+labels.join('、')+'。':'尚未生成分值，可直接填写。';
}
function itemScoreHtml(item){
  const resolution=currentScoreResolution(item),value=item.revision?.requested_score??item.requested_score??resolution.retained_value??0;
  if(unresolvedScore(resolution))return `<span class="tag warning" title="${esc(scoreResolutionMessage(resolution))}">待核定分值</span><div class="table-sub">当前保留 ${fmt(value)} 分</div>`;
  return `<span class="score-num">${fmt(value)}</span> <small>分</small>`;
}
function workspaceScoreStatusHtml(item,edit){
  const resolution=currentScoreResolution(item);
  if(S.workspaceScoreTouched||!unresolvedScore(resolution))return '';
  return `<p class="small muted mt8">${esc(scoreResolutionMessage(resolution))}</p>`;
}
function refreshWorkspaceScoreStatus(){const item=S.workspaceItem;if(!item)return;patchHtml($('#workspace-score-resolution'),workspaceScoreStatusHtml(item,Boolean(item.can_edit&&!S.workspaceHistorical&&item.year_id===activeYear()?.id)));}
function activityMatchDecision(activity){const value=activity?.decision||activity?.eligibility?.decision;return ['VALID','INVALID'].includes(value)?value:'UNKNOWN';}
function activityMatchStatusHtml(activity){
  if(!activity)return '';
  const decision=activityMatchDecision(activity),label=activity.status_label||{VALID:'已认定有效',INVALID:'已认定无效',UNKNOWN:'待认定'}[decision];
  return `<span class="tag ${decision==='VALID'?'success':decision==='INVALID'?'danger':''}">${esc(label)}</span>${activity.scope_label?`<span class="small muted">${esc(activity.scope_label)}</span>`:''}`;
}
function activityMatchValue(detail){
  const id=detail.revision?.activity_id??detail.activity_id??detail.activity?.id??'';
  return id?{...(detail.activity||{}),id,canonical_name:detail.activity?.canonical_name||detail.activity_name||'已匹配活动',decision:detail.eligibility?.decision||detail.activity?.decision||'UNKNOWN'}:null;
}
function activityMatchField(detail,edit){
  const selected=activityMatchValue(detail);
  return `<div class="field span2 activity-match-field"><div class="activity-match-label"><label for="workspace-activity-search">匹配已认定活动</label><span class="small muted">匹配已认定活动，便无需辅导员审批活动有效性</span></div>${edit?`<div class="activity-match-input"><input id="workspace-activity" type="hidden" name="activity_id" value="${esc(selected?.id||'')}"><input id="workspace-activity-search" type="search" maxlength="250" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="workspace-activity-options" value="${esc(selected?.canonical_name||'')}" placeholder="搜索活动名称或别名"><button type="button" class="text-action" id="workspace-activity-clear" ${selected?'':'hidden'}>清除</button></div><div id="workspace-activity-options" class="activity-match-options" role="listbox" hidden></div>`:`<div class="read-value">${esc(selected?.canonical_name||'未匹配')}</div>`}<div id="workspace-activity-status" class="row gap6 mt8">${activityMatchStatusHtml(selected)}</div></div>`;
}
function syncActivityMatchSelection(detail){
  const selected=activityMatchValue(detail),input=$('#workspace-activity-search'),hidden=$('#workspace-activity'),status=$('#workspace-activity-status'),clear=$('#workspace-activity-clear');
  if(input)input.value=selected?.canonical_name||'';if(hidden)hidden.value=selected?.id||'';if(status)status.innerHTML=activityMatchStatusHtml(selected);if(clear)clear.hidden=!selected;
}
function setupActivityMatchSearch(detail,edit){
  const input=$('#workspace-activity-search'),hidden=$('#workspace-activity'),panel=$('#workspace-activity-options');if(!edit||!input||!hidden||!panel)return;
  const form=input.form,owner=S.user?.id,id=detail.id;let timer,sequence=0,items=[],active=-1;
  const current=()=>input.isConnected&&form===$('#workspace-item-form')&&S.workspaceItem?.id===id&&S.user?.id===owner;
  const close=()=>{sequence++;panel.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;};
  const changed=()=>hidden.dispatchEvent(new Event('change',{bubbles:true}));
  const choose=activity=>{if(!current())return;hidden.value=activity.id;input.value=activity.canonical_name;$('#workspace-activity-status').innerHTML=activityMatchStatusHtml(activity);$('#workspace-activity-clear').hidden=false;sequence++;close();changed();};
  async function search(){
    if(!current())return;const request=++sequence;panel.hidden=false;input.setAttribute('aria-expanded','true');panel.innerHTML='<p class="small muted">搜索中…</p>';
    try{
      const params=new URLSearchParams({q:input.value.trim().slice(0,250),year_id:detail.year_id||'',category_id:detail.category_id||'',limit:'20'});
      const response=await api('/activities/search?'+params);if(!current()||request!==sequence)return;
      items=arr(response.items??response);active=-1;
      panel.innerHTML=items.length?items.map((activity,index)=>`<button type="button" id="activity-choice-${index}" class="activity-match-option" role="option" aria-selected="false" data-activity-choice="${index}"><span>${esc(activity.canonical_name)}</span><span class="row gap6">${activityMatchStatusHtml(activity)}</span></button>`).join('')+(response.total>items.length?'<p class="small muted">输入更多关键词可缩小范围。</p>':''):'<p class="small muted">未找到匹配活动</p>';
      $$('[data-activity-choice]',panel).forEach(button=>{button.onmousedown=event=>event.preventDefault();button.onclick=()=>choose(items[Number(button.dataset.activityChoice)]);});
    }catch(error){if(current()&&request===sequence)panel.innerHTML='<p class="small muted">'+esc(error.message||'活动检索暂不可用')+'</p>';}
  }
  input.oninput=()=>{clearTimeout(timer);sequence++;items=[];close();hidden.value='';$('#workspace-activity-status').replaceChildren();$('#workspace-activity-clear').hidden=true;timer=setTimeout(search,250);};
  input.onfocus=()=>search();input.onblur=()=>setTimeout(()=>{if(current())close();},120);
  input.onkeydown=event=>{
    if(event.key==='Escape'){sequence++;clearTimeout(timer);close();return;}
    if(!['ArrowDown','ArrowUp','Enter'].includes(event.key)||panel.hidden||!items.length)return;
    if(event.key==='Enter'){if(active>=0){event.preventDefault();choose(items[active]);}return;}
    event.preventDefault();active=event.key==='ArrowDown'?(active+1)%items.length:(active-1+items.length)%items.length;
    $$('[data-activity-choice]',panel).forEach((button,index)=>{button.setAttribute('aria-selected',String(index===active));button.classList.toggle('selected',index===active);});input.setAttribute('aria-activedescendant','activity-choice-'+active);
  };
  $('#workspace-activity-clear').onclick=()=>{sequence++;clearTimeout(timer);input.value='';hidden.value='';$('#workspace-activity-status').replaceChildren();$('#workspace-activity-clear').hidden=true;close();changed();};
  if(hidden.value){
    const initial=hidden.value;api('/activity-matches/'+encodeURIComponent(initial)+'?'+new URLSearchParams({year_id:detail.year_id||'',category_id:detail.category_id||''})).then(activity=>{if(current()&&!S.workspaceDirty&&hidden.value===initial){input.value=activity.canonical_name||'已匹配活动';$('#workspace-activity-status').innerHTML=activityMatchStatusHtml(activity);}}).catch(()=>{});
  }
}
function workspaceFormBasisKey(item){
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const revision=item.revision||{};
  return JSON.stringify(canonical({revision_id:revision.id,title:revision.title??item.title??'',requested_score:String(revision.requested_score??item.requested_score??''),activity_id:revision.activity_id||null,payload_json:revision.payload_json||{},file_ids:(item.files||[]).map(file=>file.id).sort()}));
}
function adoptWorkspaceFormBasis(item){S.workspaceFormVersion=item.lock_version??null;S.workspaceFormBasis=workspaceFormBasisKey(item);}
function ocrPolicyNotice(){return `<div class="ocr-policy-notice"><p><strong>上传范围</strong><span>单文件≤200MB，PDF≤500页。</span></p><p><strong>先读取文字层</strong><span>PDF有可用文字层时，直接读取文字和位置供AI分析、匹配本人，不消耗文字OCR额度；印章仍只检测首末页。</span></p><p><strong>云识别范围</strong><span>扫描件≤12页时识别全部页面；长扫描件首页末页自动识别活动名称、时间等有效性内容，中间本人参与信息请手动定位框选。同项附件独立处理。</span></p><p><strong>印章查找</strong><span>所有文件均只自动检查首页和末页，单页文件只检查一次。未检出时，请自行到原件其它页寻找、框选印章。</span></p><p class="ocr-budget-note">仅使用腾讯云免费资源包，不自动购买，不会自动切换到火山；实际剩余额度以腾讯云控制台为准，本系统未实时查询。</p></div>`;}
function filePageCount(file){return Number(file.page_count??file.pages?.length??0);}
function itemPolicyResult(item=S.detail||S.workspaceItem){
  if(!item)return {};
  const candidates=[...S.jobs.filter(j=>j.application_id===item.id).map(policyJobResult),policyJobResult(item.latest_job),item.ocr_policy,item.latest_ocr_result,item.ocr_result,policyJobResult(findItem(item.id)?.latest_job)];
  const current=candidates.find(r=>r&&(Array.isArray(r.file_results)||Array.isArray(r.skipped_files)||Array.isArray(r.per_file_warnings)))||{};
  const files=candidates.find(r=>Array.isArray(r?.file_results));return {...(files||{}),...current};
}
function fileRecognitionPolicy(file){const policy=itemPolicyResult();return {...file,...(policy.file_results||[]).find(r=>r.file_id===file.id)};}
function nativeTextFile(file){const f=fileRecognitionPolicy(file);return f.native_text_used===true||f.text_layer_used===true||f.has_usable_text_layer===true||['NATIVE_TEXT','PDF_TEXT','PDF_TEXT_LAYER','NATIVE_PDF'].includes(f.automatic_ocr_status)||['AVAILABLE','EXTRACTED','USED','PARTIAL'].includes(f.native_text_status);}
function endpointOnlyFile(file){const f=fileRecognitionPolicy(file);if(f.cloud_ocr_scope==='ENDPOINTS')return true;if(['FULL','ALL_PAGES'].includes(f.cloud_ocr_scope))return false;if(typeof f.full_page_cloud_ocr_eligible==='boolean')return !f.full_page_cloud_ocr_eligible;return filePageCount(file)>Math.min(12,Math.max(1,Number(f.max_pages)||12));}
const materialStates={PENDING:{label:'待处理',color:''},PROCESSING:{label:'处理中',color:'info'},CHECK:{label:'待核对',color:'success'},MANUAL:{label:'需手动补充',color:'warning'},ERROR:{label:'处理异常',color:'danger'}};
function itemJobs(item,kind){const list=S.jobs.filter(j=>j.application_id===item?.id&&(!kind||j.kind===kind));if(item?.latest_job&&(!kind||item.latest_job.kind===kind)&&!list.some(j=>j.id===item.latest_job.id))list.push(item.latest_job);return list.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));}
function elapsedText(job){const start=Date.parse(job?.started_at||''),end=job?.finished_at?Date.parse(job.finished_at):Date.now();if(!Number.isFinite(start)||!Number.isFinite(end)||end<start)return '';const seconds=Math.floor((end-start)/1000);return '已耗时 '+(seconds<60?`${seconds}秒`:`${Math.floor(seconds/60)}分${seconds%60}秒`);}
function processingPhase(job){
  if(!job||!['QUEUED','RUNNING'].includes(job.state))return null;
  const waiting=waitingForOcr(job),progress=Number(job.total)>0?` ${Number(job.progress||0)}/${Number(job.total)}`:'';let title,hint;
  if(job.kind==='AI'){
    title=waiting?'AI 等待材料识别完成':job.state==='QUEUED'?'AI 分析排队中':'AI 正在分析条目与计分';
    hint=waiting?'原 OCR 任务继续运行，完成后再开始 AI 分析。':job.state==='QUEUED'?'等待后台分析任务，已有材料和填写内容会保留。':'正在整理活动、计分及证据建议，完成后仍需人工核对。';
  }else if(job.state==='QUEUED'){title='材料识别排队中';hint='已进入后台队列，等待读取材料和文字层。';}
  else if(/文字层|读取|检查材料|准备|排队/.test(job.stage||'')){title='读取材料和 PDF 文字层';hint='大 PDF 的本地读取也可能需要一些时间，先检查已有文字，避免重复云识别。';}
  else {title=`OCR 识别中${progress}`;hint=job.stage||'正在识别材料文字与首页末页印章。';}
  return {title,hint,elapsed:elapsedText(job),job};
}
function ocrStopNotice(job,policy={}){
  const candidates=[policyJobResult(job),job?.result_json,policy].filter(Boolean),sources=new Set(['LOCAL_LIMIT','PROVIDER_RESOURCE','BILLING','RATE_LIMIT','AUTH','NETWORK','CONFIGURATION','PROVIDER_UNAVAILABLE','INPUT_LIMIT','UNKNOWN']);
  const report=candidates.find(value=>value.stop_code||value.stop_source)||candidates.find(value=>value.stopped)||{},stopped=candidates.some(value=>value.stopped);
  // Old results retain an exact error code before the colon. Human warnings
  // mentioning free resources, budgets or quota are not balance evidence.
  const legacy=String(report.stop_reason||'').match(/^\s*([A-Za-z][A-Za-z0-9_.]{0,99})(?=[:：\s]|$)/)?.[1];
  const code=String(report.stop_code||report.error_code||legacy||''),structured=sources.has(report.stop_source)?report.stop_source:null;
  if(!stopped&&!structured&&!(job?.state==='FAILED'&&code))return null;
  let source=structured;
  if(!source){
    if(code==='LocalBudgetExceeded')source='LOCAL_LIMIT';
    else if(/(?:^|\.)ResourcePackageRunOut(?:\.|$)/.test(code))source='PROVIDER_RESOURCE';
    else if(/(?:^|\.)(?:InArrears|ChargeStatusException)(?:\.|$)/.test(code))source='BILLING';
    else if(code==='ImageTooLarge'||/(?:^|\.)TooLargeFileError$/.test(code))source='INPUT_LIMIT';
    else if(/^RequestLimitExceeded(?:\.|$)/.test(code)||code==='LimitExceeded.Quota')source='RATE_LIMIT';
    else if(/^(?:AuthFailure|UnauthorizedOperation)(?:\.|$)/.test(code))source='AUTH';
    else if(['NetworkError','HTTPError'].includes(code))source='NETWORK';
    else if(code==='NotConfigured')source='CONFIGURATION';
    else if(/^(?:ResourceUnavailable|ResourcesSoldOut)(?:\.|$)/.test(code))source='PROVIDER_UNAVAILABLE';
    else source='UNKNOWN';
  }
  const messages={LOCAL_LIMIT:'达到本地OCR调用保护上限，已停止云识别；本系统未查询腾讯云实际剩余额度。已有结果保留，请联系管理员检查本地上限，或手动补充。',PROVIDER_RESOURCE:'腾讯云返回当前OCR接口资源包不可用，已停止云识别；请到腾讯云控制台核对该接口资源包。系统未查询实时剩余额度，已有结果保留，可手动补充。',BILLING:'腾讯云返回账户计费状态异常，已停止本次调用；请核对账户状态，已有结果保留。',RATE_LIMIT:'腾讯云返回调用频率或配额限制，已停止本次云识别；已有结果保留，可稍后重试或手动补充。',AUTH:'腾讯云OCR身份验证或接口权限未通过；请检查配置后重试，已有结果保留。',NETWORK:'OCR网络请求失败或超时；已有结果保留，可稍后重试或手动补充。',CONFIGURATION:'OCR服务配置尚未就绪，未继续调用；请检查配置，已有结果保留。',PROVIDER_UNAVAILABLE:'腾讯云OCR服务暂不可用，已停止本次调用；已有结果保留，可稍后重试或手动补充。',INPUT_LIMIT:'此页或文件超出OCR输入限制；已有结果保留，请调整文件后重试或手动补充。',UNKNOWN:'云识别已停止，具体原因尚未确认；本系统未查询腾讯云实际剩余额度。已有结果保留，可重试或手动补充。'};
  return {source,code,manual:['LOCAL_LIMIT','PROVIDER_RESOURCE'].includes(source),message:messages[source]};
}
function normalizeMaterialState(file=null,item=S.detail||S.workspaceItem||{},forcedJob=null){
  const ocrJobs=forcedJob?[forcedJob]:itemJobs(item,'OCR'),active=ocrJobs.find(j=>j.state==='RUNNING')||ocrJobs.find(j=>j.state==='QUEUED'),latest=active||ocrJobs[0];
  const policy=forcedJob?policyJobResult(forcedJob):itemPolicyResult(item),detail=S.detail?.id===item.id?S.detail:item,crops=policyVisibleCrops(detail.crops||[],Boolean(detail.can_edit),detail.files||[]);
  const summary=detail.evidence_summary||item.evidence_summary||{},summarized=!Array.isArray(detail.crops),evidence={participation:crops.some(c=>c.evidence_type==='PARTICIPATION'&&c.participation_role!=='OUTCOME')||(summarized&&(summary.has_participation===true||summary.participation===true)),name:crops.some(c=>c.evidence_type==='VALIDITY'&&c.validity_kind==='NAME')||(summarized&&(summary.has_activity_name===true||summary.name===true)),time:crops.some(c=>c.evidence_type==='VALIDITY'&&c.validity_kind==='TIME')||(summarized&&(summary.has_activity_time===true||summary.time===true)),seal:crops.some(c=>c.evidence_type==='VALIDITY'&&c.validity_kind==='SEAL')||(summarized&&(summary.has_seal===true||summary.seal===true))};
  const result=(state,extra={})=>({...materialStates[state],state,busy:state==='PROCESSING',actions:[],...extra});
  if(!file){
    const known=detail.files?.some(f=>f.page_count||f.pages?.length)?detail.files:(policy.file_results||[]).map(f=>({...f,id:f.file_id,original_name:f.file_name}));
    const files=known||[];
    if(active){const phase=processingPhase(active);return result('PROCESSING',{method:phase.title,reason:phase.hint,phase});}
    if(files.length){const statuses=files.map(f=>normalizeMaterialState(f,item,forcedJob)),priority=['PROCESSING','ERROR','MANUAL','PENDING','CHECK'],state=priority.find(key=>statuses.some(s=>s.state===key)),chosen=statuses.find(s=>s.state===state);return result(state,{method:files.length===1?chosen.method:`${files.length}份附件 · ${statuses.filter(s=>s.state==='CHECK').length}份待核对${statuses.some(s=>s.state==='MANUAL')?' · 含手动补充':''}`,reason:chosen.reason,files:statuses,phase:chosen.phase});}
    const raw=policyJobResult(latest),error=latest?.error_message||raw.stop_reason||latest?.stage||'',stop=ocrStopNotice(latest,policy);
    if(Object.values(evidence).every(Boolean))return result('CHECK',{method:'同小项证据已补充，待人工核对'});
    if(stop)return result(stop.manual?'MANUAL':'ERROR',{method:'保留已有识别结果',reason:stop.message});
    if(jobNeedsManual(latest))return result('MANUAL',{method:'保留已有识别结果',reason:raw.message||'请按原件补充信息与证据。'});
    if(latest?.state==='FAILED')return result('ERROR',{reason:error||'识别未完成，可查看原件手动补充或稍后重试。'});
    if(['SUCCEEDED','STALE'].includes(latest?.state)||['SUCCEEDED','SUCCESS','COMPLETED'].includes(item.ocr_status))return result('CHECK',{method:latest?.stage||'识别结果保留，请核对'});
    return result('PENDING',{method:'等待材料识别'});
  }
  const f={...file,...(policy.file_results||[]).find(r=>r.file_id===(file.id||file.file_id))},count=filePageCount(f),id=f.id||f.file_id,pages=f.pages||[],native=f.native_pages||[],cloud=f.ocr_page_numbers||[],nativeCount=native.length,covered=Number(f.text_covered_page_count??f.processed_page_count??new Set([...native,...cloud]).size),endpoint=endpointOnlyFile(f);
  const allFiles=detail.files?.length?detail.files:policy.file_results||[],hasPart=evidence.participation,hasSeal=evidence.seal||allFiles.some(source=>source.seal_found||source.seal_status==='FOUND');
  const completedFile=(policy.file_results||[]).find(r=>r.file_id===id),completedResult=!active&&latest?.state==='SUCCEEDED'&&completedFile&&['COMPLETED','PARTIAL','MANUAL_REQUIRED'].includes(completedFile.automatic_ocr_status)&&completedFile.native_text_status!=='PENDING';
  const missing=f.manual_page_numbers||[],phase=processingPhase(active),checking=!completedResult&&(file.automatic_ocr_status==='CHECKING_TEXT_LAYER'||file.text_layer_check_complete===false||f.automatic_ocr_status==='CHECKING_TEXT_LAYER'||f.text_layer_check_complete===false||f.native_text_status==='PENDING');
  const checked=Math.min(count,Math.max(0,Number(file.text_layer_checked_page_count??f.text_layer_checked_page_count??0)));
  let method=nativeCount===count&&count?`直接读取全文 ${nativeCount}/${count} 页`:nativeCount?`文字层 ${nativeCount} 页 + 云识别 ${cloud.length||Math.max(0,covered-nativeCount)} 页 · 已覆盖 ${covered||nativeCount}/${count} 页`:endpoint?`${covered?'仅识别':'计划识别'}首末 ${Math.min(2,count)}/${count} 页${hasPart?' · 本人证据已补充':' · 中间本人信息手动'}`:cloud.length||covered?`云识别 ${cloud.length||covered}/${count} 页`:`文字层优先 · 共 ${count} 页`;
  if(checking)return result('PROCESSING',{method:`正在检查已有文字层，已检查${checked}/${count}页`,reason:'检测完成后决定本地读取、云OCR或手工处理；检查期间可以继续核对和填写。',phase:phase||{title:'读取材料和 PDF 文字层',hint:'本地读取较长 PDF 可能需要一些时间。'},file:f});
  if(active)return result('PROCESSING',{method,reason:phase.hint,phase,file:f});
  const ai=itemJobs(item,'AI')[0],analyzed=['SUCCEEDED','SUCCESS','COMPLETED'].includes(item.analysis_status)||['SUCCEEDED','STALE'].includes(ai?.state),raw=policyJobResult(latest),error=latest?.error_message||raw.stop_reason||policy.stop_reason||'',stop=ocrStopNotice(latest,policy);
  const missingPart=(f.participation_mode==='MANUAL'||(endpoint&&!nativeCount)||analyzed)&&!hasPart,missingSeal=['NOT_FOUND','LIMITED'].includes(f.seal_status)&&!hasSeal,manualComplete=hasPart&&hasSeal&&evidence.name&&evidence.time;
  const actions=[];if(missingPart)actions.push({label:'定位本人信息并框选',page:pages.find(p=>missing.includes(p.page_index))?.id||pages[0]?.id});if(missingSeal)actions.push({label:'寻找并框选印章',page:pages[0]?.id});
  const reasons=[];if(missingPart)reasons.push(endpoint?'中间本人参与信息尚未补充，请到原件定位框选。':'尚未定位本人参与信息，请核对原件并框选。');if(missingSeal)reasons.push(f.seal_message||'首页和末页未找到印章，请自行查看原件其它页并手动框选。');if(stop&&!manualComplete)reasons.unshift(stop.message);
  if(!manualComplete&&((stop&&!stop.manual)||((latest?.state==='FAILED'||policy.stopped)&&!stop&&!jobNeedsManual(latest))))return result('ERROR',{method,reason:stop?.message||(error||policy.message||'材料识别未完成')+'；已有结果保留，可查看原件手动补充或重试。',actions:[{label:'查看原页并补充',page:pages[0]?.id}],file:f});
  if(manualComplete)return result('CHECK',{method,reason:'小项证据已补齐，待人工核对。'+((latest?.state==='FAILED'||policy.stopped)?'此前自动识别未完成，原异常记录保留；已有手工证据可正常使用。':''),file:f,sealSatisfied:true});
  if(reasons.length)return result('MANUAL',{method,reason:reasons.join(' '),actions:actions.length?actions:[{label:'查看原页并补充',page:pages[0]?.id}],file:f,sealSatisfied:Boolean(hasSeal)});
  if(covered||f.seal_status==='FOUND'||hasPart||['COMPLETED','PARTIAL'].includes(f.automatic_ocr_status)||latest?.state==='SUCCEEDED')return result('CHECK',{method,reason:hasPart?'小项已有本人证据，名称、时间、印章可来自其它附件；请核对计分与证据，材料状态不代表审核通过。':'已有结果可核对；AI分析和正式审核分别进行。',file:f,sealSatisfied:Boolean(hasSeal)});
  return result('PENDING',{method,reason:'等待材料识别；可先核对原件和填写信息。',file:f});
}
function aiTask(item,forcedJob=null){
  const jobs=forcedJob?[forcedJob]:itemJobs(item,'AI'),job=jobs.find(j=>j.state==='RUNNING')||jobs.find(j=>j.state==='QUEUED')||jobs[0],phase=processingPhase(job),policy=policyJobResult(job),stop=ocrStopNotice(job);
  if(phase)return {label:phase.title,color:'info',busy:true,description:phase.hint,phase};
  if(job?.state==='FAILED')return {label:'分析异常',color:'danger',description:stop?.message||job.error_message||job.stage||'已有材料保留，可稍后重试。'};
  if(jobNeedsManual(job))return {label:'需手工填写',color:'warning',description:stop?.message||policy.message||'未得到可分析文字，AI未调用。'};
  if(policy.policy_expired)return {label:'请重新分析',color:'warning',description:'识别策略已更新，旧任务结果保留。'};
  if(['SUCCEEDED','STALE'].includes(job?.state)||['SUCCEEDED','SUCCESS','COMPLETED'].includes(item.analysis_status)){
    const current=currentScoreResolution(item),suggested=analysisScoreResolution(job?.result_json||{});
    if(current.status==='MANUAL')return {label:'人工分值已保留',color:'purple',description:current.message||'当前人工核定分值保留，自动建议不会覆盖。'};
    if(unresolvedScore(current))return {label:'分值待核定',color:'warning',description:'条目已提取，分值待核定。'+scoreResolutionMessage(current)};
    if(unresolvedScore(suggested))return {label:'新建议待核对',color:'warning',description:'新分析已提取条目，建议分值尚待核定；当前已核定分值保留。'+scoreResolutionMessage(suggested)};
    if(current.status==='RULE_APPLIED'||suggested?.status==='RULE_APPLIED')return {label:'分值已生成，待核对',color:'purple',description:'已按明确依据生成分值，请核对条目与证据；自动计分不代表审核通过。'};
    return {label:'已分析，待核对',color:'purple',description:'AI分析不代表审核通过；请核对生成内容、计分与证据。'};
  }
  return {label:'未分析',color:'',description:'材料识别完成后会自动开始AI分析。'};
}
function processingNotice(item){const jobs=itemJobs(item),active=jobs.filter(j=>['QUEUED','RUNNING'].includes(j.state)),job=active.find(j=>j.kind==='OCR'&&j.state==='RUNNING')||active.find(j=>j.state==='RUNNING')||active[0];if(!job)return '';const phase=processingPhase(job),waiting=active.find(j=>j.kind==='AI'&&waitingForOcr(j));return `<div class="notice processing-notice" role="status"><span class="tiny-spinner"></span><div><strong>${esc(phase.title)}</strong><p>${esc(phase.hint)}${waiting&&job.kind==='OCR'?' AI等待此识别任务完成，原OCR继续执行。':''}</p><p>识别完成后自动进行AI分析，可能需要一些时间；每人最多同时识别3个文件、分析3个小项，其余排队。可切换站内页面，完成后自动更新。</p>${phase.elapsed?`<p class="processing-elapsed">${esc(phase.elapsed)}</p>`:''}</div></div>`;}
function filePolicyHtml(file){
  const status=normalizeMaterialState(file),f=status.file||file;
  return `<div class="file-processing-status file-ocr-policy" data-file-state="${status.state}"><div class="row gap6"><span class="tag ${status.color}">${status.state==='PROCESSING'?'<span class="tiny-spinner"></span>':''}${esc(status.label)}</span><strong class="small">${esc(status.method||'')}</strong></div>${status.reason?`<p class="file-processing-reason">${esc(status.reason)}</p>`:''}${status.actions.length?`<div class="row wrap gap6 file-processing-actions">${status.actions.map(action=>action.page?`<button class="text-action" data-action="v2-crop-page" data-page="${esc(action.page)}">${icon('crop')}${esc(action.label)}</button>`:'').join('')}${f.url?`<a class="text-action" href="${urlSafe(f.url)}" target="_blank" rel="noopener">查看完整原件</a>`:''}</div>`:''}${!status.busy?`<p class="file-seal-status small muted">${esc(f.seal_status==='FOUND'?(f.seal_message||'首页或末页已检出印章，请核对证据。'):f.seal_status==='NOT_FOUND'?(status.sealSatisfied?'本附件首页和末页未找到印章；同小项已有其它印章证据，可合并核对。':'首页和末页未找到印章，请查看原件其它页并手动框选。'):filePageCount(f)===1?'印章只自动检查此页一次。':'印章只自动检查首页和末页。')}</p>`:''}</div>`;
}
function policyJobResult(job){const result=job?.result_json||{};return result.ocr_result||result.ocr||result;}
function skippedOcrFiles(item,job){const result=policyJobResult(job);return item.skipped_files||result.skipped_files||result.manual_required_files||[];}
function jobNeedsManual(job){const result=policyJobResult(job);return Boolean(result.manual_required||result.status==='MANUAL_REQUIRED'||(job?.requires_manual&&!job.partial&&!result.partial));}
function policyVisibleCrops(crops,editable,files=S.detail?.files||[]){
  return crops.filter(crop=>{
    if(crop.source!=='AI')return true;
    if(typeof crop.automatic_policy_allowed==='boolean')return crop.automatic_policy_allowed;
    if(!editable)return true;
    const file=files.find(f=>f.id===crop.file_id||(f.pages||[]).some(p=>p.id===crop.page_id));if(!file)return true;
    const pages=file.pages||[],page=pages.find(p=>p.id===crop.page_id),number=Number(page?.page_index??crop.page_number);
    const boundary=number===1||number===filePageCount(file),native=[crop.ocr_source,crop.recognition_api,crop.api_name,crop.origin,crop.metadata?.ocr_source].some(source=>['NativePDFText','NATIVE_PDF_TEXT'].includes(source));
    if(crop.validity_kind==='SEAL')return boundary;
    if(endpointOnlyFile(file))return crop.evidence_type==='PARTICIPATION'?native:boundary||native;
    return true;
  });
}
function itemTask(i){const material=normalizeMaterialState(null,i),ai=aiTask(i);return {...material,description:material.reason,materialBusy:material.busy,busy:Boolean(material.busy||ai.busy)};}
function materialTaskContent(i){const task=itemTask(i);return `<span class="tag ${task.color}" title="${esc(task.description||task.label)}">${task.materialBusy?'<span class="tiny-spinner"></span>':''}${esc(task.label)}</span>${task.method?`<small>${esc(task.method)}</small>`:''}`;}
function aiTaskContent(i){const task=aiTask(i);return `<span class="tag ${task.color}" title="${esc(task.description||task.label)}">${task.busy?'<span class="tiny-spinner"></span>':''}${esc(task.label)}</span>`;}
function waitingForOcr(job){
  if(job.kind!=='AI'||job.state!=='QUEUED')return false;
  const dependencyId=job.wait_for_ocr_job_id||job.result_json?.wait_for_ocr_job_id,dependency=dependencyId&&S.jobs.find(j=>j.id===dependencyId);
  if(dependency)return ['QUEUED','RUNNING'].includes(dependency.state);
  if(typeof job.waiting_for_ocr==='boolean')return job.waiting_for_ocr;
  return S.jobs.some(j=>j.application_id===job.application_id&&j.kind==='OCR'&&['QUEUED','RUNNING'].includes(j.state))||String(job.stage||'').includes('等待OCR');
}
function taskTag(i){return `<div class="material-task-cell" data-item-progress="${esc(i.id)}">${materialTaskContent(i)}</div>`;}
function itemPolicySummary(item){
  const state=normalizeMaterialState(null,item),policy=itemPolicyResult(item),messages=Array.from(new Set((state.files||[state]).filter(s=>['MANUAL','ERROR'].includes(s.state)).map(s=>s.reason).filter(Boolean)));
  if(!messages.length)return '';
  const skipped=policy.skipped_files||[],skipIds=new Set(skipped.map(f=>f.file_id)),others=(policy.processed_files||[]).filter(f=>!skipIds.has(f.file_id||f.id)).length;if(others)messages.push(`${others}份其它附件继续独立处理。`);
  return `<div class="notice ${state.state==='ERROR'?'':'warning'} ocr-result-notice">${icon('info')}<div>${messages.map(m=>`<p>${esc(m)}</p>`).join('')}<p>本人、活动名称、时间和印章证据可来自不同附件；可手动补充后正常审核，不会自动切换服务商。</p></div></div>`;
}
function jobPresentation(job){const item=findItem(job.application_id)||{id:job.application_id};const state=job.kind==='AI'?aiTask(item,job):normalizeMaterialState(null,item,job);return {...state,manual:state.state==='MANUAL',description:state.reason||state.description};}
function jobStage(job){const phase=processingPhase(job);if(phase)return phase.title+' · '+phase.hint;const state=jobPresentation(job),stop=ocrStopNotice(job);return stop?stop.message:job.kind==='AI'&&['SUCCEEDED','STALE'].includes(job.state)&&(unresolvedScore(currentScoreResolution(findItem(job.application_id)||{}))||unresolvedScore(analysisScoreResolution(job.result_json||{})))?(state.description||state.label):job.state==='SUCCEEDED'?(job.stage||state.description||state.label):(state.description||job.stage||state.label);}

function setupWorkspaceShell(){
  if(isStudent()){
    const select=$('#year-selector');if(select){const span=document.createElement('span');span.className='current-year-label';span.id='current-year-label';span.title='当前填报学年由管理员指定';span.textContent=activeYear()?.name||'当前学年';select.replaceWith(span);}
  }
  if(isAdmin()){
    const button=document.createElement('button');button.className='nav-item';button.dataset.action='v2-statistics-year';button.innerHTML=icon('calendar')+'<span>当前统计学年</span>';$('.nav-list')?.append(button);
  }
  const existing=$('#background-jobs');if(existing)existing.remove();
  if(S.jobsOwner!==S.user?.id){S.jobs=[];S.jobsLoaded=false;S.completedJobKeys.clear();S.jobSignature='';S.jobsOwner=S.user?.id;S.submissions=[];S.workspaceItem=null;S.detail=null;S.selectedItem=null;S.expandedSubmission=null;S.workspaceDirty=false;S.workspaceScoreTouched=false;S.checkedItems.clear();S.comparisonRequest++;}
  if(isStudent()){
    const bar=document.createElement('section');bar.id='background-jobs';bar.className='jobs-dock hidden';bar.setAttribute('aria-live','polite');bar.innerHTML='<div id="upload-batches"></div><div id="jobs-summary"></div><div id="jobs-list" class="jobs-list hidden"></div>';$('.shell')?.append(bar);
    bar.addEventListener('click',e=>{const button=e.target.closest('[data-upload-action]');if(!button)return;const batch=S.uploadBatches.find(b=>b.id===button.dataset.batch&&b.ownerId===S.user?.id);if(button.dataset.uploadAction==='expand'){S.uploadQueueExpanded=!S.uploadQueueExpanded;drawJobs();}else if(button.dataset.uploadAction==='retry'&&batch&&batch.state==='FAILED'){batch.state='QUEUED';batch.error='';batch.items.filter(item=>!item.confirmed).forEach(item=>{item.state='QUEUED';item.error='';});pumpUploadBatches();drawJobs();}else if(button.dataset.uploadAction==='dismiss'&&batch&&batch.state==='COMPLETED'){S.uploadBatches=S.uploadBatches.filter(b=>b!==batch);drawJobs();}});
    bar.addEventListener('click',e=>{const button=e.target.closest('[data-upload-action="discard"]');if(!button)return;const batch=S.uploadBatches.find(value=>value.id===button.dataset.batch&&value.ownerId===S.user?.id&&value.state==='FAILED');if(batch&&window.confirm('放弃这批未完成的本地上传？已上传确认的小项会保留，未完成项不会正式申报。')){batch.items.forEach(item=>item.files=[]);S.uploadBatches=S.uploadBatches.filter(value=>value!==batch);drawJobs();}});
    bar.onclick=e=>{const button=e.target.closest('[data-job-action]');if(!button)return;if(button.dataset.jobAction==='expand')$('#jobs-list').classList.toggle('hidden');if(button.dataset.jobAction==='item')openJobItem(button.dataset.id);if(button.dataset.jobAction==='retry')enqueueItem(button.dataset.id,button.dataset.kind,true).catch(err=>toast(err.message,true));};
  }
  if(S.jobTimer)clearInterval(S.jobTimer);S.jobTimer=null;
  if(isStudent()){pollJobs(true);S.jobTimer=setInterval(()=>pollJobs(),2200);}
  drawJobs();pumpUploadBatches();
}
async function pollJobs(){
  if(!S.user||!isStudent()||S.pollingJobs)return;S.pollingJobs=true;
  const requestOwnerId=S.user.id;
  try{
    const response=await api('/analysis-jobs?active_only=false');if(S.user?.id!==requestOwnerId)return;
    const jobs=arr(response.jobs||response),completed=[];
    for(const job of jobs){if(!['SUCCEEDED','FAILED','STALE'].includes(job.state))continue;const key=job.id+':'+job.state+':'+(job.finished_at||'');if(!S.completedJobKeys.has(key)){if(S.jobsLoaded)completed.push({key,id:job.application_id});else S.completedJobKeys.add(key);}}
    const signature=JSON.stringify(jobs.map(j=>[j.id,j.state,j.progress,j.stage,j.finished_at]));
    S.jobs=jobs.sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));S.jobSignature=signature;drawJobs();
    S.jobsLoaded=true;
    let refreshedWorkspace=false;
    if(completed.length&&['applications','review','classApplications','submission','history'].includes(S.view)&&$('#submission-groups')){await refreshWorkspaceData({fromPoll:true,completedItems:new Set(completed.map(job=>job.id))});refreshedWorkspace=true;}
    completed.forEach(job=>S.completedJobKeys.add(job.key));return {refreshedWorkspace};
  }catch(e){if(e.status!==404&&e.status!==401)console.debug('任务状态暂时不可用');}finally{S.pollingJobs=false;}
}
function drawJobs(){
  const bar=$('#background-jobs');if(!bar)return;
  const uploads=S.uploadBatches.filter(batch=>batch.ownerId===S.user?.id);drawUploadBatches(uploads);
  const active=S.jobs.filter(j=>['QUEUED','RUNNING'].includes(j.state)).sort((a,b)=>{const rank=j=>j.state==='RUNNING'?(j.kind==='OCR'?0:1):2;return rank(a)-rank(b)||String(b.created_at||'').localeCompare(String(a.created_at||''));});
  const activeIds=new Set(active.map(j=>j.id)),recent=[...active,...S.jobs.filter(j=>!activeIds.has(j.id)).slice(0,6)];
  if(!recent.length&&!uploads.length){bar.classList.add('hidden');document.body.classList.remove('has-jobs');return;}
  bar.classList.remove('hidden');document.body.classList.add('has-jobs');
  $('#jobs-summary').classList.toggle('hidden',!recent.length);if(!recent.length){$('#jobs-list').replaceChildren();return;}
  patchHtml($('#jobs-summary'),`<button class="jobs-summary" data-job-action="expand"><span class="row gap6">${active.length?'<span class="tiny-spinner"></span>':icon('check')}<strong>${active.length?`${active.length} 个后台任务正在进行`:'后台任务已处理'}</strong><span class="muted small">切换页面或刷新后继续运行</span></span><span class="row gap6 small">查看进度 ${icon('chevron')}</span></button>${active.length?'<p class="jobs-busy-notice">识别完成后自动分析；每人最多同时识别3个文件、分析3个小项，其余排队。可切换页面，完成后自动更新。</p>':''}`);
  patchJobRows($('#jobs-list'),recent.map(j=>{const presentation=jobPresentation(j),errorText=ocrStopNotice(j)?.message||j.error_message;const title=findItem(j.application_id)?.title||j.title||j.application_title||'加分小项';const ratio=Math.min(100,Number(j.progress||0)/Math.max(1,Number(j.total||1))*100);return `<div class="job-row" data-job-id="${esc(j.id)}" data-job-kind="${esc(j.kind)}"><div class="flex1"><button class="text-action" data-job-action="item" data-id="${esc(j.application_id)}">${esc(title)}</button><p class="small muted">${jobName(j.kind)} · ${esc(jobStage(j))}${elapsedText(j)?' · '+esc(elapsedText(j)):''}${j.state==='RUNNING'&&Number(j.total)>0?` · ${Number(j.progress||0)}/${Number(j.total)}`:''}</p>${['QUEUED','RUNNING'].includes(j.state)&&Number(j.total)>0?`<div class="progress-track mt8"><span style="width:${ratio}%"></span></div>`:''}${errorText?`<p class="small ${presentation.manual?'muted':'danger-text'} mt8">${esc(errorText)}</p>`:''}</div><span class="tag ${presentation.color}">${esc(presentation.label)}</span>${j.state==='FAILED'&&!presentation.manual?`<button class="text-action" data-job-action="retry" data-id="${esc(j.application_id)}" data-kind="${esc(j.kind)}">重试</button>`:''}</div>`;}).join(''));
  refreshProcessingStatus();
}
function patchHtml(element,html){if(element&&element._renderedHtml!==html){element.innerHTML=html;element._renderedHtml=html;}}
function patchStatusTree(current,next){
  if(current.nodeType===Node.TEXT_NODE){if(current.nodeValue!==next.nodeValue)current.nodeValue=next.nodeValue;return;}
  for(const attribute of Array.from(current.attributes||[]))if(!next.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
  for(const attribute of Array.from(next.attributes||[]))if(current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
  for(let index=0;index<next.childNodes.length;index++){const incoming=next.childNodes[index],existing=current.childNodes[index];if(!existing)current.append(incoming.cloneNode(true));else if(existing.nodeType!==incoming.nodeType||existing.nodeName!==incoming.nodeName)existing.replaceWith(incoming.cloneNode(true));else patchStatusTree(existing,incoming);}
  while(current.childNodes.length>next.childNodes.length)current.lastChild.remove();
}
function patchJobRows(container,html){
  const holder=document.createElement('div');holder.innerHTML=html;const scroll=container.scrollTop,focus=container.contains(document.activeElement)?document.activeElement:null,ids=new Set(Array.from(holder.children).map(el=>el.dataset.jobId));
  for(const row of Array.from(container.children))if(!ids.has(row.dataset.jobId))row.remove();
  Array.from(holder.children).forEach((incoming,index)=>{const row=Array.from(container.children).find(el=>el.dataset.jobId===incoming.dataset.jobId);if(row){patchStatusTree(row,incoming);if(container.children[index]!==row)container.insertBefore(row,container.children[index]||null);}else container.insertBefore(incoming,container.children[index]||null);});
  if(focus?.isConnected&&document.activeElement!==focus)focus.focus({preventScroll:true});
  container.scrollTop=scroll;
}
function refreshProcessingStatus(){
  $$('[data-item-progress]').forEach(el=>{const item=findItem(el.dataset.itemProgress);if(item)patchHtml(el,materialTaskContent(item));});
  $$('[data-ai-progress]').forEach(el=>{const item=findItem(el.dataset.aiProgress);if(item)patchHtml(el,aiTaskContent(item));});
  const processing=$('#workspace-processing-notice');if(processing&&S.workspaceItem)patchHtml(processing,processingNotice(S.workspaceItem));
  if(S.workspaceItem)$$('.comparison-material-body [data-action="v2-analyze-item"]').forEach(button=>{button.disabled=itemTask(S.workspaceItem).busy;});
  patchGroupSummaries();
  $$('.source-file-list [data-source-file]').forEach(article=>{const file=S.workspaceItem?.files?.find(f=>f.id===article.dataset.sourceFile);if(!file)return;const current=$('.file-processing-status',article),html=filePolicyHtml(file);if(current&&current.outerHTML!==html){const holder=document.createElement('div');holder.innerHTML=html;current.replaceWith(holder.firstElementChild);}});
}
function patchGroupSummaries(){
  for(const group of S.submissions){const items=groupItems(group),approved=items.filter(i=>itemState(i)==='APPROVED').length,draft=items.filter(itemEditable).length,pending=items.filter(i=>['PENDING_PEER','PENDING_COUNSELOR'].includes(itemState(i))).length,active=items.filter(i=>itemTask(i).busy).length;
    patchHtml($(`[data-group-item-count="${group.id}"]`),`${items.length} <small>小项</small>`);
    patchHtml($(`[data-group-approved-count="${group.id}"]`),`${approved} <small>已通过</small>`);
    patchHtml($(`[data-group-approved-total="${group.id}"]`),`${fmt(group.approved_total)} <small>已通过分</small>`);
    patchHtml($(`[data-group-workflow-count="${group.id}"]`),`${draft} 项可编辑 · ${pending} 项待审 · ${approved} 项通过`);
    const busy=$(`[data-group-busy="${group.id}"]`);if(busy){busy.classList.toggle('hidden',!active);patchHtml(busy,`<span class="tiny-spinner"></span>${active} 项识别中`);}
  }
  const count=$('#workspace-count'),text=`${S.submissions.length} 份大类申报 · ${S.submissions.reduce((n,g)=>n+groupItems(g).length,0)} 个小项`;if(count&&count.textContent!==text)count.textContent=text;
  updateCheckedCount();
}
function comparisonEmptyHtml(){return `<div class="comparison-empty">${icon('eye')}<span>点击小项“对照核对”，在左侧查看材料，右侧核对条目与证据。</span></div>`;}
function closeWorkspaceComparison(){
  if(!S.selectedItem)return true;
  if(S.workspaceDirty&&!window.confirm('当前小项有未保存修改。关闭对照将放弃这些修改，是否继续？'))return false;
  const id=S.selectedItem;S.comparisonRequest++;S.selectedItem=null;S.workspaceItem=null;S.workspaceDirty=false;S.workspaceScoreTouched=false;
  S.detail=null;
  $$('.workspace-comparison').forEach(el=>{if($('.comparison-header',el)||$('.loading-panel',el))el.innerHTML=comparisonEmptyHtml();});
  $$('[data-item-row]').forEach(row=>row.classList.remove('selected'));
  const button=$(`[data-action="v2-select-item"][data-id="${id}"]`);button?.focus({preventScroll:true});button?.scrollIntoView({block:'nearest'});return true;
}
function findItem(id){return S.submissions.flatMap(g=>groupItems(g)).find(i=>i.id===id);}
async function openJobItem(id){
  try{await openApplicationWorkspace(id,{includeDeleted:true});}catch(e){toast(e.message,true);}
}
async function openApplicationWorkspace(id,{includeDeleted=false}={}){
  if(!S.user||!id)return;
  if(S.selectedItem===id&&$('#workspace-item-form')){$('.comparison-header')?.scrollIntoView({block:'start'});return;}
  if(S.workspaceDirty&&!window.confirm('当前小项有未保存修改。切换后将放弃这些修改，是否继续？'))return;
  const actor=S.user.id,sequence=S.renderSequence,request=(S.applicationOpenRequest||0)+1;S.applicationOpenRequest=request;
  const detail=await api(`/applications/${id}`+(includeDeleted?'?include_deleted=true':''));
  if(S.user?.id!==actor||sequence!==S.renderSequence||request!==S.applicationOpenRequest)return;
  const own=isStudent()&&detail.student_user_id===actor,previous=detail.year_id!==activeYear()?.id;
  let view;
  if(own)view=previous||detail.viewing_history?'history':'applications';
  else if(detail.can_review&&['PENDING_PEER','PENDING_COUNSELOR'].includes(itemState(detail))&&navItems().includes('review'))view='review';
  else if(navItems().includes('classApplications'))view='classApplications';
  else throw new Error('当前账号不能在班级申报中查看此小项。');
  S.classId=detail.class_id;S.yearId=detail.year_id;
  if(view==='history')S.historyYear=detail.year_id;
  if(view==='review'&&isStudent())S.reviewYear=detail.year_id;
  const yearSelector=$('#year-selector'),classSelector=$('#class-selector');if(yearSelector)yearSelector.value=S.yearId;if(classSelector)classSelector.value=S.classId;
  await go(view,{applicationId:id,includeDeleted:Boolean(detail.viewing_history)});
}

async function renderSubmissionWorkspace(seq){
  const mine=['applications','history','submission'].includes(S.view)&&(!S.params.scope||S.params.scope==='mine');
  const historical=S.view==='history';
  if(isStudent()&&mine&&!historical)S.yearId=activeYear()?.id||S.yearId;
  const scope=S.view==='review'?'review':S.view==='classApplications'?'class':S.params.scope||'mine';
  const year=historical?(S.historyYear||S.boot.years.find(y=>y.id!==activeYear()?.id)?.id||activeYear()?.id):scope==='review'&&isStudent()?(S.reviewYear||activeYear()?.id):S.yearId;
  const list=arr(await api('/submissions?'+new URLSearchParams({year_id:year||'',class_id:S.classId||'',scope,...(historical||S.params.includeDeleted?{include_deleted:'true'}:{})})));if(seq!==S.renderSequence)return;
  let reviewStats=null;if(scope==='review'&&isCounselor()){reviewStats=(await api('/dashboard'+query({year_id:year}))).stats||{};if(seq!==S.renderSequence)return;}
  S.submissions=list;S.workspaceScope=scope;S.workspaceYear=year;S.workspaceHistorical=historical;S.workspaceDirty=false;S.workspaceScoreTouched=false;
  if(S.params.id && S.view==='submission')S.expandedSubmission=S.params.id;
  if(S.params.applicationId){const group=list.find(value=>groupItems(value).some(item=>item.id===S.params.applicationId));S.expandedSubmission=group?.id||null;S.selectedItem=group?S.params.applicationId:null;if(!group)toast('小项状态已更新，请在列表核对最新记录。',true);}
  if(!list.some(g=>g.id===S.expandedSubmission))S.expandedSubmission=list[0]?.id||null;
  const headings=historical?['历史申报记录','旧学年材料、计分与审核记录保留，可查看，学生不在此新增或修改。']:scope==='mine'?['我的申报','先整理上传材料，再核对各小项。每个加分类别保留一份申报。']:scope==='review'?['待审申请','按大类查看材料与小项，逐项比对和审核，已通过项目保持锁定。']:['班级申报','按学生和类别查看完整小项，核查申报、审核与识别进度。'];
  const reference=`<a class="btn" href="/api/reference/scoring-standard" target="_blank" rel="noopener">${icon('book')}查看评分标准</a>`;
  const actions=reference+(scope==='mine'&&!historical?`<button class="btn" data-action="v2-bulk-analyze">${icon('spark')}重试未分析项</button><button class="btn primary" data-action="new-application">${icon('upload')}上传加分材料</button>`:hasPermission('export.read')&&!historical?`<button class="btn" data-action="export">${icon('download')}导出汇总</button>`:'');
  $('#page').innerHTML=pageTitle(...headings,actions)+(historical?`<div class="toolbar"><label class="row small">历史学年 <select id="history-year-selector">${options(S.boot.years,year)}</select></label><span class="tag">学生只读</span></div>`:scope==='mine'?`<div class="workspace-year-note">${icon('calendar')}<span>当前填报：<strong>${esc(activeYear()?.name||'管理员指定学年')}</strong></span><span class="muted">由管理员指定；旧学年请进入“历史记录”</span></div><div class="category-grid mb16">${S.boot.categories.map((c,i)=>`<button class="category-entry" data-action="new-application" data-category="${esc(c.id)}"><span class="cat-icon c${i%7}">${icon(catIcons[i%7])}</span><div><h3>${esc(c.name)}</h3><p>上传材料 · 上限 ${Number(c.default_cap)} 分</p></div></button>`).join('')}</div><div class="notice mb16">${icon('info')}<span>每个主文件默认一个小项，可补充多份附件。上传后自动读取文字或OCR，完成后自动进行AI分析。每人最多同时识别3个文件、分析3个小项，其余排队；可继续上传其它类别。</span></div>`:'')+`<div class="toolbar mt24"><div class="row wrap"><span class="muted small" id="workspace-count">${list.length} 份大类申报 · ${list.reduce((n,g)=>n+groupItems(g).length,0)} 个小项</span></div><input id="workspace-search" class="search-input" placeholder="搜索类别、学生或小项名称" type="search" aria-label="搜索申报"></div><div id="submission-groups"></div>`;
  if(historical)$('#history-year-selector').onchange=e=>{S.historyYear=e.target.value;go('history');};
  if(scope==='review'&&isStudent()){
    const filter=document.createElement('div');filter.className='toolbar';filter.innerHTML=`<label class="row small">审查学年 <select id="review-year-selector">${options(S.boot.years,year)}</select></label><span class="small muted">仅筛选待审记录，不改变本人填报的当前学年</span>`;$('#workspace-search').closest('.toolbar').before(filter);$('#review-year-selector').onchange=e=>{S.reviewYear=e.target.value;go('review');};
  }
  if(reviewStats){const notice=document.createElement('div');notice.className='notice mb16';notice.id='counselor-review-stages';notice.innerHTML=`${icon('info')}<span>待辅导员审核 <strong>${Number(reviewStats.pending_counselor||0)}</strong> 项 · 待学生初审 <strong>${Number(reviewStats.pending_peer||0)}</strong> 项。需辅导员认定活动资格的小项，经学生初审后进入此队列；集体活动等可在初审通过后直接通过。</span><button class="btn sm" data-nav="classApplications">查看班级申报</button>`;$('#submission-groups').before(notice);}
  $('#workspace-search').oninput=drawSubmissionGroups;await drawSubmissionGroups();
}
async function renderHistoryWorkspace(seq){return renderSubmissionWorkspace(seq);}
async function drawSubmissionGroups(){
  const search=($('#workspace-search')?.value||'').toLowerCase();const groups=S.submissions.filter(g=>(`${g.category_name} ${g.student_name} ${groupItems(g).map(i=>i.title).join(' ')}`).toLowerCase().includes(search));
  const el=$('#submission-groups');if(!el)return;
  if(!groups.length&&S.workspaceScope==='review'&&isCounselor()){el.innerHTML=`<section class="card">${empty(search?'没有匹配的待审小项':'当前没有待辅导员审核的小项',search?'可清空搜索条件查看。':'学生初审完成后，需要辅导员认定活动资格的小项会显示在这里；可查看班级申报跟踪其它小项。',!search?'<button class="btn sm" data-nav="classApplications">查看班级申报</button>':'')}</section>`;return;}
  el.innerHTML=groups.length?groups.map(g=>groupCard(g)).join(''):`<section class="card">${empty(S.workspaceHistorical?'该学年暂无申报记录':'尚未上传加分材料',S.workspaceScope==='mine'&&!S.workspaceHistorical?'点击上方加分类别，先整理并确认上传材料。':'当前筛选范围内没有申报。')}</section>`;
  $$('[data-item-check]',el).forEach(c=>c.onchange=()=>{if(c.checked)S.checkedItems.add(c.dataset.itemCheck);else S.checkedItems.delete(c.dataset.itemCheck);updateCheckedCount();});
  $$('[data-group-check]',el).forEach(c=>c.onchange=()=>{const group=S.submissions.find(g=>g.id===c.dataset.groupCheck);groupItems(group).filter(itemEditable).forEach(i=>{c.checked?S.checkedItems.add(i.id):S.checkedItems.delete(i.id);const checkbox=$(`[data-item-check="${i.id}"]`);if(checkbox)checkbox.checked=c.checked;});updateCheckedCount();});
  const expanded=S.submissions.find(g=>g.id===S.expandedSubmission);if(expanded && S.selectedItem && groupItems(expanded).some(i=>i.id===S.selectedItem))await loadWorkspaceItem(S.selectedItem).catch(e=>toast(e.message,true));
}
function groupCard(g){
  const expanded=g.id===S.expandedSubmission;const items=groupItems(g),draft=items.filter(itemEditable).length,pending=items.filter(i=>['PENDING_PEER','PENDING_COUNSELOR'].includes(itemState(i))).length,approved=items.filter(i=>itemState(i)==='APPROVED').length;
  const mine=g.student_user_id===S.user.id;const edit=mine&&!S.workspaceHistorical&&g.year_id===activeYear()?.id;
  const active=items.filter(i=>itemTask(i).busy).length;
  return `<section class="card submission-group ${expanded?'expanded':''}" data-group-id="${esc(g.id)}"><header class="submission-head"><button class="submission-toggle" data-action="v2-toggle-group" data-id="${esc(g.id)}"><span class="cat-icon">${icon(catIcons[S.boot.categories.findIndex(c=>c.id===g.category_id)]||'file')}</span><span><strong>${esc(g.category_name)}</strong><small>${esc(g.student_name)} · ${esc(g.class_name)} · ${esc(g.year_name)}</small></span></button><div class="submission-head-stats"><span data-group-item-count="${esc(g.id)}">${items.length} <small>小项</small></span><span class="success-text" data-group-approved-count="${esc(g.id)}">${approved} <small>已通过</small></span><span class="score-num" data-group-approved-total="${esc(g.id)}">${fmt(g.approved_total)} <small>已通过分</small></span><span class="tag info ${active?'':'hidden'}" data-group-busy="${esc(g.id)}"><span class="tiny-spinner"></span>${active} 项识别中</span>${edit?`<button class="btn sm" data-action="v2-add-files" data-id="${esc(g.id)}" data-category="${esc(g.category_id)}">${icon('plus')}增补材料</button>`:''}<button class="icon-btn" data-action="v2-toggle-group" data-id="${esc(g.id)}" aria-label="展开小项">${icon(expanded?'back':'chevron')}</button></div></header>${expanded?`<div class="submission-overview"><span data-group-workflow-count="${esc(g.id)}">${draft} 项可编辑 · ${pending} 项待审 · ${approved} 项通过</span>${edit?`<span class="row"><button class="text-action" data-action="v2-bulk-analyze" data-id="${esc(g.id)}">${icon('spark')}重试未分析项</button><button class="btn sm primary" data-action="v2-submit-selected" data-id="${esc(g.id)}">提交选中小项 <span data-checked-count="${esc(g.id)}">${items.filter(i=>S.checkedItems.has(i.id)&&itemEditable(i)).length}</span></button></span>`:''}</div><div class="table-wrap"><table class="items-table"><thead><tr>${edit?`<th><input type="checkbox" data-group-check="${esc(g.id)}" aria-label="选择本类可编辑小项"></th>`:''}<th>证明材料</th><th>具体加分条目</th><th>申报分值</th><th>材料状态</th><th>AI 分析</th><th>审核状态</th><th>操作</th></tr></thead><tbody>${items.map(i=>itemRow(g,i,edit)).join('')}</tbody></table></div><div class="workspace-comparison" id="comparison-${esc(g.id)}">${S.selectedItem&&items.some(i=>i.id===S.selectedItem)?loading():`<div class="comparison-empty">${icon('eye')}<span>点击小项“对照核对”，在左侧查看材料，右侧核对条目与证据。</span></div>`}</div>`:''}</section>`;
}
function itemRow(g,i,edit){
  const fileNames=(i.files||[]).map(f=>f.original_name).filter(Boolean);const state=itemState(i);const editable=edit&&itemEditable(i);const selected=S.selectedItem===i.id;
  return `<tr class="${selected?'selected':''}" data-item-row="${esc(i.id)}">${edit?`<td><input type="checkbox" data-item-check="${esc(i.id)}" ${S.checkedItems.has(i.id)?'checked':''} ${editable?'':'disabled'} aria-label="选择 ${esc(i.title||'此小项')}"></td>`:''}<td class="item-file-cell"><div class="row gap6">${icon('file')}<span>${esc(fileNames[0] || i.primary_file_name || i.file_name || '证明材料')}</span></div><small>${fileNames.length>1?`另 ${fileNames.length-1} 份附件`:`${i.file_count||fileNames.length||1} 份附件`}</small></td><td class="title-cell"><div class="table-title">${esc(i.title||'等待分析，请核对填写')}</div>${i.student_modified_score?'<div class="table-sub">学生手动修改过计分</div>':''}</td><td data-item-score="${esc(i.id)}">${itemScoreHtml(i)}</td><td>${taskTag(i)}</td><td><div data-ai-progress="${esc(i.id)}">${aiTaskContent(i)}</div></td><td><span data-item-state="${esc(i.id)}">${statusTag(state)}${i.deleted_at?'<span class="tag">已删除</span>':''}</span></td><td><div class="table-actions"><button class="text-action" data-action="v2-select-item" data-id="${esc(i.id)}" data-group="${esc(g.id)}">${S.workspaceScope==='review'?'比对审核':'对照核对'}</button>${editable?`<button class="text-action danger-text" data-action="v2-delete-item" data-id="${esc(i.id)}" data-group="${esc(g.id)}">删除</button>`:''}</div></td></tr>`;
}
function updateCheckedCount(){$$('[data-checked-count]').forEach(el=>{const g=S.submissions.find(x=>x.id===el.dataset.checkedCount);el.textContent=groupItems(g).filter(i=>itemEditable(i)&&S.checkedItems.has(i.id)).length;});}
async function refreshWorkspaceData({fromPoll=false,completedItems=new Set(),mergeUploads=false}={}){
  const mount=$('#submission-groups'),sequence=S.renderSequence;if(!mount)return;
  const list=arr(await api('/submissions?'+new URLSearchParams({year_id:S.workspaceYear||'',class_id:S.classId||'',scope:S.workspaceScope||'mine',...(S.workspaceHistorical||S.params.includeDeleted?{include_deleted:'true'}:{})})));
  if(!mount.isConnected||mount!==$('#submission-groups')||sequence!==S.renderSequence)return;
  const oldDirty=S.workspaceDirty;S.submissions=list;
  if(!fromPoll&&!oldDirty){await drawSubmissionGroups();return;}
  if(mergeUploads){
    const search=($('#workspace-search')?.value||'').toLowerCase();
    for(const group of list){
      if(!(`${group.category_name} ${group.student_name} ${groupItems(group).map(item=>item.title).join(' ')}`).toLowerCase().includes(search))continue;
      let section=$(`[data-group-id="${group.id}"]`,mount);
      if(!section){if(!mount.querySelector('[data-group-id]'))mount.replaceChildren();if(!S.expandedSubmission&&!S.selectedItem)S.expandedSubmission=group.id;mount.insertAdjacentHTML('beforeend',groupCard(group));section=$(`[data-group-id="${group.id}"]`,mount);}
      const body=$('tbody',section),edit=group.student_user_id===S.user.id&&!S.workspaceHistorical&&group.year_id===activeYear()?.id;
      if(body)for(const item of groupItems(group))if(!$(`[data-item-row="${item.id}"]`,body))body.insertAdjacentHTML('beforeend',itemRow(group,item,edit));
      $$('[data-item-check]',section).forEach(checkbox=>checkbox.onchange=()=>{if(checkbox.checked)S.checkedItems.add(checkbox.dataset.itemCheck);else S.checkedItems.delete(checkbox.dataset.itemCheck);updateCheckedCount();});
      $$('[data-group-check]',section).forEach(checkbox=>checkbox.onchange=()=>{groupItems(group).filter(itemEditable).forEach(item=>{if(checkbox.checked)S.checkedItems.add(item.id);else S.checkedItems.delete(item.id);const field=$(`[data-item-check="${item.id}"]`,section);if(field)field.checked=checkbox.checked;});updateCheckedCount();});
    }
  }
  for(const g of list)for(const i of groupItems(g)){
    const row=$(`[data-item-row="${i.id}"]`);if(!row)continue;
    const title=$('.table-title',row),text=i.title||'等待分析，请核对填写';if(title&&title.textContent!==text)title.textContent=text;
    patchHtml($('[data-item-score]',row),itemScoreHtml(i));
    patchHtml($('[data-item-state]',row),statusTag(itemState(i))+(i.deleted_at?'<span class="tag">已删除</span>':''));
  }
  refreshProcessingStatus();
  if(S.selectedItem&&(!fromPoll||completedItems.has(S.selectedItem)))await refreshWorkspaceMaterials({syncForm:fromPoll});
}

async function handleWorkspaceAction(action,button){
  if(action==='new-application'){uploadOrganizerModal(button.dataset.category);return true;}
  if(action==='v2-close-comparison'){closeWorkspaceComparison();return true;}
  if(action==='v2-add-files'){uploadOrganizerModal(button.dataset.category,button.dataset.id);return true;}
  if(action==='v2-toggle-group'){if(!closeWorkspaceComparison())return true;S.expandedSubmission=S.expandedSubmission===button.dataset.id?null:button.dataset.id;drawSubmissionGroups();return true;}
  if(action==='v2-select-item'){if(S.selectedItem===button.dataset.id&&$('#workspace-item-form')){$('.comparison-header')?.scrollIntoView({behavior:'smooth',block:'start'});return true;}if(S.workspaceDirty&&!window.confirm('当前小项有未保存修改。切换后将放弃这些修改，是否继续？'))return true;S.expandedSubmission=button.dataset.group;S.selectedItem=button.dataset.id;S.workspaceDirty=false;S.workspaceScoreTouched=false;await loadWorkspaceItem(button.dataset.id);$$('[data-item-row]').forEach(r=>r.classList.toggle('selected',r.dataset.itemRow===S.selectedItem));$('.comparison-header')?.scrollIntoView({behavior:'smooth',block:'start'});return true;}
  if(action==='v2-bulk-analyze'){await bulkAnalyze(button,button.dataset.id);return true;}
  if(action==='v2-submit-selected'){await buttonTask(button,()=>submitSelected(button.dataset.id));return true;}
  if(action==='v2-delete-item'){deleteItemModal(button.dataset.group,button.dataset.id);return true;}
  if(action==='v2-save-item'){await buttonTask(button,async()=>{await saveWorkspaceItem();toast('小项已保存');await refreshWorkspaceData();});return true;}
  if(action==='v2-submit-item'){await buttonTask(button,()=>submitWorkspaceItem(button.dataset.id));return true;}
  if(action==='v2-analyze-item'){await buttonTask(button,async()=>{if(S.workspaceDirty)await saveWorkspaceItem();await enqueueItem(button.dataset.id,'AI',button.dataset.force==='true');});refreshProcessingStatus();return true;}
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
  showModal('整理本次上传材料',`<div class="upload-organizer"><div class="organizer-intro"><span class="step-num">01</span><div><h3>先按小项整理文件</h3><p>每个主文件默认一个小项。若一个小项有多份支撑材料，请在对应行点击“补充附件”。此窗口不会进行 OCR。</p></div></div>${ocrPolicyNotice()}<div class="form-grid mt16"><div class="field"><label for="organizer-category">加分类别</label><select id="organizer-category" ${submissionId?'disabled':''}>${options(S.boot.categories,categoryId,'name','请选择加分类别')}</select></div><div class="field"><label>所属学年</label><div class="current-year-field">${esc(activeYear()?.name||'当前统计学年')} <span class="tag">管理员指定</span></div></div></div><label class="upload-zone mt16" id="organizer-drop">${icon('upload')}<p>选择主文件，每个文件建立一个加分小项</p><small>支持 PDF、JPG、JPEG、PNG · 单文件≤200MB，PDF≤500页 · 可一次多选</small><input type="file" id="organizer-main-files" class="hidden" accept=".pdf,.png,.jpg,.jpeg" multiple></label><div id="organizer-groups" class="mt16"></div><div class="notice mt16">${icon('clock')}<span>确认后返回列表继续上传，系统自动读取文字或OCR，再进行AI分析，无需另行点击。每人最多同时识别3个文件、分析3个小项，其余排队；切换页面后继续运行。</span></div></div>`,`<span class="hint" id="organizer-count">尚未选择文件</span><button class="btn" data-action="close-modal" id="organizer-cancel">取消</button><button class="btn primary" id="organizer-confirm" disabled>${icon('upload')}确认上传</button>`,true);
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
  el.innerHTML=S.uploadGroups.length?`<div class="organizer-table"><div class="organizer-table-head"><span>加分小项与证明文件</span><span>附件安排</span></div>${S.uploadGroups.map((g,index)=>`<div class="organizer-row ${g.confirmed?'done':''}"><div class="organizer-row-main"><span class="organizer-index">${index+1}</span><div class="flex1"><strong>${esc(g.files[0]?.name)}</strong><p>${g.files.length} 份材料 · ${Math.ceil(g.files.reduce((n,f)=>n+f.size,0)/1024)} KB</p>${g.files.length>1?`<div class="organizer-attachments">${g.files.slice(1).map(f=>`<span>${icon('file')}${esc(f.name)}</span>`).join('')}</div>`:''}${g.error?`<p class="danger-text mt8">${esc(g.error)}</p>`:''}</div></div><div class="row gap6">${g.confirmed?'<span class="tag success">已确认，自动识别与分析已安排</span>':`<button class="btn sm" data-organizer-action="supplement" data-group="${g.id}">${icon('plus')}补充附件</button><button class="icon-btn" data-organizer-action="remove" data-group="${g.id}" aria-label="移除此小项">${icon('trash')}</button><input type="file" class="hidden" data-supplement-input="${g.id}" accept=".pdf,.png,.jpg,.jpeg" multiple>`}</div></div>`).join('')}</div>`:empty('还没有选择材料','选择多个主文件，会在这里按行整理为多个小项。');
  $('#organizer-count').textContent=`${S.uploadGroups.length} 个小项 · ${S.uploadGroups.reduce((n,g)=>n+g.files.length,0)} 份附件`;
  $('#organizer-confirm').disabled=!S.uploadGroups.some(g=>!g.confirmed)||S.uploading;
}
async function confirmOrganizedUpload(button){
  if(button.disabled)return;const category=$('#organizer-category').value;if(!category){modalError('请先选择加分类别。');return;}
  const groups=S.uploadGroups.filter(item=>!item.confirmed);if(!groups.length)return;button.disabled=true;
  const batch={id:crypto.randomUUID(),ownerId:S.user.id,yearId:activeYear()?.id,yearName:activeYear()?.name||'当前学年',categoryId:category,categoryName:S.boot.categories.find(item=>item.id===category)?.name||'加分材料',submissionId:S.uploadSubmissionId||null,state:'QUEUED',error:'',createdAt:new Date().toISOString(),items:groups.map(item=>({...item,files:[...item.files],fileNames:item.files.map(file=>file.name),totalFiles:item.files.length,uploaded:new Set(item.uploaded),state:'QUEUED',stage:'排队等待上传',error:'',confirmationAttempted:false}))};
  S.uploadBatches.push(batch);S.uploadGroups=[];S.uploadSubmissionId=null;S.uploadQueueExpanded=true;closeModal();drawJobs();pumpUploadBatches();
  toast('已加入后台上传，可继续选择其它大类。上传期间请保留此网站窗口。');
  if(S.view!=='applications'&&!S.workspaceDirty&&!$('#application-form'))await go('applications');
}

function uploadBatchVisible(){return S.uploadBatches.filter(batch=>batch.ownerId===S.user?.id);}
function hasPendingLocalUploads(){return S.uploadBatches.some(batch=>batch.state!=='COMPLETED'&&batch.items.some(item=>!item.confirmed&&item.files.length));}
window.addEventListener('beforeunload',event=>{if(hasPendingLocalUploads()){event.preventDefault();event.returnValue='';}});

function drawUploadBatches(batches=uploadBatchVisible()){
  const container=$('#upload-batches');if(!container)return;
  if(!batches.length){container.replaceChildren();container._renderedHtml='';return;}
  const active=batches.filter(batch=>['QUEUED','RUNNING'].includes(batch.state)).length,failed=batches.filter(batch=>batch.state==='FAILED').length;
  const labels={QUEUED:'等待上传',RUNNING:'上传处理中',FAILED:'部分失败，可重试',COMPLETED:'上传已完成'};
  patchHtml(container,`<div class="upload-queue-head"><button class="text-action" data-upload-action="expand">${active?'<span class="tiny-spinner"></span>':icon('upload')}<strong>文件上传${active?` · ${active} 批进行中`:failed?` · ${failed} 批待处理`:' · 已完成'}</strong>${icon('chevron')}</button></div>${S.uploadQueueExpanded?`<p class="upload-queue-note">可切换站内页面或继续上传其它类别。未完成的文件仍在本窗口上传，请勿刷新或关闭；已入队的识别任务在服务器继续运行。</p><div class="upload-queue-list">${batches.map(batch=>`<section class="upload-batch" data-upload-batch="${esc(batch.id)}"><div class="row between gap6"><strong>${esc(batch.categoryName)} <small>${esc(batch.yearName)}</small></strong><span class="tag ${batch.state==='FAILED'?'danger':batch.state==='COMPLETED'?'success':'info'}">${labels[batch.state]}</span></div><p class="small muted">${batch.items.filter(item=>item.confirmed).length}/${batch.items.length} 个小项已上传确认</p>${batch.items.map(item=>`<div class="upload-queue-item" data-upload-item="${esc(item.id)}"><span>${esc(item.fileNames[0]||'证明材料')}</span><small class="${item.state==='FAILED'?'danger-text':'muted'}">${esc(item.error||item.stage)}${item.totalFiles>1?` · ${item.uploaded.size}/${item.totalFiles} 份文件已保存`:''}</small>${item.warning?`<small class="warning-text">${esc(item.warning)}</small>`:''}</div>`).join('')}${batch.error?`<p class="small danger-text">${esc(batch.error)}</p>`:''}${batch.state==='FAILED'?`<div class="row gap6"><button class="btn sm" data-upload-action="retry" data-batch="${esc(batch.id)}">重试未完成项</button><button class="text-action" data-upload-action="discard" data-batch="${esc(batch.id)}">放弃未完成上传</button></div>`:batch.state==='COMPLETED'?`<button class="text-action" data-upload-action="dismiss" data-batch="${esc(batch.id)}">收起此批记录</button>`:''}</section>`).join('')}</div>`:''}`);
}

function assertUploadOwner(batch){if(S.user?.id!==batch.ownerId||!isStudent())throw new Error('登录账号已变化，请用原账号重试此批上传。');}
function pumpUploadBatches(){
  if(!isStudent())return;
  while(S.uploadWorkers<2){const batch=S.uploadBatches.find(item=>item.ownerId===S.user.id&&item.state==='QUEUED');if(!batch)break;S.uploadWorkers++;batch.state='RUNNING';drawJobs();runUploadBatch(batch).finally(()=>{S.uploadWorkers--;drawJobs();pumpUploadBatches();});}
}
async function refreshUploadList(batch){
  if(S.user?.id!==batch.ownerId||S.view!=='applications'||S.workspaceHistorical||S.workspaceScope!=='mine'||S.workspaceYear!==batch.yearId||!$('#submission-groups'))return;
  try{await refreshWorkspaceData({fromPoll:true,mergeUploads:true});}catch{if(S.user?.id===batch.ownerId)toast('材料已上传，列表暂未更新；可稍后刷新查看。',true);}
}
async function runUploadBatch(batch){
  try{
    assertUploadOwner(batch);
    if(!batch.submissionId){const response=await api('/submissions',{method:'POST',body:{category_id:batch.categoryId,year_id:batch.yearId}});batch.submissionId=response.id||response.submission?.id;if(!batch.submissionId)throw new Error('未取得大类申报编号，请重试。');}
    for(const item of batch.items){
      if(item.confirmed)continue;
      item.state='RUNNING';item.error='';item.stage='准备上传小项';drawJobs();
      try{
        assertUploadOwner(batch);
        if(item.confirmationAttempted){
          const current=await api(`/submissions/${batch.submissionId}`);const existing=groupItems(current.submission||current).find(value=>value.id===item.applicationId);
          if(existing?.confirmed_at){item.confirmed=true;item.state='COMPLETED';item.stage='上传已确认';item.files=[];drawJobs();await refreshUploadList(batch);continue;}
        }
        if(!item.applicationId){assertUploadOwner(batch);const response=await api(`/submissions/${batch.submissionId}/items`,{method:'POST',body:{staged:true}});item.applicationId=response.id||response.application?.id||response.item?.id;if(!item.applicationId)throw new Error('未取得小项编号，请重试。');}
        for(let index=0;index<item.files.length;index++){
          if(item.uploaded.has(index))continue;
          assertUploadOwner(batch);item.stage=`上传并保存第 ${index+1}/${item.totalFiles} 份文件；PDF解析可能需要一些时间`;drawJobs();
          const body=new FormData();body.append('file',item.files[index]);await api(`/applications/${item.applicationId}/files`,{method:'POST',body});item.uploaded.add(index);drawJobs();
        }
        assertUploadOwner(batch);item.stage='文件已保存，确认小项并安排识别';item.confirmationAttempted=true;drawJobs();
        const response=await api(`/submissions/${batch.submissionId}/confirm-upload`,{method:'POST',body:{item_ids:[item.applicationId]}});
        if(!Array.isArray(response.confirmed_item_ids)||!response.confirmed_item_ids.includes(item.applicationId))throw new Error('小项尚未确认完成，请重试。');
        item.confirmed=true;item.state='COMPLETED';item.stage='上传完成，自动识别后将进行AI分析';item.files=[];
        if(response.ocr_errors?.length){item.warning='上传已完成，部分识别未能入队，请在小项中重试识别。';item.stage='上传完成';}
        if(S.user?.id===batch.ownerId){const jobs=response.jobs||[];S.jobs=[...jobs,...S.jobs.filter(job=>!jobs.some(value=>value.id===job.id))];drawJobs();}
        await refreshUploadList(batch);
      }catch(error){item.state='FAILED';item.error=error.message;item.stage='上传未完成';drawJobs();if(S.user?.id!==batch.ownerId)throw error;}
    }
    batch.state=batch.items.every(item=>item.confirmed)?'COMPLETED':'FAILED';
  }catch(error){batch.state='FAILED';batch.error=error.message;for(const item of batch.items)if(!item.confirmed&&item.state!=='FAILED'){item.state='FAILED';item.error=error.message;}}
  finally{drawJobs();if(batch.state==='COMPLETED'&&S.user?.id===batch.ownerId)toast(`${batch.categoryName}材料已上传完成，可在列表核对。`);}
}

async function loadWorkspaceItem(id){
  const group=S.submissions.find(g=>groupItems(g).some(i=>i.id===id));if(!group)return;
  const target=$(`#comparison-${group.id}`);if(!target)return;const request=++S.comparisonRequest;
  target.innerHTML=`<div class="comparison-header"><h3>载入对照材料</h3><button type="button" class="btn sm comparison-close" data-action="v2-close-comparison">${icon('close')}关闭对照</button></div>${loading()}`;
  S.selectedItem=id;const entry=groupItems(group).find(i=>i.id===id);const response=await api(`/applications/${id}`+(entry?.deleted_at?'?include_deleted=true':''));if(S.selectedItem!==id||request!==S.comparisonRequest||!target.isConnected)return;
  S.detail=response;S.workspaceItem=response;S.workspaceDirty=false;S.workspaceScoreTouched=false;S.workspaceFormEdits=0;adoptWorkspaceFormBasis(response);
  const r=response.revision||{},payload=r.payload_json||{};const edit=response.can_edit&&!S.workspaceHistorical&&response.year_id===activeYear()?.id;
  const submitAction=edit&&isStudent()&&response.student_user_id===S.user.id&&hasPermission('application.submit')&&itemEditable(response)?`<button type="button" class="btn primary sm" data-action="v2-submit-item" data-id="${esc(id)}">${icon('upload')}提交此小项</button>`:'';
  target.innerHTML=`<div class="comparison-header"><h3>${esc(response.title||'小项对照核对')}</h3><span class="row gap6">${statusTag(itemState(response))}${response.student_modified_score?'<span class="tag purple">学生手动修改过计分</span>':''}</span><button type="button" class="btn sm comparison-close" data-action="v2-close-comparison" aria-label="关闭对照">${icon('close')}关闭对照</button></div><div class="comparison-columns"><section class="comparison-materials"><div class="comparison-column-title"><span>${icon('file')}证明材料与证据</span>${edit?`<button class="btn sm" data-action="v2-supplement-item" data-id="${esc(id)}">${icon('plus')}补充附件</button>`:''}</div><div class="comparison-material-body">${edit?`<div class="material-analysis-tools"><button class="btn sm" data-action="v2-analyze-item" data-id="${esc(id)}" data-force="true" ${itemTask(response).busy?'disabled':''}>${icon('spark')}重新分析</button><button class="text-action" data-action="v2-ocr-item" data-id="${esc(id)}">重试 OCR</button></div>`:''}<div id="workspace-processing-notice">${processingNotice(response)}</div>${ocrPolicyNotice()}<div id="workspace-policy-results"></div><div id="workspace-evidence-results">${workspaceEvidenceHtml(response.crops||[],edit)}</div><div class="source-file-list">${workspaceFilesHtml(response.files||[],edit)}</div></div></section><section class="comparison-information"><div class="comparison-column-title"><span>${icon('edit')}加分信息与审核</span><span class="small muted">${edit?'可核对修改':'已提交内容'}</span></div><div class="comparison-information-body"><form id="workspace-item-form" class="form-grid"><div class="field span2"><label for="workspace-title">具体活动与条目名称</label>${edit?`<input id="workspace-title" name="title" maxlength="255" required value="${esc(r.title||response.title||'')}" placeholder="应包含具体活动名称，不使用泛称">`:`<div class="read-value">${esc(r.title||response.title)}</div>`}</div><div class="field"><label for="workspace-score">申报分值</label>${edit?`<input id="workspace-score" name="requested_score" type="number" min="0" step="0.01" required value="${esc(r.requested_score??response.requested_score)}">`:`<div class="read-value score-num big">${fmt(r.requested_score??response.requested_score)} 分</div>`}<div id="workspace-score-resolution">${workspaceScoreStatusHtml(response,edit)}</div></div>${['RESEARCH','ARTS','POST'].includes(response.category_code)?resultLevelField(payload,edit,response.category_code,response.result_label)+activityMatchField(response,edit):''}<div class="field span2"><label for="workspace-note">备注</label>${edit?`<textarea id="workspace-note" name="note" maxlength="4000">${esc(payload.note||'')}</textarea>`:`<div class="read-value">${esc(payload.note||'暂无')}</div>`}</div></form><div class="comparison-actions">${edit?`<button class="btn primary sm" data-action="v2-save-item">${icon('check')}保存此小项</button>`:''}${response.can_withdraw&&!S.workspaceHistorical?`<button class="btn sm" data-action="v2-withdraw-item" data-id="${esc(id)}">撤回此小项</button>`:''}${reviewButtonsHtml(response)}<button type="button" class="btn sm comparison-close" data-action="v2-close-comparison">${icon('back')}返回列表</button></div><div id="workspace-analysis-suggestion"></div><div class="divider"></div><h3 class="mb16">处理记录</h3>${historyHtml(response)}</div></section></div>`;
  $('#workspace-policy-results').innerHTML=itemPolicySummary(response);
  setupActivityMatchSearch(response,edit);
  if(submitAction){const save=$('[data-action="v2-save-item"]',target);save.classList.remove('primary');save.insertAdjacentHTML('afterend',submitAction);}
  if(edit){$('#workspace-item-form').onsubmit=e=>e.preventDefault();const changed=e=>{S.workspaceDirty=true;S.workspaceFormEdits++;if(e.target?.name==='requested_score')S.workspaceScoreTouched=true;refreshWorkspaceScoreStatus();};$('#workspace-item-form').oninput=changed;$('#workspace-item-form').onchange=changed;}
  const last=S.jobs.find(j=>j.application_id===id&&j.kind==='AI'&&['SUCCEEDED','STALE'].includes(j.state));if(last)showWorkspaceSuggestion(last);else if(response.latest_analysis?.result_json?.suggestion)showWorkspaceSuggestion({result_json:{...response.latest_analysis.result_json,analysis_id:response.latest_analysis.id,automatically_applied:r.ai_analysis_id===response.latest_analysis.id}});
  refreshProcessingStatus();
}
function workspaceFilesHtml(files,edit){
  return files.map(f=>`<article class="workspace-source-file" data-source-file="${esc(f.id)}"><div class="row between"><strong class="small">${esc(f.original_name)}</strong>${f.url?`<a class="text-action" href="${urlSafe(f.url)}" target="_blank" rel="noopener">查看原件</a>`:''}</div>${filePolicyHtml(f)}<div class="source-pages">${(f.pages||[]).map(p=>`<button class="source-page" data-action="v2-crop-page" data-page="${esc(p.id)}"><img loading="lazy" src="${urlSafe(p.url||p.image_url)}" alt="${esc(f.original_name)} 第${Number(p.page_index)||1}页"><span>第 ${Number(p.page_index)||1} 页${edit?' · 框选 / 调整证据':' · 查看原页'}</span></button>`).join('')}</div></article>`).join('')||'<p class="small muted">尚无证明材料。</p>';
}
function participationEvidenceLabel(crop){if(crop.evidence_type!=='PARTICIPATION')return '';const outcome=String(crop.outcome_text||'').slice(0,80);return crop.participation_role==='OUTCOME'?' · 本人奖次 / 考评'+(outcome?' · '+outcome:''):outcome?' · 本人身份及奖次 / 考评 · '+outcome:' · 本人身份';}
function workspaceEvidenceHtml(crops,edit){
  crops=policyVisibleCrops(crops,edit);
  const types=[['PARTICIPATION','本人参与证明'],['VALIDITY','有效性证据']];
  return types.map(([kind,label])=>{const list=crops.filter(c=>c.evidence_type===kind);return `<section class="workspace-evidence"><div class="row between"><h4>${label}</h4><span class="small muted">${kind==='PARTICIPATION'?'身份与奖次 / 考评 · 原页整宽 · 最多两张':'仅活动名称 / 时间 / 印章'}</span></div>${list.length?list.map(c=>`<figure class="context-evidence"><a href="${urlSafe(c.url||c.private_url)}" target="_blank" rel="noopener"><img loading="lazy" src="${urlSafe(c.url||c.private_url)}" alt="${label}，${esc(c.file_name||'证明文件')}第${Number(c.page_number)||1}页"></a><figcaption><span>${esc(c.file_name||'证明文件')} · 第 ${Number(c.page_number)||1} 页${esc(participationEvidenceLabel(c))}${c.validity_kind?' · '+esc(({NAME:'活动名称',TIME:'活动时间',SEAL:'印章'})[c.validity_kind]||c.validity_kind):''}</span><button class="text-action" data-action="v2-evidence-source" data-page="${esc(c.page_id)}" data-crop="${esc(c.id)}">定位原页</button>${edit?`<button class="text-action" data-action="v2-crop-page" data-page="${esc(c.page_id)}">调整截图</button>`:''}</figcaption>${(c.other_matches||[]).length?`<div class="evidence-other-matches">其他匹配：${c.other_matches.map(m=>`<button class="text-action" data-action="v2-evidence-source" data-page="${esc(m.page_id)}">${esc(m.file_name||'证明文件')}第${Number(m.page_number)||1}页 · ${esc(({NAME:'姓名',CLASS:'班级',STUDENT_NUMBER:'学号',OUTCOME:'奖次 / 考评'})[m.field]||m.field||'参与信息')}</button>`).join('、')}</div>`:''}</figure>`).join(''):`<div class="evidence-empty">${kind==='PARTICIPATION'?'尚未定位本人参与信息，可查看原页并手动框选。':'未找到具体活动名称、活动时间或印章时，请人工核对原件并补充证据。'}</div>`}</section>`;}).join('');
}
async function saveWorkspaceItem({confirmScore=false}={}){
  const form=$('#workspace-item-form'),item=S.workspaceItem;if(!form||!item)return;if(!form.reportValidity())throw new Error('请完善此小项的名称和分值。');
  const id=item.id,owner=S.user?.id,expectedVersion=S.workspaceFormVersion??item.lock_version,editVersion=S.workspaceFormEdits;
  if(expectedVersion===undefined||expectedVersion===null)throw new Error('未取得当前小项版本，请关闭后重新打开再保存。');
  const payload={...(item.revision?.payload_json||{})};
  for(const key of ['result_label','note'])if(form.elements[key])payload[key]=form.elements[key].value;
  const body={expected_version:expectedVersion,title:form.elements.title.value.trim(),requested_score:form.elements.requested_score.value,...(form.elements.activity_id?{activity_id:form.elements.activity_id.value||null}:{}),payload_json:payload,score_confirmed:true};
  if(confirmScore)S.workspaceDirty=true;
  let updated;
  try{updated=await api(`/applications/${id}`,{method:'PUT',body});}
  catch(error){if(error.status===409)error.message+=' 未保存输入已保留，请关闭后重新打开此小项，核对最新内容再保存。';throw error;}
  if(S.user?.id===owner&&S.workspaceItem?.id===id&&form===$('#workspace-item-form')){
    S.workspaceItem=updated;S.detail=updated;
    if(S.workspaceFormEdits===editVersion){S.workspaceDirty=false;S.workspaceScoreTouched=false;adoptWorkspaceFormBasis(updated);}
    else S.workspaceDirty=true;
  }
  return updated;
}
function fileStructureKey(file){return JSON.stringify([file?.id,file?.original_name,file?.url,(file?.pages||[]).map(p=>[p.id,p.page_index,p.url||p.image_url])]);}
async function refreshWorkspaceMaterials({syncForm=false}={}){
  const id=S.selectedItem,container=$('.comparison-material-body'),form=$('#workspace-item-form'),request=S.comparisonRequest;if(!id||!container)return;
  const previous=S.workspaceItem,detail=await api(`/applications/${id}`);
  if(detail.id!==S.selectedItem||request!==S.comparisonRequest||!container.isConnected||form!==$('#workspace-item-form'))return;
  S.detail=detail;S.workspaceItem=detail;
  const edit=detail.can_edit&&!S.workspaceHistorical,files=$('.source-file-list',container),currentFiles=detail.files||[];
  if(files){
    for(const article of $$('[data-source-file]',files))if(!currentFiles.some(f=>f.id===article.dataset.sourceFile))article.remove();
    for(const file of currentFiles){const article=$$('[data-source-file]',files).find(el=>el.dataset.sourceFile===file.id),old=(previous?.files||[]).find(f=>f.id===file.id);
      if(!article)files.insertAdjacentHTML('beforeend',workspaceFilesHtml([file],edit));
      else if(fileStructureKey(old)!==fileStructureKey(file)){const holder=document.createElement('div');holder.innerHTML=workspaceFilesHtml([file],edit);article.replaceWith(holder.firstElementChild);}
    }
  }
  patchHtml($('#workspace-policy-results',container),itemPolicySummary(detail));
  if(JSON.stringify(previous?.crops||[])!==JSON.stringify(detail.crops||[]))patchHtml($('#workspace-evidence-results',container),workspaceEvidenceHtml(detail.crops||[],edit));
  if(syncForm&&!S.workspaceDirty&&form){const revision=detail.revision||{},payload=revision.payload_json||{};for(const name of ['title','requested_score','activity_id','result_label','note']){const field=form.elements[name];if(!field)continue;const value=name==='result_label'?resultLevelText(payload,detail.category_code,detail.result_label):['title','requested_score','activity_id'].includes(name)?revision[name]:payload[name];syncPolicyFactField(field,value);}syncActivityMatchSelection(detail);adoptWorkspaceFormBasis(detail);}else if(S.workspaceFormBasis===workspaceFormBasisKey(detail)){adoptWorkspaceFormBasis(detail);}
  const title=$('.comparison-header h3');if(title&&title.textContent!==(detail.title||'小项对照核对'))title.textContent=detail.title||'小项对照核对';
  const last=itemJobs(detail,'AI').find(j=>['SUCCEEDED','STALE'].includes(j.state));if(last)showWorkspaceSuggestion(last);
  refreshWorkspaceScoreStatus();
  refreshProcessingStatus();
}

async function enqueueItem(id,kind='AI',force=false){
  const result=await api(`/applications/${id}/jobs`,{method:'POST',body:{kind,force}});
  if(result.job){S.jobs=[result.job,...S.jobs.filter(j=>j.id!==result.job.id)];drawJobs();}
  toast(kind==='OCR'?'OCR 已加入后台队列，可切换页面继续操作。':'AI 分析已加入后台队列，已有人工修改时仅生成建议。');await pollJobs(true);return result;
}
async function bulkAnalyze(button,submissionId=null){
  await buttonTask(button,async()=>{const saved=S.workspaceDirty;if(saved)await saveWorkspaceItem();const body={kind:'AI'};if(submissionId)body.submission_id=submissionId;const result=await api('/analysis-jobs/bulk',{method:'POST',body});toast(`已安排 ${result.jobs?.length||0} 项后台分析${result.skipped?.length?`，跳过 ${result.skipped.length} 项已分析或锁定项`:''}。${(result.jobs||[]).some(waitingForOcr)?'原OCR继续执行，AI等待其完成。':''}人工稿仅生成建议，不覆盖已填写信息。`);const update=await pollJobs();if(saved&&!update?.refreshedWorkspace)await refreshWorkspaceData({fromPoll:true});});
}
function showWorkspaceSuggestion(job){
  const out=$('#workspace-analysis-suggestion');if(!out)return;const result=job.result_json||{};const suggestion=result.suggestion||result.analysis?.result_json?.suggestion;const analysisId=result.analysis_id||result.analysis?.id;
  const resolution=result.score_resolution||suggestion?.score_resolution;
  if(resolution){
    const current=currentScoreResolution(S.workspaceItem||{}),retained=S.workspaceItem?.revision?.requested_score??S.workspaceItem?.requested_score??resolution.retained_value??0,pending=unresolvedScore(resolution),manual=current.status==='MANUAL';
    if(result.auto_applied||result.automatically_applied){
      out.innerHTML=manual?'<p class="small muted mt16">当前已核定分值保留，自动分析不会覆盖。</p>':pending?'<div class="notice warning mt16">'+icon('info')+'<span><strong>条目已提取，分值待核定。</strong><br>'+esc(scoreResolutionMessage(resolution))+'</span></div>':'<div class="notice success mt16">'+icon('check')+'<span>已生成计分建议，请核对活动、分值与证据；自动计分不代表审核通过。</span></div>';
      return;
    }
    if(!suggestion)return;
    out.innerHTML=`<div class="analysis-result mt16"><h3>后台分析建议</h3>${pending?'<div class="notice warning mt12"><strong>条目已提取，建议分值待核定。</strong><p>'+esc(scoreResolutionMessage(resolution))+'</p></div>':''}<p class="small muted mt8">${manual?'当前人工核定分值保留。':''}建议尚未应用，请核对后应用。</p><div class="selection-box mt12">${esc(suggestion.title||suggestion.suggested_title||'')}<br>${pending?'当前保留分值（自动计分未核定）':'建议分值'}：${fmt(pending?retained:resolution.value??suggestion.requested_score??suggestion.score??suggestion.suggested_score)} 分</div>${analysisId&&S.workspaceItem?.can_edit?`<button class="btn sm primary mt12" id="workspace-apply-suggestion">${pending?'应用已识别信息（不核定分值）':'应用此建议'}</button>`:''}</div>`;
    if($('#workspace-apply-suggestion'))$('#workspace-apply-suggestion').onclick=e=>buttonTask(e.currentTarget,async()=>{if(S.workspaceDirty)throw new Error('当前有未保存修改，请先保存后再核对此建议。');await api(`/applications/${S.workspaceItem.id}/apply-analysis`,{method:'POST',body:{analysis_id:analysisId}});toast('识别信息已应用，请继续核对分值与证据。');await refreshWorkspaceData();});
    return;
  }
  const scoreRequiresReview=result.needs_review===true||suggestion?.score_requires_confirmation===true,retainedScore=S.workspaceItem?.revision?.requested_score??S.workspaceItem?.requested_score??suggestion?.requested_score??suggestion?.score??suggestion?.suggested_score;
  const titleFallback=result.title_resolution?.status==='GROUNDED_REVIEW',reviewNotice=titleFallback?'已补全具体活动名，分值保留当前稿；请核对本人参与、次数和分值。':'已提取可识别信息，分值保留当前稿；请核对本人等级、考评和计分。';
  if(result.auto_applied||result.automatically_applied){out.innerHTML=scoreRequiresReview?'<div class="notice warning mt16">'+icon('info')+'<span><strong>'+reviewNotice+'</strong></span></div>':'<div class="notice success mt16">'+icon('check')+'<span>首次分析已填入本小项，请核对具体活动、分值与证据。</span></div>';return;}
  if(!suggestion)return;
  out.innerHTML=`<div class="analysis-result mt16"><h3>后台分析建议</h3>${scoreRequiresReview?'<div class="notice warning mt12"><strong>'+reviewNotice+'</strong></div>':''}<p class="small muted mt8">建议尚未应用，请核对后应用。</p><div class="selection-box mt12">${esc(suggestion.title||suggestion.suggested_title||'')}<br>${scoreRequiresReview?'当前保留分值（需核对）':'建议分值'}：${fmt(scoreRequiresReview?retainedScore:suggestion.requested_score??suggestion.score??suggestion.suggested_score)} 分</div>${analysisId&&S.workspaceItem?.can_edit?`<button class="btn sm primary mt12" id="workspace-apply-suggestion">应用此建议</button>`:''}</div>`;
  if($('#workspace-apply-suggestion'))$('#workspace-apply-suggestion').onclick=e=>buttonTask(e.currentTarget,async()=>{if(S.workspaceDirty)throw new Error('当前有未保存修改。请先保存，重新分析后再核对此建议，避免覆盖新稿。');await api(`/applications/${S.workspaceItem.id}/apply-analysis`,{method:'POST',body:{analysis_id:analysisId}});toast('建议已应用，请继续核对。');await refreshWorkspaceData();});
}
const workspaceSubmissions=new Set(),workspaceBatchSubmissions=new Set();
async function submitWorkspaceItem(id){
  if(workspaceSubmissions.has(id))return;
  workspaceSubmissions.add(id);let submitted=false;
  try{
    let detail=S.workspaceItem;const owner=S.user?.id;
    if(!detail||detail.id!==id||S.selectedItem!==id||!detail.can_edit||!isStudent()||detail.student_user_id!==owner||!hasPermission('application.submit')||S.workspaceHistorical||detail.year_id!==activeYear()?.id||!itemEditable(detail))throw new Error('当前小项不可提交。');
    const form=$('#workspace-item-form');if(!form||!form.reportValidity())throw new Error('请填写条目名称和有效分值。');
    if(S.workspaceDirty){detail=await saveWorkspaceItem();if(S.workspaceDirty)throw new Error('保存期间内容有新修改，请保存后再提交。');}
    if(S.user?.id!==owner||S.selectedItem!==id||!form.isConnected)return;
    const version=S.workspaceFormVersion??detail.lock_version;if(version===undefined||version===null)throw new Error('未取得小项版本，请重新打开后提交。');
    await api(`/applications/${id}/submit`,{method:'POST',body:{expected_version:version,score_confirmed:true}});submitted=true;
    if(S.user?.id!==owner)return;S.checkedItems.delete(id);toast('已提交，等待审核。');await refreshWorkspaceData();
  }catch(error){if(submitted)toast('已提交，请刷新查看最新状态。',true);else throw error;}
  finally{workspaceSubmissions.delete(id);}
}
async function submitSelected(groupId){
  if(workspaceBatchSubmissions.has(groupId))return;
  workspaceBatchSubmissions.add(groupId);let submitted=false;
  try{
    const owner=S.user?.id,group=S.submissions.find(g=>g.id===groupId);
    let selected=groupItems(group).filter(item=>itemEditable(item)&&S.checkedItems.has(item.id));
    if(!selected.length){toast('请先勾选要提交的小项。',true);return;}
    if(S.workspaceDirty&&selected.some(item=>item.id===S.workspaceItem?.id)){
      const saved=await saveWorkspaceItem();if(S.workspaceDirty)throw new Error('保存期间内容有新修改，请保存后再提交。');
      selected=selected.map(item=>item.id===saved.id?saved:item);
    }
    if(S.user?.id!==owner)return;
    if(selected.some(item=>item.lock_version===undefined||item.lock_version===null))throw new Error('未取得小项版本，请刷新后提交。');
    const expected_versions=Object.fromEntries(selected.map(item=>[item.id,item.lock_version]));
    const response=await api(`/submissions/${groupId}/submit`,{method:'POST',body:{item_ids:selected.map(item=>item.id),score_confirmed:true,expected_versions}});submitted=true;
    if(S.user?.id!==owner)return;
    const results=response.results||[],passed=results.filter(item=>item.ok),failed=results.filter(item=>!item.ok);
    passed.forEach(item=>S.checkedItems.delete(item.id));
    const failures=failed.map(item=>`${selected.find(value=>value.id===item.id)?.title||'小项'}：${typeof item.detail==='string'?item.detail:item.detail?.message||'提交失败'}`).join('；');
    toast(failed.length?`已提交 ${passed.length} 项，${failed.length} 项未提交。${failures}`:`已提交 ${passed.length} 个小项。`,Boolean(failed.length));
    await refreshWorkspaceData();
  }catch(error){if(submitted)toast('提交结果已返回，请刷新查看最新状态。',true);else throw error;}
  finally{workspaceBatchSubmissions.delete(groupId);}
}
function deleteItemModal(groupId,id){
  showModal('删除此加分小项','<p class="small muted">删除后不再展示在当前申报表格。原件、内容版本和审核历史仍会保留，已通过或待审小项不能直接删除。</p>',`<button class="btn" data-action="close-modal">取消</button><button class="btn danger" id="confirm-delete-item">删除此项</button>`);
  $('#confirm-delete-item').onclick=e=>buttonTask(e.currentTarget,async()=>{try{await api(`/submissions/${groupId}/items/${id}`,{method:'DELETE'});closeModal();S.checkedItems.delete(id);if(S.selectedItem===id)S.selectedItem=null;S.workspaceDirty=false;S.workspaceScoreTouched=false;toast('小项已删除，历史记录保留。');await refreshWorkspaceData();}catch(err){modalError(err.message);}});
}
function supplementItemModal(id){
  showModal('为此小项补充证明附件','<p class="small muted">附件归入当前小项，不创建其它小项。确认后按每份文件的页数安排识别。</p>'+ocrPolicyNotice()+'<div class="field mt16"><label for="supplement-files">补充附件</label><input id="supplement-files" type="file" accept=".pdf,.png,.jpg,.jpeg" multiple></div>',`<button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="supplement-confirm">确认补传</button>`);
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
  const crop=(S.detail.crops||[]).find(c=>c.id===cropId);if(crop&&$('#crop-kind')){$('#crop-kind').value=crop.evidence_type;$('#crop-kind').dispatchEvent(new Event('change'));if(crop.validity_kind&&$('#crop-validity-kind'))$('#crop-validity-kind').value=crop.validity_kind;if($('#crop-participation-role')){$('#crop-participation-role').value=crop.participation_role||'PRIMARY';$('#crop-participation-role').dispatchEvent(new Event('change'));}}
}

async function statisticsYearModal(){
  const data=await api('/admin/statistics-year');
  showModal('设置当前统计学年',`<div class="notice">${icon('calendar')}<span>学生只在管理员指定的当前学年新增、修改和正式提交；旧学年记录保留只读，原待审仍可继续审核。</span></div><form id="statistics-year-form" class="mt16"><div class="field"><label for="statistics-year-select">当前统计学年</label><select id="statistics-year-select">${options(data.years||S.boot.years,data.active_year?.id||activeYear()?.id)}</select></div></form><div class="divider"></div><h3>新建学年（可选）</h3><div class="form-grid mt12"><div class="field"><label for="academic-year-code">学年编码</label><input id="academic-year-code" placeholder="例如：2027-2028" pattern="[0-9]{4}-[0-9]{4}"></div><div class="field"><label for="academic-year-name">显示名称</label><input id="academic-year-name" placeholder="例如：2027-2028学年"></div></div><button class="btn sm mt12" id="create-academic-year">新建学年</button>`,`<button class="btn" data-action="close-modal">取消</button><button class="btn primary" id="save-statistics-year">保存当前学年</button>`);
  $('#save-statistics-year').onclick=e=>buttonTask(e.currentTarget,async()=>{try{await api('/admin/statistics-year',{method:'PUT',body:{year_id:$('#statistics-year-select').value}});closeModal();await loadBootstrap();renderShell();await go('admin');toast('当前统计学年已更新，历史归属保留。');}catch(err){modalError(err.message);}});
  $('#create-academic-year').onclick=e=>buttonTask(e.currentTarget,async()=>{const code=$('#academic-year-code').value.trim();if(!/^\d{4}-\d{4}$/.test(code)){modalError('学年编码格式为 YYYY-YYYY。');return;}try{await api('/admin/academic-years',{method:'POST',body:{code,name:$('#academic-year-name').value.trim()||code+'学年'}});await loadBootstrap();await statisticsYearModal();toast('新学年已创建，可选择为当前统计学年。');}catch(err){modalError(err.message);}});
}

function cropModal(pageId){
  const detail=S.detail,file=(detail?.files||[]).find(f=>(f.pages||[]).some(p=>p.id===pageId));if(!file)return;
  const pages=[...(file.pages||[])].sort((a,b)=>Number(a.page_index)-Number(b.page_index));let position=pages.findIndex(p=>p.id===pageId);if(position<0)return;
  const canEdit=detail.can_edit===true&&detail.year_id===activeYear()?.id,applicationId=detail.id,drafts=new Map();
  showModal(canEdit?'原页证据定位与调整':'证明原页',`<p class="muted small crop-file-name">${esc(file.original_name)}</p><div class="crop-page-nav"><button type="button" class="btn sm" id="crop-page-prev" aria-label="上一页" title="上一页">${icon('back')}上一页</button><span id="crop-page-label" role="status" aria-live="polite"></span><button type="button" class="btn sm" id="crop-page-next" aria-label="下一页" title="下一页">下一页${icon('arrow')}</button></div>${canEdit?`<p class="crop-draft-status" id="crop-draft-status">切页保留本窗口各页草稿；保存只提交当前页。</p><div class="crop-controls"><select id="crop-kind" aria-label="证据类别"><option value="PARTICIPATION">本人参与证明</option><option value="VALIDITY">有效性证据</option></select><select id="crop-participation-role" aria-label="本人证明内容"><option value="PRIMARY">本人身份</option><option value="OUTCOME">本人奖次 / 考评</option></select><select id="crop-validity-kind" class="hidden" aria-label="有效性证据子类"><option value="NAME">具体活动名称</option><option value="TIME">活动时间</option><option value="SEAL">印章</option></select><button class="btn sm primary" id="crop-select-mode">${icon('crop')}定位截图</button><button class="btn sm" id="crop-highlight-mode">红框标注</button><select id="crop-highlight-field" aria-label="红框信息类型"><option value="STUDENT_NUMBER">学号</option><option value="NAME">姓名</option><option value="CLASS">班级</option><option value="OUTCOME">奖次 / 考评结果</option></select><button class="btn sm" id="crop-mask-mode">${icon('shield')}额外遮挡</button><button class="btn sm" id="crop-reset">清除选区</button></div>`:''}<div class="crop-canvas-wrap"><canvas id="crop-canvas" aria-label="证明原页，可拖动定位证据"></canvas></div><p class="crop-helper" id="crop-mode-help">${canEdit?'参与证明保留原页整宽及四分之一页高。拖动确定上下文区域，可用红框标注学号、姓名或班级。':'原件仅向授权用户开放。证据截图显示其对应原件页码，不拼接不同页面。'}</p><div class="crop-preview-label" id="crop-coordinates">正在加载原件页…</div>`,`<button class="btn" data-action="close-modal">关闭</button>${canEdit?'<button class="btn primary" id="crop-save" disabled>保存当前页证据</button>':''}`,true);
  const canvas=$('#crop-canvas'),ctx=canvas.getContext('2d'),wrap=$('.crop-canvas-wrap'),controls=$$('.crop-controls button,.crop-controls select');let image=null,box=null,masks=[],highlights=[],start=null,drag=null,mode='box',ready=false,saving=false,dirty=false,saved=false,request=0;
  const point=e=>{const bounds=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(canvas.width,(e.clientX-bounds.left)*canvas.width/bounds.width)),y:Math.max(0,Math.min(canvas.height,(e.clientY-bounds.top)*canvas.height/bounds.height))};};
  const rect=(a,b)=>[Math.round(Math.min(a.x,b.x)),Math.round(Math.min(a.y,b.y)),Math.round(Math.abs(a.x-b.x)),Math.round(Math.abs(a.y-b.y))];
  const normalized=b=>{if($('#crop-kind')?.value!=='PARTICIPATION')return b;const height=Math.round(canvas.height/4),middle=b[1]+b[3]/2;return [0,Math.max(0,Math.min(canvas.height-height,Math.round(middle-height/2))),canvas.width,height];};
  function paint(){ctx.clearRect(0,0,canvas.width,canvas.height);if(!ready||!image)return;ctx.drawImage(image,0,0);if(box){ctx.fillStyle='#102a4340';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(image,...box,...box);ctx.strokeStyle='#168b82';ctx.lineWidth=Math.max(2,canvas.width/450);ctx.strokeRect(...box);}for(const mask of masks){ctx.fillStyle='#173c5790';ctx.fillRect(...mask);}for(const h of highlights){ctx.strokeStyle='#e13e3e';ctx.lineWidth=Math.max(2,canvas.width/400);ctx.strokeRect(...h.box);}if(drag&&start){const b=mode==='box'?normalized(rect(start,drag)):rect(start,drag);ctx.strokeStyle=mode==='highlight'?'#e13e3e':mode==='mask'?'#375c78':'#168b82';ctx.lineWidth=Math.max(2,canvas.width/450);ctx.strokeRect(...b);}}
  function pendingPages(){return [...drafts].filter(([id,state])=>id!==pages[position].id&&state.dirty).length+(dirty?1:0);}
  function updateNavigation(){
    $('#crop-page-label').textContent=`原件第 ${Number(pages[position].page_index)||position+1} 页 / 共 ${pages.length} 页`;
    $('#crop-page-prev').disabled=saving||position===0;$('#crop-page-next').disabled=saving||position===pages.length-1;
    controls.forEach(control=>control.disabled=saving||!ready);
    if($('#crop-save'))$('#crop-save').disabled=saving||!ready||!box||!dirty;
    if($('#crop-draft-status'))$('#crop-draft-status').textContent=`${saved&&!dirty?'本页已保存。':''}${pendingPages()?`还有 ${pendingPages()} 页草稿未保存。`:''}切页保留本窗口各页草稿；保存只提交当前页。`;
  }
  function updateText(){updateNavigation();$('#crop-coordinates').textContent=box?`截图 ${box[2]} × ${box[3]} 像素 · ${highlights.length} 处红框 · ${masks.length} 处额外遮挡${saved&&!dirty?' · 本页已保存':''}`:canEdit?'请在原页上拖动定位截图区域。':'可使用左右箭头查看同一附件的其它页。';}
  function stash(){drafts.set(pages[position].id,{box:box?.slice()||null,masks:masks.map(b=>b.slice()),highlights:highlights.map(h=>({...h,box:h.box.slice()})),mode,dirty,saved,kind:$('#crop-kind')?.value||'PARTICIPATION',participationRole:$('#crop-participation-role')?.value||'PRIMARY',validity:$('#crop-validity-kind')?.value||'NAME',highlightField:$('#crop-highlight-field')?.value||'STUDENT_NUMBER',scrollTop:wrap.scrollTop});}
  function loadPage(index){
    if(index<0||index>=pages.length||saving)return;$('.modal-error')?.remove();position=index;const page=pages[position],state=drafts.get(page.id)||{},token=++request;
    box=state.box?.slice()||null;masks=(state.masks||[]).map(b=>b.slice());highlights=(state.highlights||[]).map(h=>({...h,box:h.box.slice()}));mode=state.mode||'box';dirty=Boolean(state.dirty);saved=Boolean(state.saved);start=null;drag=null;ready=false;image=null;
    if(canEdit){$('#crop-kind').value=state.kind||'PARTICIPATION';$('#crop-participation-role').value=state.participationRole||'PRIMARY';$('#crop-participation-role').classList.toggle('hidden',$('#crop-kind').value!=='PARTICIPATION');$('#crop-validity-kind').value=state.validity||'NAME';$('#crop-highlight-field').value=state.highlightField||'STUDENT_NUMBER';$('#crop-validity-kind').classList.toggle('hidden',$('#crop-kind').value!=='VALIDITY');changeMode(mode);updateHelper();}
    paint();updateNavigation();canvas.setAttribute('aria-busy','true');$('#crop-coordinates').textContent='正在加载原件页…';
    const incoming=new Image();incoming.onload=()=>{if(token!==request||!canvas.isConnected)return;image=incoming;ready=true;canvas.width=incoming.naturalWidth;canvas.height=incoming.naturalHeight;canvas.setAttribute('aria-busy','false');paint();updateText();wrap.scrollTop=state.scrollTop||0;};
    incoming.onerror=()=>{if(token!==request||!canvas.isConnected)return;canvas.setAttribute('aria-busy','false');$('#crop-coordinates').textContent='此原件页加载失败，可切换其它页后再试。';updateNavigation();};incoming.src=page.url||page.image_url;
  }
  function switchPage(offset){if(saving)return;stash();loadPage(position+offset);}
  $('#crop-page-prev').onclick=()=>switchPage(-1);$('#crop-page-next').onclick=()=>switchPage(1);
  S.cropCloseGuard=()=>{if(!canvas.isConnected)return true;if(saving){toast('正在保存当前页证据，请稍候。');return false;}const pending=pendingPages();return !pending||window.confirm(`还有 ${pending} 页框选草稿未保存。关闭将放弃这些草稿，是否继续？`);};
  loadPage(position);
  if(!canEdit)return;
  canvas.onpointerdown=e=>{if(!ready||saving)return;start=point(e);drag=start;canvas.setPointerCapture(e.pointerId);e.preventDefault();};canvas.onpointermove=e=>{if(start){drag=point(e);paint();}};canvas.onpointerup=e=>{if(!start)return;const b=rect(start,point(e));if(b[2]>=3&&b[3]>=3){if(mode==='mask')masks.push(b);else if(mode==='highlight')highlights.push({box:b,field:$('#crop-highlight-field').value});else box=normalized(b);dirty=true;}start=null;drag=null;paint();updateText();};canvas.onpointercancel=()=>{start=null;drag=null;paint();};
  function changeMode(next){mode=next;[['#crop-select-mode','box'],['#crop-highlight-mode','highlight'],['#crop-mask-mode','mask']].forEach(([id,key])=>$(id).classList.toggle('primary',next===key));}
  function updateHelper(){$('#crop-mode-help').textContent=$('#crop-kind').value==='PARTICIPATION'?($('#crop-participation-role').value==='OUTCOME'?'奖次或考评补充图同样保留原页整宽和四分之一页高，请框住本人对应的结果，并用红框标注奖次或考评词。':'本人身份图保留原页整宽和四分之一页高；能同时看到奖次或考评时，可在这张图一并标红。'):'仅截取具体活动名称、活动时间或印章。不把“特此证明”等套话当作有效性证据。';}
  $('#crop-select-mode').onclick=()=>changeMode('box');$('#crop-highlight-mode').onclick=()=>changeMode('highlight');$('#crop-mask-mode').onclick=()=>changeMode('mask');$('#crop-reset').onclick=()=>{box=null;masks=[];highlights=[];dirty=false;saved=false;paint();updateText();};
  $('#crop-kind').onchange=e=>{$('#crop-validity-kind').classList.toggle('hidden',e.target.value!=='VALIDITY');$('#crop-participation-role').classList.toggle('hidden',e.target.value!=='PARTICIPATION');updateHelper();if(box){box=normalized(box);dirty=true;paint();updateText();}};
  $('#crop-validity-kind').onchange=()=>{if(box){dirty=true;updateText();}};
  $('#crop-participation-role').onchange=()=>{updateHelper();if(box){dirty=true;updateText();}};
  $('#crop-save').onclick=async e=>{
    if(saving||!ready||!box||!dirty)return;$('.modal-error')?.remove();const button=e.currentTarget,old=button.innerHTML,currentPage=pages[position],payload={page_id:currentPage.id,evidence_type:$('#crop-kind').value,box:box.slice(),masks:masks.map(b=>b.slice()),highlights:highlights.map(h=>({...h,box:h.box.slice()}))};
    if(payload.evidence_type==='VALIDITY')payload.validity_kind=$('#crop-validity-kind').value;else payload.participation_role=$('#crop-participation-role').value;saving=true;updateNavigation();button.innerHTML='<span class="tiny-spinner"></span> 保存中';
    try{await api(`/applications/${applicationId}/crops`,{method:'POST',body:payload});dirty=false;saved=true;stash();toast(`原件第 ${Number(currentPage.page_index)||position+1} 页证据已保存；其它页草稿需分别保存。`);if($('#submission-groups')){await refreshWorkspaceMaterials();await refreshWorkspaceData({fromPoll:true});}else await refresh();}
    catch(error){if(canvas.isConnected)modalError(error.message);else toast(error.message,true);}
    finally{saving=false;if(canvas.isConnected){button.innerHTML=old;updateText();}}
  };
}

init();
