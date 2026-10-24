/** Additional real-UI form contract checks; never calls cloud recognition. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();const checks=[],issues=[];
page.on('pageerror',e=>issues.push(e.message));
async function login(name){await page.goto(base);await page.locator('#login-form').waitFor();await page.locator('#username').fill(name);await page.locator('#password').fill('Demo@2026');await page.locator('#login-form button[type=submit]').click();await page.locator('.sidebar').waitFor();await page.locator('.loading-panel').waitFor({state:'detached'});}
async function nav(name){await page.locator(`.sidebar [data-nav="${name}"]`).click();await page.locator('.loading-panel').waitFor({state:'detached'});}
async function shot(name){await page.locator('#toast-root').evaluate(el=>el.replaceChildren());await page.screenshot({path:resolve('docs/screenshots',name+'.png'),fullPage:true,animations:'disabled'});}
const pass=text=>{checks.push(text);console.log('PASS '+text);};
try{
  await login('counselor');await nav('activities');
  const canonical='浏览器验收活动（虚构）';let card=page.locator('.activity-card').filter({hasText:canonical});
  if(await card.count())await card.locator('[data-action="edit-activity"]').click();else await page.locator('[data-action="new-activity"]').click();
  await page.locator('#activity-name').fill(canonical);await page.locator('#activity-alias').fill('浏览器验收赛，验收竞赛');await page.locator('#activity-category').selectOption({label:'学术科研及创新创业'});await page.locator('#activity-decision').selectOption('VALID');await page.locator('#activity-year').selectOption({index:1});await page.locator('#eligibility-edition_key').fill('2025届');await page.locator('#eligibility-track_key').fill('软件赛');await page.locator('#eligibility-group_key').fill('大学A组');await page.locator('#activity-reason').fill('浏览器验收的虚构认定，仅用于检查别名与范围表单。');await page.locator('#save-activity').click();await page.locator('.modal').waitFor({state:'detached'});
  card=page.locator('.activity-card').filter({hasText:canonical});await card.getByText('有效',{exact:true}).waitFor();if(!(await card.innerText()).includes('浏览器验收赛'))throw new Error('别名未显示');if(!(await card.innerText()).includes('大学A组'))throw new Error('范围未显示');pass('辅导员通过页面新增有效活动，别名及学年/届次/赛道/组别正确保留');
  await card.locator('[data-action="edit-activity"]').click();await page.locator('#activity-decision').selectOption('INVALID');await page.locator('#activity-reason').fill('浏览器验收：模拟活动不具备加分资格，保留此前有效版本。');await page.locator('#save-activity').click();await page.locator('.modal').waitFor({state:'detached'});card=page.locator('.activity-card').filter({hasText:canonical});await card.getByText('无效',{exact:true}).waitFor();await shot('13-activity-maintenance');pass('辅导员通过页面修改为无效，范围与别名保留');
  await card.locator('[data-action="activity-impact"]').click();await page.locator('.modal').filter({hasText:'受影响的历史申请'}).waitFor();await page.getByText('没有受影响的历史申请',{exact:true}).waitFor();await page.locator('[data-action="close-modal"]').first().click();pass('活动影响列表可打开，空结果与不自动撤销说明正确');
  await page.locator('.topbar [data-action="logout"]').click();await login('admin');await nav('admin');
  const username='browser_check_student';let row=page.locator('tr').filter({hasText:username});
  if(await row.count()){await row.locator('[data-action="edit-user"]').click();}else{await page.locator('[data-action="new-user"]').click();await page.locator('#user-username').fill(username);await page.locator('#user-password').fill('Demo@2026');}
  await page.locator('#user-display-name').fill('浏览器验收同学');await page.locator('#user-identity').selectOption('STUDENT').catch(()=>{});await page.locator('#user-student-number').fill('TEST-BROWSER-01');await page.locator('#user-class').selectOption({label:'软件2401'});await page.locator('#save-user').click();await page.locator('.modal').waitFor({state:'detached'});row=page.locator('tr').filter({hasText:username});await row.waitFor();if(!(await row.innerText()).includes('软件2401'))throw new Error('新增账号班级未保留');pass('管理员通过页面创建虚构学生账号，账号/学号/班级正确保留');
  await row.locator('[data-action="edit-user"]').click();if(!await page.locator('#user-class').inputValue())throw new Error('编辑学生没有预选班级');await page.locator('#user-display-name').fill('浏览器验收同学（已编辑）');await page.locator('#save-user').click();await page.locator('.modal').waitFor({state:'detached'});row=page.locator('tr').filter({hasText:username});await row.getByText('浏览器验收同学（已编辑）',{exact:true}).waitFor();await shot('14-admin-user-edit');pass('管理员通过页面编辑学生姓名，保留学号及所选学年班级');
  await page.locator('[data-tab="roles"]').click();await page.locator('.matrix-table').waitFor();await page.setViewportSize({width:390,height:844});await shot('12-mobile-admin');
  if(issues.length)throw new Error(issues.join('; '));
}catch(e){issues.push(e.stack||String(e));throw e;}
finally{
  const path=resolve('docs/screenshots/browser-forms-smoke.json');await writeFile(path,JSON.stringify({base,checks,issues,createdAt:new Date().toISOString()},null,2));
  if(!issues.length){const summaryPath=resolve('docs/screenshots/browser-smoke.json');const prior=JSON.parse(await readFile(summaryPath,'utf8'));prior.additionalChecks=checks;prior.totalChecks=prior.checks.length+checks.length;prior.additionalReport='browser-forms-smoke.json';await writeFile(summaryPath,JSON.stringify(prior,null,2));}
  await browser.close();
}
