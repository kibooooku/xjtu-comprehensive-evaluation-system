/** Focused rendering check; every API is mocked and all writes are rejected. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';
const user={id:'fallback-user',username:'fallback-user',display_name:'核对提示验收同学',base_identity:'STUDENT',roles:[{code:'STUDENT'}]};
const year={id:'fallback-year',name:'2025-2026学年'},boot={user,years:[year],active_year:year,current_year:year,classes:[{id:'fallback-class',name:'模拟班'}],categories:[{id:'fallback-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'}],permissions:['application.read','application.write'],demo_accounts:[]};
const checks=[],errors=[],writes=[];const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',async route=>{const req=route.request(),path=new URL(req.url()).pathname;if(req.method()!=='GET'){writes.push(path);return route.fulfill({status:400,body:'{}'});}const data=path==='/api/session'?{user,csrf_token:'mock'}:path==='/api/bootstrap'?boot:path==='/api/analysis-jobs'?{jobs:[]}:['/api/submissions','/api/activities'].includes(path)?[]:null;if(data===null)errors.push('Unexpected API '+path);await route.fulfill({status:data===null?400:200,contentType:'application/json',body:JSON.stringify(data)});});
try{
 await page.goto((process.env.TEST_BASE_URL||'http://127.0.0.1:8000')+'/#applications');await page.locator('#submission-groups').waitFor();
 const result=await page.evaluate(()=>{const out=document.createElement('div');out.id='workspace-analysis-suggestion';document.querySelector('#page').append(out);S.workspaceItem={id:'fallback-item',can_edit:true,revision:{requested_score:'0.15'}};
  showWorkspaceSuggestion({result_json:{auto_applied:true,needs_review:true,suggestion:{title:'具体活动名称',requested_score:'9.99'}}});const automatic={text:out.innerText,warning:Boolean(out.querySelector('.notice.warning'))};
  showWorkspaceSuggestion({result_json:{suggestion:{title:'具体活动名称',requested_score:'9.99',score_requires_confirmation:true}}});const retained={text:out.innerText,box:out.querySelector('.selection-box').innerText,warning:Boolean(out.querySelector('.notice.warning'))};
  showWorkspaceSuggestion({result_json:{suggestion:{title:'普通确定建议',requested_score:'0.60'}}});return {automatic,retained,ordinary:out.querySelector('.selection-box').innerText};
 });
 assert.equal(result.automatic.warning,true);assert.match(result.automatic.text,/已补全具体活动名，分值保留当前稿；请核对本人参与、次数和分值/);assert.doesNotMatch(result.automatic.text,/首次分析已填入/);assert.equal(result.retained.warning,true);assert.match(result.retained.box,/当前保留分值（需核对）：0\.15 分/);assert.doesNotMatch(result.retained.box,/建议分值|9\.99/);assert.match(result.ordinary,/建议分值：0\.60 分/);assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);checks.push('空标题回退自动填名与普通建议均醒目标注保留当前0.15分需核对，普通确定建议仍显示建议分值');console.log('PASS '+checks[0]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{const out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});await writeFile(resolve(out,'browser-score-fallback-mock.json'),JSON.stringify({source:'Focused rendering only; all APIs mocked, no real writes or provider calls',checks,errors,writes,createdAt:new Date().toISOString()},null,2));await browser.close();}
