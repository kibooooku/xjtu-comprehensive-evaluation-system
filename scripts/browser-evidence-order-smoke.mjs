/** Evidence order with a 56-page file. Every API and image is mocked. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const year={id:'evidence-year',name:'2025-2026学年'},category={id:'evidence-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'};
let role='STUDENT',ready=false,manual=0,empty=false;
const modelExplanation='MODEL_EXPLANATION_MUST_STAY_HIDDEN：'+'这是一段仅用于模拟的长篇模型分析，不应展示在学生或审核页面。'.repeat(15);
const user=()=>({id:role==='STUDENT'?'evidence-owner':'evidence-reader',username:'evidence-test',display_name:'证据顺序验收同学',base_identity:role==='COUNSELOR'?'COUNSELOR':'STUDENT',roles:[{code:role}]});
const file=()=>({id:'evidence-file',original_name:'56页虚构参与名单.pdf',page_count:56,mime_type:'application/pdf',url:'/mock-evidence/original.pdf',native_text_status:'USED',native_pages:Array.from({length:56},(_,i)=>i+1),text_layer_check_complete:true,text_layer_checked_page_count:56,text_covered_page_count:56,seal_status:'FOUND',automatic_ocr_status:'COMPLETED',pages:Array.from({length:56},(_,i)=>({id:'evidence-page-'+(i+1),page_index:i+1,width_px:900,height_px:1200,url:'/mock-evidence/page.svg'}))});
const crops=()=>empty?[]:[{id:'evidence-part',page_id:'evidence-page-28',file_name:'56页虚构参与名单.pdf',page_number:28,evidence_type:'PARTICIPATION',source:'MANUAL',url:'/mock-evidence/part.svg'},...['NAME','TIME','SEAL'].map((kind,i)=>({id:'evidence-'+kind,page_id:'evidence-page-1',file_name:'56页虚构参与名单.pdf',page_number:1,evidence_type:'VALIDITY',validity_kind:kind,source:'MANUAL',url:'/mock-evidence/crop.svg'})),...(ready?[{id:'evidence-completed',page_id:'evidence-page-56',file_name:'56页虚构参与名单.pdf',page_number:56,evidence_type:'VALIDITY',validity_kind:'SEAL',source:'MANUAL',url:'/mock-evidence/crop.svg'}]:[]),...Array.from({length:manual},(_,i)=>({id:'evidence-manual-'+i,page_id:'evidence-page-1',file_name:'56页虚构参与名单.pdf',page_number:1,evidence_type:'PARTICIPATION',source:'MANUAL',url:'/mock-evidence/part.svg'}))];
const job=()=>({id:'evidence-job',application_id:'evidence-item',kind:'OCR',state:ready?'SUCCEEDED':'RUNNING',stage:ready?'完成':'读取原生文字',progress:ready?56:28,total:56,created_at:'2026-10-26T04:00:00Z',...(ready?{finished_at:'2026-10-26T04:01:00Z'}:{}),result_json:{status:ready?'SUCCEEDED':'RUNNING'}});
const item=()=>({id:'evidence-item',title:'虚构夜跑活动',requested_score:'0.30',state:role==='STUDENT'?'DRAFT':'PENDING_PEER',category_id:category.id,category_name:category.name,category_code:category.code,student_user_id:'evidence-owner',student_name:'证据顺序验收同学',class_id:'evidence-class',class_name:'虚构班',year_id:year.id,year_name:year.name,lock_version:1,file_count:1,files:[{id:'evidence-file',original_name:'56页虚构参与名单.pdf'}]});
const detail=()=>({...item(),can_edit:role==='STUDENT',can_review:role!=='STUDENT',can_withdraw:false,revision:{id:'evidence-revision',title:'虚构夜跑活动',requested_score:'0.30',payload_json:{ai_explanation:modelExplanation}},latest_analysis:{id:'evidence-analysis',status:'SUCCEEDED',result_json:{suggestion:{title:'虚构夜跑活动',requested_score:'0.30',explanation:modelExplanation}}},files:[file()],crops:crops(),history:[],reviews:[],versions:[]});
const group=()=>({id:'evidence-group',category_id:category.id,category_name:category.name,year_id:year.id,year_name:year.name,class_id:'evidence-class',class_name:'虚构班',student_user_id:'evidence-owner',student_name:'证据顺序验收同学',approved_total:'0.00',items:[item()]});
const boot=()=>({user:user(),years:[year],active_year:year,current_year:year,classes:[{id:'evidence-class',name:'虚构班'}],categories:[category],permissions:['application.read','application.write','application.submit','activity.read','review.peer','review.counselor'],demo_accounts:[]});
const svg=(crop=false)=>`<svg xmlns="http://www.w3.org/2000/svg" width="900" height="${crop?225:1200}"><rect width="100%" height="100%" fill="#edf6f6"/><text x="35" y="75" font-size="30" fill="#315775">${crop?'Mock evidence — no personal data':'Mock original page — no personal data'}</text></svg>`;
const writes=[],errors=[],checks=[],out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050}});
page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(()=>{const scroll=Element.prototype.scrollIntoView;Element.prototype.scrollIntoView=function(options){return scroll.call(this,options&&typeof options==='object'?{...options,behavior:'instant'}:options);};});
await page.route('**/mock-evidence/**',route=>route.fulfill({status:200,contentType:'image/svg+xml',body:svg(!route.request().url().endsWith('page.svg'))}));
await page.route('**/api/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname,json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(req.method()!=='GET'){
    writes.push({method:req.method(),path,body:req.postDataJSON()});
    if(req.method()==='POST'&&path==='/api/applications/evidence-item/crops'){manual++;return json(crops().at(-1));}
    if(req.method()==='PUT'&&path==='/api/applications/evidence-item')return json(detail());
    if(req.method()==='POST'&&path==='/api/applications/evidence-item/analyze')return json({status:'SUCCEEDED',message:modelExplanation,analysis:{id:'evidence-analysis',status:'SUCCEEDED'},suggestion:{title:'虚构夜跑活动',score:'0.30',explanation:modelExplanation,reason:modelExplanation}});
    return json({detail:'Only a fixture crop write is allowed'},403);
  }
  if(path==='/api/session')return json({user:user(),csrf_token:'mock'});
  if(path==='/api/bootstrap')return json(boot());
  if(path==='/api/dashboard'){
    assert.deepEqual(Object.fromEntries(new URL(req.url()).searchParams),{year_id:year.id,class_id:'evidence-class'});
    const pendingPeer=item().state==='PENDING_PEER'?1:0,pendingCounselor=item().state==='PENDING_COUNSELOR'?1:0;
    return json({stats:{pending:role==='COUNSELOR'?pendingCounselor:pendingPeer+pendingCounselor,pending_peer:pendingPeer,pending_counselor:pendingCounselor,pending_total:pendingPeer+pendingCounselor,applications:1,approved:0,total_score:'70.00',baseline:'70.00'},category_scores:[],recent:[]});
  }
  if(path==='/api/submissions')return json([group()]);
  if(path==='/api/applications/evidence-item')return json(detail());
  if(path==='/api/activities')return json([]);
  if(path==='/api/analysis-jobs')return json({jobs:[job()]});
  errors.push('Unexpected mock request '+path);return json({},404);
});
const pass=text=>{checks.push(text);console.log('PASS '+text);};
const open=async view=>{await page.goto(base+'/?evidence-order='+Date.now()+'#'+view);await page.locator('[data-action="v2-select-item"]').click();await page.locator('#workspace-item-form').waitFor();};
const assertOrder=async()=>{assert.equal(await page.evaluate(()=>{const evidence=document.querySelector('#workspace-evidence-results'),originals=document.querySelector('.source-file-list');return Boolean(evidence&&originals&&(evidence.compareDocumentPosition(originals)&Node.DOCUMENT_POSITION_FOLLOWING));}),true);assert.deepEqual(await page.locator('#workspace-evidence-results .workspace-evidence h4').allTextContents(),['本人参与证明','有效性证据']);assert.equal(await page.locator('.source-page').count(),56);};
const assertExplanationHidden=async()=>{assert.doesNotMatch(await page.locator('body').innerText(),/MODEL_EXPLANATION_MUST_STAY_HIDDEN/);assert.equal(await page.evaluate(()=>S.detail.revision.payload_json.ai_explanation.startsWith('MODEL_EXPLANATION_MUST_STAY_HIDDEN')),true);};
try{
  await open('applications');await assertOrder();await assertExplanationHidden();assert.equal(await page.locator('#workspace-title').inputValue(),'虚构夜跑活动');assert.equal(await page.locator('#workspace-score').inputValue(),'0.30');assert.equal(await page.locator('#workspace-apply-suggestion').count(),1);pass('学生初次打开56页材料时，证据在原件之前；隐藏模型长篇说明并保留名称、分值及应用建议按钮');
  const screenshotStyle=await page.addStyleTag({content:'.jobs-dock{visibility:hidden!important}'});
  await page.locator('#workspace-evidence-results').screenshot({path:resolve(out,'15-evidence-before-originals-mock.png'),animations:'disabled'});await screenshotStyle.evaluate(el=>el.remove());
  await page.evaluate(()=>{window.preserved={form:document.querySelector('#workspace-item-form'),image:document.querySelector('.source-page img'),files:document.querySelector('.source-file-list')};});ready=true;await page.evaluate(()=>pollJobs());await assertOrder();assert.equal(await page.locator('#workspace-evidence-results .context-evidence').count(),5);assert.equal(await page.evaluate(()=>window.preserved.form===document.querySelector('#workspace-item-form')&&window.preserved.image===document.querySelector('.source-page img')&&window.preserved.files===document.querySelector('.source-file-list')),true);pass('后台识别完成后只更新证据容器，证据仍在原件前且原图与表单节点不变');
  await page.locator('#workspace-title').fill('尚未保存的手工名称');await page.evaluate(()=>cropModal('evidence-page-1'));await page.waitForFunction(()=>document.querySelector('#crop-canvas')?.width===900);const bounds=await page.locator('#crop-canvas').boundingBox();await page.mouse.move(bounds.x+bounds.width*.1,bounds.y+bounds.height*.1);await page.mouse.down();await page.mouse.move(bounds.x+bounds.width*.8,bounds.y+bounds.height*.3,{steps:5});await page.mouse.up();await page.locator('#crop-save').click();await page.waitForFunction(()=>document.querySelector('#crop-draft-status')?.textContent.includes('本页已保存'));await assertOrder();assert.equal(await page.locator('#workspace-evidence-results .context-evidence').count(),6);assert.equal(await page.locator('#workspace-title').inputValue(),'尚未保存的手工名称');await page.locator('.modal-footer [data-action="close-modal"]').click();pass('手工保存截图后仍更新原位置的证据，保持未保存表单及当前原件节点');
  for(const next of ['REVIEWER','COUNSELOR']){role=next;await open('review');await assertOrder();await assertExplanationHidden();assert.equal(await page.locator('#workspace-title').count(),0);}
  pass('学生审查员和辅导员的只读比对同样证据优先，且不展示模型长篇分析');
  await page.evaluate(()=>go('detail',{id:'evidence-item'}));await page.locator('#workspace-item-form').waitFor();await assertOrder();await assertExplanationHidden();assert.equal(await page.locator('.detail-grid,#version-selector,[data-detail-materials]').count(),0);assert.doesNotMatch(await page.locator('.comparison-actions').innerText(),/完整版本与历史/);pass('旧详情入口回到统一对照区，旧页面与完整版本入口已移除，证据顺序和简短状态保留');
  role='STUDENT';empty=true;ready=false;await open('applications');await assertOrder();assert.equal(await page.locator('#workspace-evidence-results .evidence-empty').count(),2);empty=false;await page.evaluate(()=>refreshWorkspaceMaterials());await assertOrder();assert.equal(await page.locator('#workspace-evidence-results .context-evidence').count(),5);pass('尚无证据时占位在原件之前，新证据到达仍填入同一位置');
  await page.evaluate(()=>showWorkspaceSuggestion({result_json:{analysis_id:'evidence-analysis',suggestion:{title:'虚构夜跑活动',requested_score:'0.30',explanation:'MODEL_EXPLANATION_MUST_STAY_HIDDEN'}}}));await page.locator('#workspace-apply-suggestion').waitFor();await assertExplanationHidden();assert.match(await page.locator('#workspace-analysis-suggestion .selection-box').innerText(),/虚构夜跑活动[\s\S]*0\.30/);pass('统一对照区保留短状态、名称、分值及应用建议入口，不显示模型长篇说明');
  assert.equal(writes.length,1);assert.deepEqual(writes.map(entry=>entry.path),['/api/applications/evidence-item/crops']);assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{await writeFile(resolve(out,'browser-evidence-order-mock.json'),JSON.stringify({source:'All APIs/images mocked; no actual database, provider or API writes',checks,errors,mock_writes:writes,createdAt:new Date().toISOString()},null,2));await browser.close();}
