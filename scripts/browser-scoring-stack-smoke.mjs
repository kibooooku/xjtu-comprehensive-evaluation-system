/** Real browser + HTTP API + SQLite + background worker; providers are offline fakes. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {openSync,closeSync} from 'node:fs';
import {spawn,execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {createServer} from 'node:net';

const root=resolve('.'),temporaryRoot=resolve('instance/scoring-stack-isolated'),port=8767,base=`http://127.0.0.1:${port}`;
await new Promise((resolve,reject)=>{const probe=createServer();probe.once('error',()=>reject(new Error('Fixture port is already occupied; no service was touched')));probe.listen(port,'127.0.0.1',()=>probe.close(resolve));});
await mkdir(temporaryRoot,{recursive:true});const work=await mkdtemp(resolve(temporaryRoot,'run-'));
const output=resolve('docs/screenshots-v2');await mkdir(output,{recursive:true});
const logs=openSync(resolve(work,'server.log'),'w');
const env=Object.fromEntries(['PATH','Path','SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP','LOCALAPPDATA','APPDATA'].filter(key=>process.env[key]).map(key=>[key,process.env[key]]));
Object.assign(env,{PYTHONIOENCODING:'utf-8',CLOUD_ANALYSIS_ENABLED:'false'});
const server=spawn(resolve('.venv/Scripts/python.exe'),['scripts/scoring-stack-fixture-server.py','--work-dir',work,'--port',String(port)],{cwd:root,env,stdio:['ignore',logs,logs],windowsHide:true});
closeSync(logs);
let browser;const checks=[],errors=[],applications=[],outsideRequests=[],dialogs=[],transitions=[],manualAnalysisRequests=[];let fixture=null,temporaryServerStopped=false,providerErrors='';
const pass=message=>{checks.push(message);console.log('PASS '+message);};
async function waitForService(){for(let i=0;i<60;i++){if(server.exitCode!==null)throw new Error('Fixture server exited: '+await readFile(resolve(work,'server.log'),'utf8'));try{const response=await fetch(base+'/api/health');if(response.ok&&await readFile(resolve(work,'manifest.json'),'utf8'))return;}catch{}await new Promise(resolve=>setTimeout(resolve,250));}throw new Error('Isolated fixture server did not become ready');}
async function waitForFixture(predicate,description){for(let i=0;i<180;i++){fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));if(predicate(fixture))return;await new Promise(resolve=>setTimeout(resolve,100));}throw new Error(description+': '+JSON.stringify(fixture));}
async function stopServer(){if(server.exitCode===null){if(process.platform==='win32'){execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}else server.kill();await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);if(server.exitCode===null)server.kill('SIGKILL');}for(let i=0;i<12;i++){try{await fetch(base+'/api/health');}catch{temporaryServerStopped=true;return;}await new Promise(resolve=>setTimeout(resolve,100));}}
try{
 await waitForService();const cases=JSON.parse(await readFile(resolve(work,'manifest.json'),'utf8'));
 fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));assert.equal(fixture.mode,'ISOLATED_STACK');assert.equal(fixture.configuration_reads_private_keys,false);
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run']});
 const context=await browser.newContext({viewport:{width:1450,height:1080}}),page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
 page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.dismiss();});
 page.on('request',request=>{const url=new URL(request.url());if(['PUT','POST'].includes(request.method())&&/\/api\/applications\/[^/]+(?:\/submit)?$/.test(url.pathname))transitions.push({path:url.pathname,method:request.method(),body:request.postDataJSON()});if(request.method()==='POST'&&(/\/api\/analysis-jobs\/bulk$/.test(url.pathname)||/\/api\/applications\/[^/]+\/jobs$/.test(url.pathname)))manualAnalysisRequests.push({path:url.pathname,body:request.postDataJSON()});});
 await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin===base||['data:','blob:'].includes(url.protocol))return route.continue();outsideRequests.push(url.origin);return route.abort();});
 await page.goto(base);await page.locator('#username').fill('student');await page.locator('#password').fill('Demo@2026');await page.locator('#login-form button[type=submit]').click();await page.locator('.sidebar').waitFor();
 const integration=await page.evaluate(()=>api('/integrations/status'));assert.equal(integration.deepseek_model,'ISOLATED_STACK_ALWAYS_99-'+work.split(/[\\/]/).at(-1));pass('仅连接独立8767端口，正常HTTP登录；配置不读真实私钥，出网硬阻断');
 await page.locator('.sidebar [data-nav="applications"]').click();await page.locator('.category-entry').first().waitFor();
 const categories=await page.evaluate(()=>S.boot.categories.map(value=>({id:value.id,code:value.code})));
 for(const testCase of cases){
  const category=categories.find(value=>value.code===testCase.category||(testCase.category==='POLITICAL'&&value.code==='IDEOLOGY'));assert.ok(category,'Fixture category '+testCase.category);
  await page.locator(`.category-entry[data-category="${category.id}"]`).click();await page.locator('#organizer-main-files').setInputFiles(testCase.path);await page.locator('.organizer-row').waitFor();
  await page.locator('#organizer-confirm').click();await page.locator('.modal').waitFor({state:'detached'});
  const batch=await page.evaluate(()=>S.uploadBatches.at(-1).id);await page.waitForFunction(id=>['COMPLETED','FAILED'].includes(S.uploadBatches.find(value=>value.id===id)?.state),batch,{timeout:30000});
  const uploaded=await page.evaluate(id=>{const b=S.uploadBatches.find(value=>value.id===id);return {state:b.state,id:b.items[0].applicationId,group:b.submissionId,error:b.error||b.items[0].error};},batch);
  assert.equal(uploaded.state,'COMPLETED',uploaded.error);applications.push({...testCase,applicationId:uploaded.id,groupId:uploaded.group});
 }
 pass('9份虚构PNG经真实上传整理和后台上传，七类及两个缺本人案例进入真实OCR任务队列');
 await page.locator('.sidebar [data-nav="members"]').click();
 await waitForFixture(value=>value.active_ocr_files===3,'Three automatic OCR file workers never became active');
 let queuedJobs=await page.evaluate(async()=>{const response=await api('/analysis-jobs');return Array.isArray(response)?response:response.jobs;});assert.equal(queuedJobs.filter(job=>job.kind==='OCR'&&job.state==='RUNNING').length,3);assert.equal(queuedJobs.filter(job=>job.kind==='OCR'&&job.state==='QUEUED').length,cases.length-3);assert.equal(queuedJobs.filter(job=>job.kind==='AI').length,0);assert.deepEqual(manualAnalysisRequests,[]);pass('确认上传即自动排OCR，真实后台最多同时处理3文件，其余排队，整个流程未请求手动分析');
 await writeFile(resolve(work,'open-ocr-gate'),'offline fixture gate only');
 await waitForFixture(value=>value.active_ai_items===3&&value.fake_general===cases.length&&value.fake_seal===cases.length,'AI saturation or independent OCR progression did not complete');
 // A provider returning is earlier than its transaction committing; observe the
 // actual durable job state rather than racing the final OCR persistence.
 for(let attempt=0;attempt<100;attempt++){queuedJobs=await page.evaluate(async()=>{const response=await api('/analysis-jobs');return Array.isArray(response)?response:response.jobs;});if(queuedJobs.filter(job=>job.kind==='OCR').every(job=>job.state==='SUCCEEDED'))break;await page.waitForTimeout(100);}
 assert.equal(queuedJobs.filter(job=>job.kind==='AI'&&job.state==='RUNNING').length,3);assert.ok(queuedJobs.some(job=>job.kind==='AI'&&job.state==='QUEUED'));assert.ok(queuedJobs.filter(job=>job.kind==='OCR').every(job=>job.state==='SUCCEEDED'),JSON.stringify(queuedJobs.filter(job=>job.kind==='OCR').map(({id,state,error_message})=>({id,state,error_message}))));assert.equal(fixture.fake_model,0);pass('OCR完成后自动衔接AI，3个AI小项并发满额时其余排队，独立OCR池继续完成所有文件');
 await writeFile(resolve(work,'open-ai-gate'),'offline fixture gate only');
 let jobs=[],allCompleted=false;
 for(let attempt=0;attempt<180;attempt++){jobs=await page.evaluate(async()=>{const response=await api('/analysis-jobs');return Array.isArray(response)?response:response.jobs;});allCompleted=applications.every(item=>jobs.some(job=>job.application_id===item.applicationId&&job.kind==='AI'&&['SUCCEEDED','FAILED','STALE'].includes(job.state)));if(allCompleted)break;await page.waitForTimeout(350);}
 assert.ok(allCompleted,'Real background worker did not finish every fixture job');
 for(const item of applications){const automatic=jobs.filter(value=>value.application_id===item.applicationId&&value.kind==='AI');assert.equal(automatic.length,1);const job=automatic[0];assert.equal(job.state,'SUCCEEDED',JSON.stringify(job));assert.equal(job.result_json.automatic,true);assert.equal(jobs.find(value=>value.id===job.wait_for_ocr_job_id)?.kind,'OCR');}
 assert.deepEqual(manualAnalysisRequests,[]);pass('无需手动分析，切换站内页面后真实后台仍每项仅自动完成一次OCR→AI，固定错误模型99由政策层替换或标待核定');
 for(const testCase of applications){
  const detail=await page.evaluate(id=>api('/applications/'+id),testCase.applicationId);testCase.actualScore=detail.requested_score;testCase.resolution=detail.score_resolution;
  await page.evaluate(id=>openApplicationWorkspace(id),testCase.applicationId);await page.locator('#workspace-item-form').waitFor();
  const matchable=['RESEARCH','ARTS','POST'].includes(testCase.category);
  assert.equal(await page.locator('#workspace-result-label').count(),Number(matchable));assert.equal(await page.locator('#workspace-activity-search').count(),Number(matchable));
  const names=await page.locator('#workspace-item-form').evaluate(form=>Array.from(form.elements).map(field=>field.name).filter(Boolean));assert.deepEqual(names.sort(),(matchable?['title','requested_score','result_label','activity_id','note']:['title','requested_score','note']).sort());
  assert.equal(await page.locator('[data-action="v2-confirm-score"]').count(),0);
  const cell=page.locator(`[data-item-score="${testCase.applicationId}"]`),status=page.locator('#workspace-score-resolution');
  if(testCase.score!==null){assert.equal(Number(detail.requested_score).toFixed(2),testCase.score);assert.equal(detail.score_resolution.status,'RULE_APPLIED');assert.equal(await page.locator('#workspace-score').inputValue(),testCase.score);assert.equal(await cell.locator('.score-num').innerText(),testCase.score);assert.ok(!/待核定分值/.test(await cell.innerText()));pass(`${testCase.category}真实API和对照表格均为${testCase.score}分，模型99未透传`);}
  else {assert.equal(detail.score_resolution.status,'NEEDS_REVIEW');assert.equal(detail.score_resolution.value,null);assert.ok(detail.score_resolution.missing_facts.length>0);assert.match(await cell.innerText(),/待核定分值/);assert.match(await status.innerText(),/本人|计分依据|等级|记录/);await status.scrollIntoViewIfNeeded();await page.screenshot({path:resolve(output,'scoring-stack-isolated.png'),animations:'disabled'});pass('无本人材料在真实API及页面均标待核定、给具体原因，保留0不冒充已计分');}
  if(testCase.matched_activity_id){assert.equal(detail.revision.activity_id,testCase.matched_activity_id);assert.equal(detail.eligibility.decision,'VALID');assert.equal(await page.locator('#workspace-activity').inputValue(),testCase.matched_activity_id);await page.waitForFunction(()=>document.querySelector('#workspace-activity-status')?.textContent.includes('已认定有效'));assert.match(await page.locator('#workspace-result-label').inputValue(),/二等|三等|良好/);}
 }
 pass('全部七类只展示核心输入，只有科研、文体和任职可填等级与搜索活动；三类真实AI均匹配到有效库');
 const arts=applications.find(item=>item.category==='ARTS');await page.evaluate(id=>openApplicationWorkspace(id),arts.applicationId);await page.locator('#workspace-item-form').waitFor();
 await page.locator('#workspace-activity-search').fill('星光');await page.locator('#workspace-activity-options [role="option"]').filter({hasText:'虚构校园星光体验活动无效认定'}).click();assert.equal(await page.locator('#workspace-activity').inputValue(),arts.invalid_activity_id);assert.match(await page.locator('#workspace-activity-status').innerText(),/已认定无效/);
 await page.locator('#workspace-activity-search').fill(arts.title);await page.locator('#workspace-activity-options [role="option"]').filter({hasText:arts.title}).click();assert.equal(await page.locator('#workspace-activity').inputValue(),arts.matched_activity_id);assert.match(await page.locator('#workspace-activity-status').innerText(),/已认定有效/);await page.evaluate(()=>saveWorkspaceItem());assert.equal((await page.evaluate(id=>api('/applications/'+id),arts.applicationId)).revision.activity_id,arts.matched_activity_id);pass('真实活动搜索返回有效和无效认定，选择状态清楚显示，改回有效匹配后直接保存');
 const [savedPending,directPending]=applications.filter(item=>item.score===null);
 await page.evaluate(id=>openApplicationWorkspace(id),savedPending.applicationId);await page.locator('#workspace-item-form').waitFor();assert.equal(Number(await page.locator('#workspace-score').inputValue()),0);
 const [saving]=await Promise.all([page.waitForResponse(response=>response.url()===base+'/api/applications/'+savedPending.applicationId&&response.request().method()==='PUT'),page.locator('[data-action="v2-save-item"]').click()]);assert.equal(saving.status(),200);
 await page.reload();await page.locator('.sidebar').waitFor();await page.evaluate(id=>openApplicationWorkspace(id),savedPending.applicationId);await page.locator('#workspace-item-form').waitFor();const manual=await page.evaluate(id=>api('/applications/'+id),savedPending.applicationId);assert.equal(manual.score_resolution.status,'MANUAL');assert.equal(manual.score_resolution.value,'0.00');assert.equal(Number(await page.locator('#workspace-score').inputValue()),0);const saved=transitions.filter(item=>item.method==='PUT'&&item.path.endsWith(savedPending.applicationId)).at(-1);assert.equal(saved.body.score_confirmed,true);assert.ok(Number.isInteger(saved.body.expected_version));pass('普通保存即核定当前0分，真实HTTP和刷新持续保留，无需额外确认动作');
 await page.evaluate(id=>openApplicationWorkspace(id),directPending.applicationId);await page.locator('#workspace-item-form').waitFor();const before=await page.evaluate(id=>api('/applications/'+id),directPending.applicationId);assert.equal(before.score_resolution.status,'NEEDS_REVIEW');assert.equal(before.score_resolution.value,null);assert.equal(await page.locator('.modal').count(),0);
 const [submission]=await Promise.all([page.waitForResponse(response=>response.url()===base+'/api/applications/'+directPending.applicationId+'/submit'&&response.request().method()==='POST'),page.locator('[data-action="v2-submit-item"]').click()]);assert.equal(submission.status(),200);const submitted=await submission.json();assert.equal(submitted.state,'PENDING_PEER');assert.equal(submitted.score_resolution.value,'0.00');assert.ok(submitted.revision.content_frozen_at);assert.equal(submitted.revision.payload_json._score_confirmation.via,'SUBMIT');assert.equal(submitted.revision.payload_json._score_confirmation.value,'0.00');assert.equal(await page.locator('.modal').count(),0);const directWrites=transitions.filter(item=>item.path.includes(directPending.applicationId));assert.equal(directWrites.filter(item=>item.method==='PUT').length,0);assert.equal(directWrites.filter(item=>item.path.endsWith('/submit')).length,1);assert.equal(directWrites[0].body.expected_version,before.lock_version);assert.equal(directWrites[0].body.score_confirmed,true);assert.deepEqual(dialogs,[]);pass('未核定默认0可直接点击提交，单次POST带版本且冻结0分，没有先保存、确认弹窗或浏览器确认');
 const groups=await page.evaluate(()=>api('/submissions'));const list=Array.isArray(groups)?groups:groups.submissions;for(const item of applications){const entry=list.flatMap(group=>group.items).find(value=>value.id===item.applicationId);assert.ok(entry);assert.equal(Number(entry.requested_score).toFixed(2),item.score??'0.00');}
 fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));assert.equal(fixture.fake_model,cases.length);assert.equal(fixture.fake_general,cases.length);assert.equal(fixture.fake_seal,cases.length);assert.equal(fixture.peak_ocr_files,3);assert.equal(fixture.peak_ai_items,3);assert.equal(fixture.active_ocr_files,0);assert.equal(fixture.active_ai_items,0);assert.equal(fixture.actual_cloud_calls,0);assert.equal(fixture.network_blocked,0);assert.deepEqual(outsideRequests,[]);assert.deepEqual(errors,[]);pass('真实submissions与详情、表格分值一致，9次受控模型/文字/印章调用，3/3并发上限全程保持，实际云调用0');
}catch(error){errors.push(error.stack||String(error));console.error(error);throw error;}
finally{
 if(browser)await browser.close();await stopServer();
 try{fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));}catch{}
 try{providerErrors=await readFile(resolve(work,'provider-errors.log'),'utf8');}catch{}
 await writeFile(resolve(output,'browser-scoring-stack-isolated.json'),JSON.stringify({mode:'ISOLATED_STACK',source:'真实HTTP、独立SQLite、原生后台worker自动OCR→AI和浏览器；受控离线provider闸门仅观察真实并发，不模拟调度，不接触生产8000或真实数据',checks,errors,applications:applications.map(({path,...value})=>value),fixture,provider_errors:providerErrors,external_requests:outsideRequests,manual_analysis_requests:manualAnalysisRequests,dialogs,transitions,temporary_server_stopped:temporaryServerStopped,actual_cloud_calls:0,actual_business_writes:0,createdAt:new Date().toISOString()},null,2));
}
