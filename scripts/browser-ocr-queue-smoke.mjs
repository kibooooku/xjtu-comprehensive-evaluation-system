/** Pure UI regression: every /api request is intercepted. No real API writes/cloud calls. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const user={id:'mock-student',username:'mock-student',display_name:'界面验收同学',base_identity:'STUDENT',roles:[{code:'STUDENT',class_id:null}]};
const year={id:'mock-year',code:'2025-2026',name:'2025-2026学年'},category={id:'mock-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'};
const ocr={id:'mock-running-ocr',application_id:'mock-item',owner_id:user.id,kind:'OCR',state:'RUNNING',stage:'正在识别原件第3页',progress:3,total:18,created_at:'2026-10-24T08:00:00Z',result_json:{}};
const ai={id:'mock-waiting-ai',application_id:'mock-item',owner_id:user.id,kind:'AI',state:'QUEUED',stage:'等待OCR完成（原OCR任务继续执行）',progress:0,total:1,created_at:'2026-10-24T10:00:00Z',wait_for_ocr_job_id:ocr.id,waiting_for_ocr:true,result_json:{wait_for_ocr_job_id:ocr.id}};
const completed=Array.from({length:6},(_,i)=>({id:'mock-past-'+i,application_id:'mock-past-item-'+i,owner_id:user.id,kind:'AI',state:'SUCCEEDED',stage:'已完成',progress:1,total:1,created_at:`2026-10-24T09:0${i}:00Z`,finished_at:`2026-10-24T09:0${i}:30Z`,result_json:{}}));
let queued=false;const writes=[],reads=[],errors=[],checks=[];
const item=()=>({id:'mock-item',title:'集体活动证明（任务显示验收）',category_id:category.id,category_name:category.name,category_code:category.code,requested_score:'0.30',state:'DRAFT',student_name:user.display_name,student_user_id:user.id,class_id:'mock-class',class_name:'界面验收班',year_id:year.id,year_name:year.name,lock_version:1,file_count:1,primary_file_name:'18页演示材料.pdf',ocr_status:'RUNNING',analysis_status:queued?'QUEUED':'UNANALYZED',latest_job:queued?ai:ocr});
const group=()=>({id:'mock-group',category_id:category.id,category_name:category.name,category_code:category.code,year_id:year.id,year_name:year.name,class_id:'mock-class',class_name:'界面验收班',student_user_id:user.id,student_name:user.display_name,item_count:1,counts:{DRAFT:1},state:'DRAFT',requested_total:'0.30',approved_total:'0.00',items:[item()]});
const boot={user,years:[year],active_year:year,current_year:year,classes:[{id:'mock-class',name:'界面验收班'}],categories:[category],permissions:['application.read','application.write','activity.read','publicity.read','organization.read'],demo_accounts:[]};
const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',async route=>{
 const request=route.request(),url=new URL(request.url()),method=request.method();let data;
 if(method!=='GET')writes.push({method,path:url.pathname});else reads.push(url.pathname);
 if(url.pathname==='/api/session')data={user,csrf_token:'mock-csrf'};
 else if(url.pathname==='/api/bootstrap')data=boot;
 else if(url.pathname==='/api/analysis-jobs'&&method==='GET')data={jobs:queued?[ai,...completed,ocr]:[...completed,ocr]};
 else if(url.pathname==='/api/submissions'&&method==='GET')data=[group()];
 else if(url.pathname==='/api/applications/mock-item'&&method==='GET')data={...item(),can_edit:true,can_review:false,can_withdraw:false,revision:{id:'mock-revision',title:item().title,requested_score:'0.30',payload_json:{}},files:[],crops:[],history:[],reviews:[],versions:[]};
 else if(url.pathname==='/api/activities'&&method==='GET')data=[];
 else if(url.pathname==='/api/analysis-jobs/bulk'&&method==='POST'){queued=true;data={jobs:[ai],skipped:[]};}
 else {errors.push(`Unexpected intercepted API: ${method} ${url.pathname}`);return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({detail:'测试未允许此API'})});}
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
});
const pass=text=>{checks.push(text);console.log('PASS '+text);};
const out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});
try{
 await page.goto(base+'/#applications');await page.locator('[data-item-progress="mock-item"]').filter({hasText:'OCR 识别中 3/18'}).waitFor();pass('已有运行OCR优先展示原进度3/18');
 await page.locator('[data-action="v2-select-item"][data-id="mock-item"]').click();await page.locator('#workspace-item-form').waitFor();
 await page.locator('[data-action="v2-bulk-analyze"][data-id="mock-group"]').click();await page.locator('[data-item-progress="mock-item"]').filter({hasText:'OCR 识别中 3/18 · AI 等待 OCR'}).waitFor();pass('点击一键分析后仍显示OCR运行，并同时注明AI等待OCR');
 await page.locator('[data-job-action="expand"]').click();const ocrRow=page.locator('[data-job-id="mock-running-ocr"]'),aiRow=page.locator('[data-job-id="mock-waiting-ai"]');await ocrRow.waitFor({state:'visible'});await aiRow.waitFor({state:'visible'});assert.match(await ocrRow.innerText(),/正在识别原件第3页.*3\/18/s);assert.match(await aiRow.innerText(),/等待OCR完成/);assert.equal(await page.locator('#jobs-list .job-row').first().getAttribute('data-job-id'),ocr.id);pass('即使存在6条更新的历史任务，任务栏仍优先保留运行OCR与排队AI两行');
 ocr.progress=8;ocr.stage='正在识别原件第8页';await page.evaluate(()=>pollJobs());await page.locator('[data-item-progress="mock-item"]').filter({hasText:'OCR 识别中 8/18 · AI 等待 OCR'}).waitFor();assert.match(await ocrRow.innerText(),/8\/18/);pass('后续轮询继续推进同一OCR到8/18，未被排队AI覆盖');
 assert.deepEqual(writes,[{method:'POST',path:'/api/analysis-jobs/bulk'}]);pass('未修改的表单点击一键分析仅提交bulk，没有PUT保存、DELETE取消或任务中止请求');
 await page.locator('.sidebar [data-nav="activities"]').click();await page.locator('#activity-list').waitFor();await ocrRow.waitFor({state:'visible'});await aiRow.waitFor({state:'visible'});pass('切换页面仍保留OCR进度和等待AI，不销毁任务显示');
 await page.locator('.sidebar [data-nav="applications"]').click();await page.locator('[data-item-progress="mock-item"]').filter({hasText:'OCR 识别中 8/18 · AI 等待 OCR'}).waitFor();await page.locator('#toast-root').evaluate(el=>el.replaceChildren());await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:resolve(out,'09-ocr-and-waiting-ai-mock.png'),fullPage:true,animations:'disabled'});
 assert.equal(errors.length,0,errors.join('; '));
}catch(e){errors.push(e.stack||String(e));throw e;}
finally{await writeFile(resolve(out,'browser-ocr-queue-mock.json'),JSON.stringify({source:'Every /api request intercepted by browser fixture; no real API writes or provider calls',checks,errors,writes,reads,createdAt:new Date().toISOString()},null,2));await browser.close();}
