/** OCR-budget UI regression. Every /api request is mocked; no business writes or cloud calls. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const user={id:'policy-student',username:'policy-student',display_name:'政策界面验收同学',base_identity:'STUDENT',roles:[{code:'STUDENT',class_id:null}]};
const year={id:'policy-year',code:'2025-2026',name:'2025-2026学年'};
const category={id:'policy-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'};
const svg='<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300"><rect width="480" height="300" fill="#edf4f6"/><rect x="30" y="90" width="420" height="50" fill="white" stroke="#cf6a65"/><text x="40" y="120" font-size="16" fill="#325570">Mock evidence — no personal data</text></svg>';
const file=(id,pages,extra={})=>({id,original_name:id+'.pdf',url:'/mock-assets/'+id+'.pdf',page_count:pages,mime_type:'application/pdf',pages:[1,Math.ceil(pages/2),pages].filter((n,i,a)=>a.indexOf(n)===i).map(n=>({id:id+'-page-'+n,page_index:n,width_px:1200,height_px:900,url:'/mock-assets/'+id+'-'+n+'.svg'})),...extra});
const long=file('长扫描材料',57,{cloud_ocr_eligible:false,native_text_status:'NONE',automatic_ocr_status:'MANUAL_REQUIRED',seal_status:'SKIPPED'});
const short=file('两页补充证明',2,{cloud_ocr_eligible:true,native_text_status:'NONE',automatic_ocr_status:'COMPLETED',seal_status:'NOT_FOUND',seal_pages:[1,2]});
const native=file('原生文字长材料',56,{cloud_ocr_eligible:false,native_text_status:'USED',text_layer_used:true,native_pages:Array.from({length:56},(_,i)=>i+1),automatic_ocr_status:'COMPLETED',seal_status:'SKIPPED'});
const partial=file('部分文字层材料',20,{cloud_ocr_eligible:false,native_text_status:'PARTIAL',text_layer_used:true,native_pages:[1,2,3],manual_page_numbers:Array.from({length:17},(_,i)=>i+4),automatic_ocr_status:'PARTIAL',seal_status:'SKIPPED'});
const shortMiddle=file('三页短材料',3,{cloud_ocr_eligible:true,automatic_ocr_status:'COMPLETED',seal_status:'NOT_FOUND',seal_pages:[1,3]});
const checking=file('234页待检查材料',234,{cloud_ocr_eligible:false,native_text_status:'PENDING',text_layer_check_complete:false,text_layer_checked_page_count:18,text_layer_used:true,native_pages:[1,2,3],automatic_ocr_status:'CHECKING_TEXT_LAYER',seal_status:'SKIPPED'});
const skip={file_id:long.id,file_name:long.original_name,page_count:57,max_pages:12,reason:'扫描件超过12页，不调用云OCR。',code:'FILE_TOO_LONG'};
const result=(files,extra={})=>({policy_version:'mock-native-first-v3',file_results:files.map(f=>({file_id:f.id,file_name:f.original_name,...f})),processed_files:files.filter(f=>f.id!==long.id),skipped_files:files.some(f=>f.id===long.id)?[skip]:[],per_file_warnings:[],...extra});
const crop=(id,f,number,extra={})=>({id,page_id:f.id+'-page-'+number,file_id:f.id,file_name:f.original_name,page_number:number,evidence_type:'PARTICIPATION',source:'AI',url:'/mock-assets/'+id+'.svg',automatic_policy_allowed:true,...extra});
const sources={
 scan:{title:'长扫描件手工处理',files:[long],crops:[],ocr_status:'MANUAL_REQUIRED',analysis_status:'UNANALYZED',state:'DRAFT',policy:result([long],{status:'MANUAL_REQUIRED',manual_required:true,message:'长扫描件跳过云OCR，请手动填写和框选。'})},
 mixed:{title:'长扫描件与短附件独立处理',files:[long,short],crops:[],ocr_status:'SUCCEEDED',analysis_status:'UNANALYZED',state:'DRAFT',policy:result([long,short],{status:'SUCCEEDED',partial:true})},
 native:{title:'长PDF本地文字层分析',files:[native,shortMiddle],ocr_status:'SUCCEEDED',analysis_status:'SUCCEEDED',state:'DRAFT',policy:result([native,shortMiddle],{status:'SUCCEEDED'}),crops:[crop('new-native-crop',native,28),crop('old-cloud-crop',native,28,{automatic_policy_allowed:false,policy_exclusion_reason:'旧云OCR候选不再自动展示。'}),crop('manual-long-seal',native,28,{source:'MANUAL',evidence_type:'VALIDITY',validity_kind:'SEAL'}),crop('old-middle-seal',shortMiddle,2,{evidence_type:'VALIDITY',validity_kind:'SEAL',automatic_policy_allowed:false}),crop('manual-middle-seal',shortMiddle,2,{source:'MANUAL',evidence_type:'VALIDITY',validity_kind:'SEAL'}),crop('old-long-seal',native,1,{evidence_type:'VALIDITY',validity_kind:'SEAL',automatic_policy_allowed:false})]},
 partial:{title:'部分页面存在文字层',files:[partial],crops:[],ocr_status:'SUCCEEDED',analysis_status:'UNANALYZED',state:'DRAFT',policy:result([partial],{status:'SUCCEEDED',partial:true})},
 frozen:{title:'已冻结历史证据保留',files:[long,shortMiddle],crops:[crop('frozen-long-ai',long,29,{automatic_policy_allowed:true}),crop('frozen-middle-ai',shortMiddle,2,{evidence_type:'VALIDITY',validity_kind:'SEAL',automatic_policy_allowed:true})],ocr_status:'SUCCEEDED',analysis_status:'SUCCEEDED',state:'APPROVED',policy:result([long,shortMiddle],{status:'SUCCEEDED'})},
 quota:{title:'免费额度用尽手工继续',files:[short],crops:[],ocr_status:'FAILED',analysis_status:'UNANALYZED',state:'DRAFT',policy:result([short],{status:'FAILED',stopped:true,stop_reason:'腾讯云免费额度不足，云识别已停止，请手动填写和框选。'})},
 checking:{title:'长PDF文字层检查进行中',files:[checking],crops:[],ocr_status:'RUNNING',analysis_status:'UNANALYZED',state:'DRAFT',policy:result([checking],{status:'RUNNING'})}
};
const jobs=Object.entries(sources).map(([id,s],i)=>({id:'job-'+id,application_id:id,owner_id:user.id,kind:'OCR',state:id==='quota'?'FAILED':id==='checking'?'RUNNING':'SUCCEEDED',requires_manual:['scan','mixed','partial'].includes(id),partial:['mixed','partial'].includes(id),stage:id==='scan'?'OCR失败':id==='quota'?'额度不足':id==='checking'?'正在检查已有文字层':'已完成',progress:1,total:1,created_at:`2026-10-25T10:0${i}:00Z`,result_json:s.policy,error_message:id==='scan'?'长文件不进行云OCR，需手动处理':id==='quota'?'免费额度不足':undefined}));
const item=id=>{const s=sources[id];return {id,title:s.title,category_id:category.id,category_name:category.name,category_code:category.code,requested_score:'0.30',state:s.state,student_name:user.display_name,student_user_id:user.id,class_id:'policy-class',class_name:'界面验收班',year_id:year.id,year_name:year.name,lock_version:1,file_count:s.files.length,files:s.files.map(f=>({id:f.id,original_name:f.original_name})),ocr_status:s.ocr_status,analysis_status:s.analysis_status,latest_job:jobs.find(j=>j.application_id===id)};};
const group=()=>({id:'policy-group',category_id:category.id,category_name:category.name,category_code:category.code,year_id:year.id,year_name:year.name,class_id:'policy-class',class_name:'界面验收班',student_user_id:user.id,student_name:user.display_name,state:'PARTIAL',approved_total:'0.30',items:Object.keys(sources).map(item)});
const boot={user,years:[year],active_year:year,current_year:year,classes:[{id:'policy-class',name:'界面验收班'}],categories:[category],permissions:['application.read','application.write','application.submit','activity.read','publicity.read','organization.read'],demo_accounts:[]};
const writes=[],errors=[],checks=[];const pass=t=>{checks.push(t);console.log('PASS '+t);};
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1050}});page.on('pageerror',e=>errors.push(e.message));
await page.route('**/mock-assets/**',route=>route.fulfill({status:200,contentType:'image/svg+xml',body:svg}));
await page.route('**/api/**',async route=>{
 const req=route.request(),url=new URL(req.url()),method=req.method();let data;
 if(method!=='GET')writes.push({method,path:url.pathname});
 if(method!=='GET'){errors.push('Unexpected write '+method+' '+url.pathname);return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"mock forbids writes"}'});}
 if(url.pathname==='/api/session')data={user,csrf_token:'mock-csrf'};
 else if(url.pathname==='/api/bootstrap')data=boot;
 else if(url.pathname==='/api/analysis-jobs')data={jobs};
 else if(url.pathname==='/api/submissions')data=[group()];
 else if(url.pathname==='/api/activities')data=[];
 else if(url.pathname.startsWith('/api/applications/')){const id=url.pathname.split('/').at(-1),s=sources[id];if(s)data={...item(id),can_edit:s.state==='DRAFT',can_review:false,can_withdraw:false,revision:{id:'revision-'+id,title:s.title,requested_score:'0.30',payload_json:{}},files:s.files,crops:s.crops,ocr_policy:s.policy,history:[],reviews:[],versions:[]};}
 if(!data){errors.push('Unexpected API '+url.pathname);return route.fulfill({status:400,contentType:'application/json',body:'{"detail":"unexpected mock API"}'});}
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
});
const open=async id=>{await page.locator(`[data-action="v2-select-item"][data-id="${id}"]`).click();await page.locator('.comparison-header h3').filter({hasText:sources[id].title}).waitFor();};
const hasCrop=id=>page.locator(`.workspace-evidence [data-crop="${id}"]`);
const out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});
try{
 await page.goto(base+'/#applications');await page.locator('[data-item-row="scan"]').waitFor();
 assert.match(await page.locator('[data-item-progress="scan"]').innerText(),/手动填写和框选/);
 assert.doesNotMatch(await page.locator('[data-item-progress="scan"]').innerText(),/失败/);
 await page.locator('[data-job-action="expand"]').click();assert.match(await page.locator('[data-job-id="job-scan"]').innerText(),/需手动处理/);assert.doesNotMatch(await page.locator('[data-job-id="job-scan"]').innerText(),/OCR失败/);assert.equal(await page.locator('[data-job-id="job-scan"] [data-job-action="retry"]').count(),0);pass('长扫描件跳过云OCR显示手工处理，不渲染为OCR失败或诱导反复重试');
 await open('mixed');const longCard=page.locator(`[data-source-file="${long.id}"]`),shortCard=page.locator(`[data-source-file="${short.id}"]`);
 assert.match(await longCard.innerText(),/57页.*已跳过云OCR/s);assert.match(await shortCard.innerText(),/2页/);assert.match(await page.locator('#workspace-policy-results').innerText(),/1份其它附件继续独立处理/);assert.match(await page.locator('[data-item-progress="mixed"]').innerText(),/短附件已识别.*长件手动/);pass('同小项长扫描件跳过、短附件独立识别，显示部分完成而非整项失败');
 assert.match(await shortCard.innerText(),/首页和末页未找到印章/);assert.match(await shortCard.innerText(),/原件其它页.*手动框选/s);assert.equal(await page.locator('.workspace-evidence [data-crop]').count(),0);pass('首页末页未检出印章时明确要求到原件其它页寻找，无伪造印章截图');
 assert.equal(await page.locator('#workspace-title').isEditable(),true);assert.equal(await page.locator('#workspace-score').isEditable(),true);assert.equal(await page.locator('[data-action="v2-save-item"]').isEnabled(),true);pass('跳过云OCR不锁定手工名称、计分与保存操作');
 await open('native');const nativeCard=page.locator(`[data-source-file="${native.id}"]`);
 assert.match(await nativeCard.innerText(),/56页.*本地读取可用文字层/s);assert.doesNotMatch(await nativeCard.innerText(),/已跳过云OCR，请手动框选/);assert.match(await nativeCard.innerText(),/不消耗OCR额度/);assert.match(await nativeCard.innerText(),/不自动查章/);await hasCrop('new-native-crop').waitFor();pass('长原生PDF本地文字与位置可继续AI及自动参与证据，文字不耗云OCR且印章手动');
 assert.equal(await hasCrop('old-cloud-crop').count(),0);assert.equal(await hasCrop('old-middle-seal').count(),0);assert.equal(await hasCrop('old-long-seal').count(),0);await hasCrop('manual-long-seal').waitFor();await hasCrop('manual-middle-seal').waitFor();pass('工作稿隐藏旧云长文件及中间页AI印章，保留长文件和中间页手工证据');
 await open('partial');assert.match(await page.locator(`[data-source-file="${partial.id}"]`).innerText(),/其中3页读取可用文字层/);assert.match(await page.locator(`[data-source-file="${partial.id}"]`).innerText(),/17页没有可用文字层/);pass('部分文字层长PDF按页说明本地已读与其余手动页面');
 await open('checking');const checkingCard=page.locator(`[data-source-file="${checking.id}"]`);assert.match(await checkingCard.innerText(),/正在检查已有文字层，已检查18\/234页/);assert.match(await checkingCard.innerText(),/检测完成后决定本地读取、云OCR或手工处理/);assert.doesNotMatch(await checkingCard.innerText(),/扫描件|已跳过云OCR|没有可用文字层|不自动查章|其中3页读取/);assert.equal(await page.locator('#workspace-title').isEditable(),true);pass('234页PDF检查中优先展示18/234页进度，已有部分文字缓存也不预判扫描件或纯手工');
 await open('frozen');await hasCrop('frozen-long-ai').waitFor();await hasCrop('frozen-middle-ai').waitFor();assert.equal(await page.locator('#workspace-title').count(),0);pass('已冻结提交记录保留旧证据且只读，不被当前自动政策隐藏');
 await open('quota');assert.match(await page.locator('[data-item-progress="quota"]').innerText(),/云识别已暂停.*手动/);assert.match(await page.locator('#workspace-policy-results').innerText(),/免费额度不足/);assert.match(await page.locator('#workspace-policy-results').innerText(),/不会自动切换服务商/);assert.equal(await page.locator('#workspace-score').isEditable(),true);pass('免费额度不足明确暂停且不切换服务商，仍可手工填写');
 await page.locator('[data-action="new-application"]').first().click();const modal=page.locator('.modal');await page.locator('#organizer-main-files').waitFor({state:'attached'});const policy=await modal.innerText();assert.match(policy,/200MB.*500页/s);assert.match(policy,/可用文字层.*本地提取/s);assert.match(policy,/扫描件.*12页/s);assert.match(policy,/不会自动切换到火山/);
 await page.locator('#organizer-main-files').setInputFiles({name:'本地整理测试.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-mock-only')});await page.locator('.organizer-row').waitFor();assert.deepEqual(writes,[]);pass('上传弹窗区分200MB/500页上传与12页扫描云识别；本地选文件不触发上传或OCR');
 await page.locator('#organizer-cancel').click();await page.locator('#background-jobs').evaluate(el=>el.style.visibility='hidden');await open('mixed');await page.locator('#toast-root').evaluate(el=>el.replaceChildren());await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(out,'10-ocr-budget-and-manual-policy-mock.png'),fullPage:true,animations:'disabled'});
 await open('native');await page.evaluate(()=>{document.querySelectorAll('img').forEach(img=>img.loading='eager');window.scrollTo(0,0);});await page.screenshot({path:resolve(out,'11-native-text-policy-mock.png'),fullPage:true,animations:'disabled'});
 await page.locator('#background-jobs').evaluate(el=>el.style.visibility='');await page.locator('.sidebar [data-nav="activities"]').click();await page.locator('#activity-list').waitFor();await page.locator('#background-jobs').waitFor();assert.deepEqual(writes,[]);pass('跨页面保留后台任务栏，整套政策验收零业务写请求、零云调用');
 assert.deepEqual(errors,[]);
}catch(e){errors.push(e.stack||String(e));throw e;}
finally{await writeFile(resolve(out,'browser-ocr-policy-mock.json'),JSON.stringify({source:'Every /api request intercepted; no real business APIs, writes, keys or provider calls',checks,errors,writes,createdAt:new Date().toISOString()},null,2));await browser.close();}
