/** Focused Q38 check, reusing an existing manually edited item. No uploads. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const extraPath=resolve('docs/screenshots-v2/browser-v2-followup.json'),extra=JSON.parse(await readFile(extraPath,'utf8'));
const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.goto(base);await page.locator('#username').fill('student');await page.locator('#password').fill('Demo@2026');await page.locator('#login-form button[type=submit]').click();await page.locator('.sidebar').waitFor();const config=await (await page.request.get(base+'/api/integrations/status')).json();if(config.enabled)throw new Error('聚焦验收应关闭云调用。');
 const boot=await (await page.request.get(base+'/api/bootstrap')).json();if(boot.active_year.code!=='2025-2026')throw new Error('当前学年未保持验收初始值');
 await page.locator('.sidebar [data-nav="applications"]').click();const group=page.locator(`[data-group-id="${extra.groupId}"]`);await group.waitFor();if(!await group.locator('.items-table').count())await group.locator('[data-action="v2-toggle-group"]').first().click();await page.locator(`[data-action="v2-select-item"][data-id="${extra.newId}"]`).click();await page.locator('#workspace-title').waitFor();const oldTitle=await page.locator('#workspace-title').inputValue(),oldScore=await page.locator('#workspace-score').inputValue();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/analysis-jobs/bulk')&&r.request().method()==='POST');await page.locator(`[data-action="v2-bulk-analyze"][data-id="${extra.groupId}"]`).click();const result=await (await response).json();const job=result.jobs.find(j=>j.application_id===extra.newId);if(!job)throw new Error('未分析人工稿首次建议未安排任务');if(job.result_json?.auto_apply!==false)throw new Error('人工稿任务允许自动覆盖');
 for(let i=0;i<15;i++){const status=await (await page.request.get(base+'/api/analysis-jobs/'+job.id)).json();if(!['QUEUED','RUNNING'].includes(status.state))break;await page.waitForTimeout(500);}
 await page.locator('.loading-panel').waitFor({state:'detached'});await page.locator('#workspace-title').waitFor();if(await page.locator('#workspace-title').inputValue()!==oldTitle||Number(await page.locator('#workspace-score').inputValue())!==Number(oldScore))throw new Error('后台分析覆盖了人工名称或分值');if(errors.length)throw new Error(errors.join('; '));
 const wording='人工稿首次分析仅生成建议，保留手填名称与计分';extra.checks=extra.checks.map(t=>t.includes('人工修改保存后')?wording:t);extra.manualSuggestionVerified={job_id:job.id,auto_apply:false,title:oldTitle,score:oldScore,active_year:boot.active_year.code,cloudCallsEnabled:false};await writeFile(extraPath,JSON.stringify(extra,null,2));
 const mainPath=resolve('docs/screenshots-v2/browser-v2-smoke.json'),main=JSON.parse(await readFile(mainPath,'utf8'));main.additionalChecks=main.additionalChecks.map(t=>t.includes('人工修改保存后')?wording:t);main.manualSuggestionReport='browser-v2-followup.json';await writeFile(mainPath,JSON.stringify(main,null,2));console.log('PASS '+wording);console.log('PASS 当前统计学年保持初始2025-2026；本测试未改动公示');
}finally{await browser.close();}
