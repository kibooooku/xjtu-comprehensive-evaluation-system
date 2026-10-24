/** Real-browser acceptance check. Uses the local, already seeded demo server.
 * Does not invoke OCR/AI. npm install --prefix .tools/browser playwright first.
 */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const base=process.env.TEST_BASE_URL || 'http://127.0.0.1:8000';
const shots=resolve('docs/screenshots');await mkdir(shots,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},deviceScaleFactor:1});
const page=await context.newPage();
const issues=[],checks=[];
let applicationId=null;
page.on('pageerror',err=>issues.push(err.message));
const screenshot=async name=>{await page.locator('#toast-root').evaluate(el=>el.replaceChildren()).catch(()=>{});await page.waitForTimeout(100);return page.screenshot({path:resolve(shots,name+'.png'),fullPage:true,animations:'disabled'});};
const ok=message=>{checks.push(message);console.log('PASS '+message);};
const nav=async name=>{await page.locator(`.sidebar [data-nav="${name}"]`).click();await page.locator('.loading-panel').waitFor({state:'detached'});};
const login=async username=>{await page.goto(base);await page.locator('#login-form').waitFor();await page.locator('#username').fill(username);await page.locator('#password').fill('Demo@2026');await page.locator('#login-form button[type=submit]').click();await page.locator('.sidebar').waitFor();await page.locator('.loading-panel').waitFor({state:'detached'});if(await page.locator('.error-page').count())throw new Error(await page.locator('.error-page').innerText());};
const logout=async()=>{await page.locator('.topbar [data-action="logout"]').click();await page.locator('#login-form').waitFor();};
try{
  // Reuse this check's demo claim on subsequent runs instead of duplicating points.
  const preparation=await browser.newContext();
  const teacher=(await preparation.request.post(base+'/api/login',{data:{username:'counselor',password:'Demo@2026'}}));
  const teacherSession=await teacher.json();const teacherBoot=await (await preparation.request.get(base+'/api/bootstrap')).json();
  const activeClass=teacherBoot.classes.find(c=>c.name==='软件2401')||teacherBoot.classes[0];const activeYear=teacherBoot.years[0];
  const grade=await (await preparation.request.get(base+'/api/scores',{params:{class_id:activeClass.id,year_id:activeYear.id}})).json();
  if(grade.publicity?.is_open)await preparation.request.put(base+'/api/publicity',{headers:{'X-CSRF-Token':teacherSession.csrf_token},data:{class_id:activeClass.id,year_id:activeYear.id,is_open:false}});
  const applicantContext=await browser.newContext();const studentSession=await (await applicantContext.request.post(base+'/api/login',{data:{username:'student',password:'Demo@2026'}})).json();
  const old=await (await applicantContext.request.get(base+'/api/applications',{params:{year_id:activeYear.id,class_id:activeClass.id,scope:'mine'}})).json();
  const existing=old.find(a=>a.title==='迎新集体活动（2次）');
  if(existing){applicationId=existing.id;if(existing.state==='APPROVED'){const response=await preparation.request.post(base+`/api/applications/${existing.id}/review`,{headers:{'X-CSRF-Token':teacherSession.csrf_token},data:{action:'REJECT',reason:'OTHER',comment:'浏览器重复验收：先按正常流程驳回后重新提交',expected_version:existing.lock_version,idempotency_key:crypto.randomUUID()}});if(!response.ok())throw new Error('复验准备驳回失败：'+await response.text());}else if(['PENDING_PEER','PENDING_COUNSELOR'].includes(existing.state)){await applicantContext.request.post(base+`/api/applications/${existing.id}/withdraw`,{headers:{'X-CSRF-Token':studentSession.csrf_token},data:{expected_version:existing.lock_version}});}}
  await preparation.close();await applicantContext.close();
  await page.goto(base);await page.locator('#login-form').waitFor();await page.locator('[data-demo="student"]').waitFor();await screenshot('01-login');ok('登录页及演示账号可用');
  await login('student');await screenshot('02-student-dashboard');ok('学生工作台正常显示');
  if(await page.locator('.sidebar [data-nav="classApplications"]').count())throw new Error('普通学生错误显示班级核查入口');
  await nav('applications');await page.locator('.category-entry').first().waitFor();if(await page.locator('.category-entry').count()!==7)throw new Error('应有七类填报入口');await screenshot('03-student-applications');ok('普通学生仅显示本人入口，七类入口齐全');
  if(applicationId){await page.locator(`[data-action="detail"][data-id="${applicationId}"]`).click();}else{await page.locator('.category-entry').first().click();const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/applications')&&r.request().method()==='POST');await page.locator('#create-application').click();applicationId=(await (await responsePromise).json()).id;}await page.locator('#application-form').waitFor();
  while(await page.locator('[data-action="delete-file"]').count()){const deletion=page.waitForResponse(r=>r.url().includes('/files/')&&r.request().method()==='DELETE');await page.locator('[data-action="delete-file"]').first().click();await deletion;await page.locator('.loading-panel').waitFor({state:'detached'});await page.locator('#application-form').waitFor();}
  await page.locator('#app-title').fill('迎新集体活动（2次）');await page.locator('#app-score').fill('0.60');await page.locator('#app-subtype').fill('迎新志愿准备与班级集体活动');await page.locator('#app-note').fill('浏览器验收使用的虚构演示材料，无真实学生数据。');
  const fixture=process.env.TEST_PROOF_FILE || resolve('tests/fixtures/browser-proof.png');
  await page.locator('#attachment-input').setInputFiles(fixture);await page.locator('.page-thumb').first().waitFor();
  for(const kind of ['VALIDITY','PARTICIPATION']){
    await page.locator('.page-thumb').first().click();await page.locator('#crop-canvas').waitFor();await page.waitForFunction(()=>document.querySelector('#crop-canvas')?.width>10);await page.locator('#crop-kind').selectOption(kind);
    const box=await page.locator('#crop-canvas').boundingBox();await page.mouse.move(box.x+box.width*.08,box.y+box.height*.1);await page.mouse.down();await page.mouse.move(box.x+box.width*.92,box.y+box.height*.65,{steps:12});await page.mouse.up();await page.locator('#crop-save').click();await page.locator('.modal').waitFor({state:'detached'});await page.locator('#application-form').waitFor();
  }
  await screenshot('04-application-editor');ok('手填分值、上传、PDF/图片页预览与两类手工证据框选');
  await page.locator('[data-action="submit-application"]').click();await page.locator('#confirm-submit').click();await page.locator('.modal').waitFor({state:'detached'});await page.locator('.page-title').waitFor();await page.locator('.page > .row .tag').filter({hasText:'待学生审查'}).waitFor();ok('正式提交进入学生审查');
  await page.locator('#version-selector').selectOption({index:1});await page.locator('.notice').filter({hasText:'当前为历史内容快照'}).waitFor();if(await page.locator('#application-form input').count())throw new Error('历史内容可被编辑');await page.locator('[data-action="current-version"]').click();await page.locator('#version-selector').waitFor();ok('历史版本可查看且不可编辑');
  await nav('members');await page.locator('.member-card').first().waitFor();await screenshot('05-class-members');ok('班级角色名单正常显示');
  await nav('activities');await page.locator('#activity-list').waitFor();ok('活动资格库可查看');
  await logout();await login('reviewer1');await nav('review');await page.locator(`[data-action="detail"][data-id="${applicationId}"]`).click();await page.locator('[data-action="review-application"]').click();await page.locator('#review-comment').fill('证明材料、参与情况及两次活动分值核对通过。');await page.locator('#confirm-review').click();await page.locator('.modal').waitFor({state:'detached'});await page.locator('.page > .row .tag').filter({hasText:'已通过'}).waitFor();await screenshot('06-review-approved');ok('非本人审查员完成初审，集体活动直接通过');
  await logout();await login('monitor');await nav('members');await page.locator('[data-action="manage-reviewers"]').click();if(await page.locator('input[name=reviewer]:checked').count()<2)throw new Error('审查员不足两人');await page.locator('[data-action="close-modal"]').first().click();await nav('scores');await page.locator('.score-table').waitFor();await screenshot('07-class-scores');ok('班长可管理审查员并核查成绩');
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-action="export"]').click();const download=await downloadPromise;if(!download.suggestedFilename().endsWith('.xlsx'))throw new Error('导出格式不正确');ok('班长可导出Excel');
  await logout();await login('counselor');await nav('activities');await page.locator('[data-action="new-activity"]').waitFor();await screenshot('08-activity-catalog');await nav('publicity');const toggle=page.locator('[data-action="publicity-toggle"]');if(await toggle.getAttribute('data-open')==='true'){await toggle.click();await page.locator('#confirm-publicity').click();await page.locator('.modal').waitFor({state:'detached'});}await page.locator('.publicity-banner').filter({hasText:'正在公示'}).waitFor();await screenshot('09-publicity');ok('辅导员开启公示，成绩汇总正常');
  await logout();await login('student');await nav('publicity');await page.locator('.score-table').waitFor();if(await page.locator('.score-table').innerText().then(t=>/2024000\d/.test(t)))throw new Error('公示展示了学号');await page.locator('tr').filter({hasText:'王欣然'}).locator('[data-action="score-detail"]').click();await page.locator('.modal').filter({hasText:'迎新集体活动（2次）'}).waitFor();await page.locator('[data-action="close-modal"]').first().click();ok('普通学生可查看公示与加分明细，名单不暴露学号');
  await logout();await login('admin');await nav('admin');await page.locator('[data-action="new-user"]').waitFor();await screenshot('10-admin-accounts');await page.locator('[data-tab="classes"]').click();await page.locator('[data-action="class-assignments"]').first().click();await page.locator('#assignment-monitor').waitFor();if(await page.locator('input[name=assigned_reviewer]:checked').count()<2)throw new Error('组织配置审查员显示异常');await page.locator('[data-action="close-modal"]').first().click();await page.locator('[data-tab="roles"]').click();await page.locator('.matrix-table').waitFor();await screenshot('11-rbac-matrix');ok('管理员账号、组织配置及RBAC矩阵正常显示');
  await page.setViewportSize({width:390,height:844});await screenshot('12-mobile-admin');await page.locator('.mobile-menu').click();await page.locator('.sidebar.open').waitFor();await page.keyboard.press('Escape');await page.locator('.topbar [data-action="account"]').click();await page.locator('#account-logout').waitFor();await page.locator('[data-action="close-modal"]').first().click();ok('移动布局、导航与账号退出入口可用');
  if(issues.length)throw new Error('浏览器脚本异常：'+issues.join('; '));
}catch(err){issues.push(err.stack||String(err));await screenshot('failure').catch(()=>{});throw err;}
finally{await writeFile(resolve(shots,'browser-smoke.json'),JSON.stringify({base,checks,issues,createdAt:new Date().toISOString()},null,2));await browser.close();}
