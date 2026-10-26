/** Stage-specific counselor queue checks. Every API is mocked; no real writes. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const year={id:'queue-year',name:'2025-2026学年'},classroom={id:'queue-class',name:'虚构审核班'};
const category={id:'queue-category',code:'RESEARCH',name:'学术科研及创新创业',default_cap:'10.00',requires_eligibility_review:true};
let role='COUNSELOR',peerCount=7,counselorCount=0;
const user=()=>({id:'queue-reader',username:'queue-reader',display_name:'队列验收老师',base_identity:role==='COUNSELOR'?'COUNSELOR':'STUDENT',roles:[{code:role}]});
const item=(state,index)=>({id:state==='PENDING_PEER'?'peer-'+index:'counselor-'+index,title:state==='PENDING_PEER'?`待学生初审活动 ${index}`:`需辅导员认定活动 ${index}`,requested_score:'0.30',state,category_id:category.id,category_name:category.name,category_code:category.code,student_user_id:'queue-owner',student_name:'虚构申报同学',class_id:classroom.id,class_name:classroom.name,year_id:year.id,year_name:year.name,lock_version:1,file_count:1,files:[{id:'queue-file',original_name:'虚构材料.pdf'}]});
const items=()=>[...Array.from({length:peerCount},(_,i)=>item('PENDING_PEER',i+1)),...Array.from({length:counselorCount},(_,i)=>item('PENDING_COUNSELOR',i+1))];
const group=()=>({id:'queue-group',category_id:category.id,category_name:category.name,category_code:category.code,year_id:year.id,year_name:year.name,class_id:classroom.id,class_name:classroom.name,student_user_id:'queue-owner',student_name:'虚构申报同学',state:counselorCount?'PARTIAL':'PENDING_PEER',approved_total:'0.00',items:items()});
const boot=()=>({user:user(),years:[year],active_year:year,current_year:year,classes:[classroom],categories:[category],permissions:['application.read','activity.read','scores.read',...(role==='COUNSELOR'?['review.counselor']:['review.peer'])],demo_accounts:[]});
const dashboard=()=>({stats:{pending:role==='COUNSELOR'?counselorCount:peerCount+counselorCount,pending_peer:peerCount,pending_counselor:counselorCount,pending_total:peerCount+counselorCount,applications:peerCount+counselorCount,approved:0,total_score:'70.00',baseline:'70.00'},category_scores:[],recent:[]});
const reads=[],writes=[],errors=[],checks=[],out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});
const pass=message=>{checks.push(message);console.log('PASS '+message);};
const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1050}});
page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(()=>{const scroll=Element.prototype.scrollIntoView;Element.prototype.scrollIntoView=function(options){return scroll.call(this,options&&typeof options==='object'?{...options,behavior:'instant'}:options);};});
await page.route('**/api/**',async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(req.method()!=='GET'){writes.push({method:req.method(),path});return json({detail:'This fixture rejects every mutation'},403);}
  reads.push({path,query:Object.fromEntries(url.searchParams)});
  if(path==='/api/session')return json({user:user(),csrf_token:'mock'});
  if(path==='/api/bootstrap')return json(boot());
  if(path==='/api/analysis-jobs')return json({jobs:[]});
  if(path==='/api/dashboard')return json(dashboard());
  if(path==='/api/submissions')return json(url.searchParams.get('scope')==='review'&&role==='COUNSELOR'&&!counselorCount?[]:[group()]);
  if(path==='/api/activities')return json([]);
  if(path.startsWith('/api/applications/')){const selected=items().find(i=>i.id===path.split('/')[3]);if(selected)return json({...selected,can_edit:false,can_review:role==='COUNSELOR'?selected.state==='PENDING_COUNSELOR':selected.state==='PENDING_PEER',can_withdraw:false,revision:{id:'queue-revision-'+selected.id,title:selected.title,requested_score:selected.requested_score,payload_json:{}},files:[],crops:[],history:[],reviews:[],versions:[]});}
  errors.push('Unexpected mock API '+path);return json({},404);
});
const load=async view=>{await page.goto(base+'/?counselor-queue='+Date.now()+'#'+view);await page.locator('#page .page-title').waitFor();};
const openItem=async id=>{await page.locator(`[data-action="v2-select-item"][data-id="${id}"]`).click();await page.locator('#workspace-item-form').waitFor();};
try{
  await load('dashboard');const pending=page.locator('.stats-grid .stat').nth(1);assert.equal(await pending.locator('.stat-label').innerText(),'待辅导员审核');assert.match(await pending.locator('.stat-value').innerText(),/^0/);assert.equal(await pending.locator('.stat-foot').innerText(),'另有 7 项待学生初审');pass('辅导员工作台只将本阶段0项标为待辅导员审核，另列7项学生初审');
  await page.evaluate(()=>go('review'));await page.locator('#counselor-review-stages').waitFor();assert.match(await page.locator('#counselor-review-stages').innerText(),/待辅导员审核 0 项 · 待学生初审 7 项/);assert.match(await page.locator('#submission-groups').innerText(),/当前没有待辅导员审核的小项/);assert.match(await page.locator('#submission-groups').innerText(),/学生初审完成后/);assert.equal(await page.locator('[data-action="v2-review-item"]').count(),0);await page.screenshot({path:resolve(out,'16-counselor-stage-queue-mock.png'),animations:'disabled'});pass('空队列明确区分两阶段并说明后续流转，不展示越阶段审核入口');
  await page.locator('#submission-groups [data-nav="classApplications"]').click();await page.locator('[data-item-row]').first().waitFor();assert.equal(await page.locator('[data-item-row]').count(),7);assert.deepEqual(await page.evaluate(()=>({view:S.view,year:S.workspaceYear,class:S.classId})),{view:'classApplications',year:year.id,class:classroom.id});await openItem('peer-1');assert.equal(await page.locator('[data-action="v2-review-item"]').count(),0);pass('查看班级申报保留当前学年班级，可跟踪初审项且只能只读比对');
  counselorCount=1;await load('review');await page.locator('#counselor-review-stages').waitFor();assert.match(await page.locator('#counselor-review-stages').innerText(),/待辅导员审核 1 项 · 待学生初审 7 项/);await openItem('peer-1');assert.equal(await page.locator('[data-action="v2-review-item"]').count(),0);await openItem('counselor-1');assert.equal(await page.locator('[data-action="v2-review-item"]').count(),1);pass('已有辅导员阶段申请时正确列入队列，同组初审项仍无辅导员审核按钮');
  await page.locator('#workspace-search').fill('无匹配的测试关键词');assert.match(await page.locator('#submission-groups').innerText(),/没有匹配的待审小项/);assert.match(await page.locator('#submission-groups').innerText(),/清空搜索条件/);assert.match(await page.locator('#counselor-review-stages').innerText(),/待辅导员审核 1 项/);await page.locator('#workspace-search').fill('');await page.locator('[data-item-row]').first().waitFor();pass('搜索空态说明筛选无匹配，阶段计数保留且清空搜索后恢复队列');
  const counselorDashboardReads=reads.filter(r=>r.path==='/api/dashboard');assert.ok(counselorDashboardReads.length>=3);for(const request of counselorDashboardReads)assert.deepEqual(request.query,{year_id:year.id,class_id:classroom.id});
  role='REVIEWER';const before=reads.filter(r=>r.path==='/api/dashboard').length;await load('review');await page.locator('[data-item-row]').first().waitFor();assert.equal(await page.locator('#counselor-review-stages').count(),0);assert.equal(reads.filter(r=>r.path==='/api/dashboard').length,before);await openItem('peer-1');assert.equal(await page.locator('[data-action="v2-review-item"]').count(),1);pass('队列计数请求使用相同学年班级，学生审查员入口不额外请求辅导员统计');
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{await writeFile(resolve(out,'browser-counselor-queue-mock.json'),JSON.stringify({source:'All APIs mocked; zero actual API/database/cloud writes',checks,errors,writes,reads,createdAt:new Date().toISOString()},null,2));await browser.close();}
