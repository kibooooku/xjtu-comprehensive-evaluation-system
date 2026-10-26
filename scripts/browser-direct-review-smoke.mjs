/** Reviewer actions use only intercepted APIs and synthetic records. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000',out=resolve('docs/screenshots-v2');
const year={id:'direct-year',name:'2025-2026学年'},classroom={id:'direct-class',name:'虚构审核班'},category={id:'direct-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'};
let role='STUDENT',state='PENDING_PEER',version=3,self=false,permission=true,denied=false,hold=false,release,dropResponse=false,acceptedKey=null;
const user=()=>({id:'direct-reviewer',username:'direct-reviewer',display_name:'虚构审查员',base_identity:role,roles:[{code:role==='COUNSELOR'?'COUNSELOR':'REVIEWER'}]});
const item=(id='direct-item')=>({id,title:id==='direct-item'?'周末集体活动':'另一份待审核材料',requested_score:'0.30',state:id==='direct-item'?state:'PENDING_PEER',lock_version:id==='direct-item'?version:2,student_user_id:self?'direct-reviewer':'direct-owner',student_name:'虚构申报同学',category_id:category.id,category_name:category.name,category_code:category.code,year_id:year.id,year_name:year.name,class_id:classroom.id,class_name:classroom.name,files:[],file_count:0});
const detail=(id='direct-item')=>({...item(id),can_edit:false,can_review:true,can_withdraw:false,revision:{id:'direct-revision-'+id,title:item(id).title,requested_score:'0.30',payload_json:{}},crops:[],history:[],reviews:[],versions:[]});
const group=()=>({id:'direct-group',category_id:category.id,category_name:category.name,year_id:year.id,year_name:year.name,class_id:classroom.id,class_name:classroom.name,student_user_id:'direct-owner',student_name:'虚构申报同学',approved_total:state==='APPROVED'?'0.30':'0.00',items:[item(),item('direct-other')]});
const boot=()=>({user:user(),years:[year],current_year:year,active_year:year,classes:[classroom],categories:[category],permissions:['application.read','activity.read',...(permission?(role==='COUNSELOR'?['review.counselor','review.revoke']:['review.peer']):[])],demo_accounts:[]});
const reads=[],writes=[],checks=[],errors=[];const pass=text=>{checks.push(text);console.log('PASS '+text);};
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050}});
page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(()=>{const scroll=Element.prototype.scrollIntoView;Element.prototype.scrollIntoView=function(options){return scroll.call(this,options&&typeof options==='object'?{...options,behavior:'instant'}:options);};});
await page.route('**/api/**',async route=>{
 const req=route.request(),url=new URL(req.url()),path=url.pathname,json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 if(req.method()==='GET'){
  reads.push(path);
  if(path==='/api/session')return json({user:user(),csrf_token:'mock'});
  if(path==='/api/bootstrap')return json(boot());
  if(path==='/api/submissions')return json([group()]);
  if(path==='/api/activities')return json([]);
  if(path==='/api/analysis-jobs')return json({jobs:[]});
  if(path==='/api/dashboard')return json({stats:{pending_peer:state==='PENDING_PEER'?1:0,pending_counselor:state==='PENDING_COUNSELOR'?1:0}});
  if(path.startsWith('/api/applications/'))return json(detail(path.split('/')[3]));
 }
 if(req.method()==='POST'&&path==='/api/applications/direct-item/review'){
  const body=req.postDataJSON();writes.push({path,body});
  if(hold)await new Promise(resolve=>release=resolve);
  if(denied)return json({detail:'模拟版本冲突'},409);
  if(!acceptedKey||acceptedKey!==body.idempotency_key){assert.equal(body.expected_version,version);acceptedKey=body.idempotency_key;state=body.action==='APPROVE'?'APPROVED':'REJECTED';version++;}
  if(dropResponse){dropResponse=false;return route.abort('failed');}
  return json(detail());
 }
 errors.push('Unexpected mock request '+req.method()+' '+path);return json({},404);
});
const load=async({as='STUDENT',status='PENDING_PEER',own=false,allowed=true,view='review'}={})=>{role=as;state=status;version=3;self=own;permission=allowed;denied=false;hold=false;dropResponse=false;acceptedKey=null;await page.goto(base+'/?direct-review='+Date.now()+'#'+(view==='detail'?'review':view));await page.locator('[data-item-row]').first().waitFor();if(view==='detail')await page.evaluate(()=>go('detail',{id:'direct-item',from:'review'}));else{await page.locator('[data-action="v2-select-item"][data-id="direct-item"]').click();await page.locator('#workspace-item-form').waitFor();}};
const approve=()=>page.locator('[data-action="review-approve"][data-id="direct-item"]');
const reject=()=>page.locator('[data-action="review-reject"][data-id="direct-item"]');
await mkdir(out,{recursive:true});
try{
 await load();assert.equal(await approve().count(),1);assert.equal(await reject().count(),1);
 assert.equal(await approve().evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(33, 131, 75)');assert.equal(await reject().evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(200, 69, 69)');
 await page.screenshot({path:resolve(out,'17-direct-review-actions-mock.png'),animations:'disabled'});
 let before=writes.length;await approve().click();await page.waitForFunction(()=>!document.querySelector('[data-action="review-approve"]'));assert.equal(writes.length,before+1);assert.equal(writes.at(-1).body.action,'APPROVE');assert.equal(await page.locator('.modal').count(),0);pass('学生审查显示绿色批准和红色驳回，批准单击直接提交且不打开确认弹窗');

 await load();before=writes.length;await reject().click();await page.locator('#review-form').waitFor();assert.equal(await page.locator('#review-action').count(),0);await page.locator('#confirm-review').click();assert.equal(writes.length,before);assert.equal(await page.locator('#review-reason').inputValue(),'');
 const reason=await page.locator('#review-reason option').nth(1).getAttribute('value');await page.locator('#review-reason').selectOption(reason);await page.locator('#review-comment').fill('虚构材料缺少对应本人信息');await page.locator('#confirm-review').click();await page.locator('.modal').waitFor({state:'detached'});assert.equal(writes.at(-1).body.action,'REJECT');assert.equal(writes.at(-1).body.reason,reason);pass('红色驳回才弹窗，必须选择原因，无结果二次选择且备注可选');

 await load({as:'COUNSELOR',status:'PENDING_COUNSELOR'});await approve().click();await page.locator('[data-action="review-reject"]').waitFor();await page.waitForFunction(()=>!document.querySelector('[data-action="review-approve"]')&&Boolean(document.querySelector('#workspace-item-form')));assert.equal(writes.at(-1).body.action,'APPROVE');assert.equal('activity_decision' in writes.at(-1).body,false);assert.equal(await reject().count(),1);assert.equal(await reject().isEnabled(),true);pass('辅导员批准沿后端默认有效认定，无额外确认；已通过条目仅保留红色驳回');
 await reject().click();assert.equal(await page.locator('#review-qualification').inputValue(),'');await page.locator('#review-reason').selectOption(reason);await page.locator('#review-qualification').selectOption('VALID');await page.locator('#confirm-review').click();await page.locator('.modal').waitFor({state:'detached'});assert.equal(writes.at(-1).body.action,'REJECT');assert.equal(writes.at(-1).body.activity_decision,'VALID');pass('辅导员驳回保留不改变资格/有效/无效选择，可认定有效并驳回个人证明');

 for(const config of [{own:true},{allowed:false},{as:'COUNSELOR',status:'PENDING_PEER'},{as:'STUDENT',status:'PENDING_COUNSELOR'},{as:'STUDENT',status:'APPROVED'}]){await load(config);assert.equal(await approve().count(),0);assert.equal(await reject().count(),0);}pass('本人、无权限、非当前审核阶段和普通学生已通过记录不显示越权审核按钮');

 await load();hold=true;before=writes.length;await approve().click();await page.waitForTimeout(50);assert.equal(writes.length,before+1);assert.equal(await approve().isDisabled(),true);assert.equal(await reject().isDisabled(),true);await page.evaluate(()=>{approveReview('direct-item',document.querySelector('[data-action="review-approve"]'));reviewModal('direct-item');});assert.equal(writes.length,before+1);assert.equal(await page.locator('.modal').count(),0);hold=false;release();await page.waitForFunction(()=>!document.querySelector('[data-action="review-approve"]'));pass('同一小项批准和驳回共享进行中锁，重复点击不会发起第二次审批');

 await load();dropResponse=true;before=writes.length;await approve().click();await page.waitForFunction(()=>document.querySelector('[data-action="review-approve"]')?.disabled===false);assert.equal(writes.length,before+1);const firstKey=writes.at(-1).body.idempotency_key;await approve().click();await page.waitForFunction(()=>!document.querySelector('[data-action="review-approve"]'));assert.equal(writes.length,before+2);assert.equal(writes.at(-1).body.idempotency_key,firstKey);pass('响应丢失后的重试复用同一幂等键，已生效的审批不重复执行');

 await load();denied=true;await approve().click();await page.waitForFunction(()=>document.querySelector('[data-action="review-approve"]')?.disabled===false);assert.equal(await page.locator('#workspace-item-form').count(),1);assert.match(await page.locator('#toast-root').innerText(),/刷新核对最新状态/);assert.equal(await page.locator('.modal').count(),0);pass('版本冲突保留核对页面并明确要求刷新核对，不盲目修改版本重批');

 await load();hold=true;await approve().click();await page.waitForTimeout(50);const listReads=reads.filter(path=>path==='/api/submissions').length;await page.evaluate(()=>showModal('另一个窗口','<input id="other-modal-input" value="保留中的输入">','<button data-action="close-modal">关闭</button>'));hold=false;release();await page.waitForFunction(()=>document.querySelector('#toast-root')?.innerText.includes('已批准'));await page.waitForTimeout(50);assert.equal(await page.locator('#other-modal-input').inputValue(),'保留中的输入');assert.equal(reads.filter(path=>path==='/api/submissions').length,listReads);pass('迟到的批准响应不会关闭新弹窗或刷新其背景核对页面');

 await load();hold=true;await approve().click();await page.waitForTimeout(50);await page.locator('[data-action="v2-select-item"][data-id="direct-other"]').click();await page.waitForFunction(()=>S.workspaceItem?.id==='direct-other');hold=false;release();await page.waitForFunction(()=>document.querySelector('#toast-root')?.innerText.includes('已批准'));await page.waitForTimeout(50);assert.equal(await page.evaluate(()=>S.workspaceItem.id),'direct-other');assert.match(await page.locator('#workspace-item-form').innerText(),/另一份待审核材料/);pass('审批途中切换其它小项，迟到响应不覆盖当前详情或改变选择');

 await load();await reject().click();await page.locator('#review-reason').selectOption(reason);hold=true;await page.locator('#confirm-review').click();await page.waitForTimeout(50);await page.evaluate(()=>{closeModal();showModal('新打开的窗口','<input id="later-reject-modal" value="不能被关闭">');});hold=false;release();await page.waitForFunction(()=>document.querySelector('#toast-root')?.innerText.includes('已驳回'));assert.equal(await page.locator('#later-reject-modal').inputValue(),'不能被关闭');pass('驳回请求发出后关闭窗口，迟到成功不关闭后来打开的新窗口');

 await load({view:'detail'});assert.equal(await page.locator('.comparison-actions [data-action="review-approve"]').count(),1);assert.equal(await page.locator('.comparison-actions [data-action="review-reject"]').count(),1);await approve().click();await page.waitForFunction(()=>!document.querySelector('[data-action="review-approve"]'));assert.equal(await page.locator('.modal').count(),0);assert.equal(await page.locator('.detail-grid,#version-selector').count(),0);pass('旧详情入口统一定位对照区，使用相同两个按钮直接审批');
 assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{await writeFile(resolve(out,'browser-direct-review-mock.json'),JSON.stringify({source:'All APIs mocked; write entries are fixtures only, zero actual API/database/cloud writes',checks,errors,mock_writes:writes,createdAt:new Date().toISOString()},null,2));await browser.close();}
