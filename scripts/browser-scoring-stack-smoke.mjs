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
let browser;const checks=[],errors=[],applications=[],outsideRequests=[];let fixture=null,temporaryServerStopped=false;
const pass=message=>{checks.push(message);console.log('PASS '+message);};
async function waitForService(){for(let i=0;i<60;i++){if(server.exitCode!==null)throw new Error('Fixture server exited: '+await readFile(resolve(work,'server.log'),'utf8'));try{const response=await fetch(base+'/api/health');if(response.ok&&await readFile(resolve(work,'manifest.json'),'utf8'))return;}catch{}await new Promise(resolve=>setTimeout(resolve,250));}throw new Error('Isolated fixture server did not become ready');}
async function stopServer(){if(server.exitCode===null){if(process.platform==='win32'){execFileSync('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}else server.kill();await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);if(server.exitCode===null)server.kill('SIGKILL');}for(let i=0;i<12;i++){try{await fetch(base+'/api/health');}catch{temporaryServerStopped=true;return;}await new Promise(resolve=>setTimeout(resolve,100));}}
try{
 await waitForService();const cases=JSON.parse(await readFile(resolve(work,'manifest.json'),'utf8'));
 fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));assert.equal(fixture.mode,'ISOLATED_STACK');assert.equal(fixture.configuration_reads_private_keys,false);
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run']});
 const context=await browser.newContext({viewport:{width:1450,height:1080}}),page=await context.newPage();
 page.on('pageerror',error=>errors.push(error.message));
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
 pass('8份虚构PNG经真实上传整理和后台上传，七类及缺本人案例进入真实OCR任务队列');
 const [bulkResponse]=await Promise.all([page.waitForResponse(response=>response.url()===base+'/api/analysis-jobs/bulk'&&response.request().method()==='POST'),page.locator('[data-action="v2-bulk-analyze"]:not([data-id])').click()]);assert.equal(bulkResponse.status(),200);
 await page.locator('.sidebar [data-nav="members"]').click();
 let jobs=[],allCompleted=false;
 for(let attempt=0;attempt<180;attempt++){jobs=await page.evaluate(async()=>{const response=await api('/analysis-jobs');return Array.isArray(response)?response:response.jobs;});allCompleted=applications.every(item=>jobs.some(job=>job.application_id===item.applicationId&&job.kind==='AI'&&['SUCCEEDED','FAILED','STALE'].includes(job.state)));if(allCompleted)break;await page.waitForTimeout(350);}
 assert.ok(allCompleted,'Real background worker did not finish every fixture job');
 for(const item of applications){const job=jobs.find(value=>value.application_id===item.applicationId&&value.kind==='AI');assert.equal(job.state,'SUCCEEDED',JSON.stringify(job));}
 pass('真实后台队列在切换站内页面后继续完成OCR和AI，固定错误模型99由政策层替换或标待核定');
 for(const testCase of applications){
  const detail=await page.evaluate(id=>api('/applications/'+id),testCase.applicationId);testCase.actualScore=detail.requested_score;testCase.resolution=detail.score_resolution;
  await page.evaluate(id=>openApplicationWorkspace(id),testCase.applicationId);await page.locator('#workspace-item-form').waitFor();
  const cell=page.locator(`[data-item-score="${testCase.applicationId}"]`),status=page.locator('#workspace-score-resolution');
  if(testCase.score!==null){assert.equal(Number(detail.requested_score).toFixed(2),testCase.score);assert.equal(detail.score_resolution.status,'RULE_APPLIED');assert.equal(await page.locator('#workspace-score').inputValue(),testCase.score);assert.equal(await cell.locator('.score-num').innerText(),testCase.score);assert.ok(!/待核定分值/.test(await cell.innerText()));pass(`${testCase.category}真实API和对照表格均为${testCase.score}分，模型99未透传`);}
  else {assert.equal(detail.score_resolution.status,'NEEDS_REVIEW');assert.equal(detail.score_resolution.value,null);assert.ok(detail.score_resolution.missing_facts.length>0);assert.match(await cell.innerText(),/待核定分值/);assert.match(await status.innerText(),/本人|计分依据|等级|记录/);await status.scrollIntoViewIfNeeded();await page.screenshot({path:resolve(output,'scoring-stack-isolated.png'),animations:'disabled'});pass('无本人材料在真实API及页面均标待核定、给具体原因，保留0不冒充已计分');}
 }
 const pending=applications.find(item=>item.score===null);
 const blocked=await page.evaluate(async id=>{try{await api('/applications/'+id+'/submit',{method:'POST',body:{}});return {unexpected:true};}catch(error){return {status:error.status,message:error.message};}},pending.applicationId);assert.equal(blocked.status,400);assert.match(blocked.message,/分值尚未核定/);pass('后端拒绝提交未核定0分，页面提示和API保护一致');
 assert.equal(Number(await page.locator('#workspace-score').inputValue()),0);const [saving]=await Promise.all([page.waitForResponse(response=>response.url()===base+'/api/applications/'+pending.applicationId&&response.request().method()==='PUT'),page.locator('[data-action="v2-confirm-score"]').click()]);assert.equal(saving.status(),200);
 await page.reload();await page.locator('.sidebar').waitFor();await page.evaluate(id=>openApplicationWorkspace(id),pending.applicationId);await page.locator('#workspace-item-form').waitFor();const manual=await page.evaluate(id=>api('/applications/'+id),pending.applicationId);assert.equal(manual.score_resolution.status,'MANUAL');assert.equal(manual.score_resolution.value,'0.00');assert.equal(Number(await page.locator('#workspace-score').inputValue()),0);assert.equal(await page.locator('[data-action="v2-confirm-score"]').count(),0);pass('浏览器明确确认人工0分后，真实保存与刷新持续显示已核定0，无需理由');
 const submitted=await page.evaluate(id=>api('/applications/'+id+'/submit',{method:'POST',body:{}}),pending.applicationId);assert.equal(submitted.state,'PENDING_PEER');assert.equal(submitted.score_resolution.value,'0.00');pass('人工确认0分可正常提交到真实隔离审核流程，冻结后仍为0');
 const groups=await page.evaluate(()=>api('/submissions'));const list=Array.isArray(groups)?groups:groups.submissions;for(const item of applications){const entry=list.flatMap(group=>group.items).find(value=>value.id===item.applicationId);assert.ok(entry);assert.equal(Number(entry.requested_score).toFixed(2),item.score??'0.00');}
 fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));assert.equal(fixture.fake_model,8);assert.equal(fixture.fake_general,8);assert.equal(fixture.fake_seal,8);assert.equal(fixture.actual_cloud_calls,0);assert.equal(fixture.network_blocked,0);assert.deepEqual(outsideRequests,[]);assert.deepEqual(errors,[]);pass('真实submissions与详情、表格分值一致，8次受控模型/文字/印章调用，实际云调用0');
}catch(error){errors.push(error.stack||String(error));console.error(error);throw error;}
finally{
 if(browser)await browser.close();await stopServer();
 try{fixture=JSON.parse(await readFile(resolve(work,'fixture-state.json'),'utf8'));}catch{}
 await writeFile(resolve(output,'browser-scoring-stack-isolated.json'),JSON.stringify({mode:'ISOLATED_STACK',source:'真实HTTP、独立SQLite、原生后台worker和浏览器；全部材料/账号为演示，OCR与模型为受控离线替身，不接触生产8000或真实数据',checks,errors,applications:applications.map(({path,...value})=>value),fixture,external_requests:outsideRequests,temporary_server_stopped:temporaryServerStopped,actual_cloud_calls:0,actual_business_writes:0,createdAt:new Date().toISOString()},null,2));
}
