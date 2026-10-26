/** Synthetic admin UI only. All APIs intercepted; no real account writes. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000',output=resolve('docs/screenshots-v2');
const admin={id:'delete-ui-admin',username:'demo-manager',display_name:'合成管理员',base_identity:'ADMIN',status:'ACTIVE',roles:[{code:'ADMIN'}]};
const student={id:'delete-ui-student',username:'recording-demo',display_name:'合成录制同学',base_identity:'STUDENT',student_number:'SYNTHETIC-001',status:'ACTIVE',roles:[{code:'STUDENT'}],class_ids:['delete-ui-class']};
const counselor={id:'delete-ui-counselor',username:'demo-advisor',display_name:'合成辅导员',base_identity:'COUNSELOR',status:'ACTIVE',roles:[{code:'COUNSELOR'}]};
let accounts=[admin,student,counselor],busy=true;
const checks=[],errors=[],deletions=[];
const pass=value=>{checks.push(value);console.log('PASS '+value);};
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.on('pageerror',error=>errors.push(error.message));
page.on('dialog',async dialog=>{errors.push('Unexpected native confirmation: '+dialog.message());await dialog.dismiss();});
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
  const json=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  if(method==='GET'){
    if(path==='/api/session')return json({user:admin,csrf_token:'synthetic-csrf'});
    if(path==='/api/bootstrap')return json({user:admin,years:[{id:'delete-ui-year',name:'合成演示学年'}],active_year:{id:'delete-ui-year',name:'合成演示学年'},classes:[{id:'delete-ui-class',name:'合成演示班'}],categories:[],permissions:['admin.users','admin.classes'],demo_accounts:[]});
    if(path==='/api/admin/users')return json(accounts);
    if(path==='/api/dashboard')return json({stats:{users:accounts.length,classes:1},recent:[]});
    if(path==='/api/analysis-jobs')return json({jobs:[]});
  }
  if(method==='DELETE'&&path==='/api/admin/users/'+student.id){
    deletions.push({id:student.id,csrf:request.headers()['x-csrf-token']});
    if(busy){busy=false;return json({detail:'已暂停该学生的后台任务；正在等待已发出的请求结束，请稍后再次点击删除'},409);}
    accounts=accounts.filter(account=>account.id!==student.id);
    return json({ok:true,preserved_audit:false,backup:'synthetic-backup.db'});
  }
  errors.push('Unexpected mock API '+method+' '+path);
  return json({detail:'Unexpected synthetic request'},404);
});
try{
  await page.goto(base+'/#admin');
  const row=page.locator('tr').filter({hasText:student.display_name});
  await row.locator('[data-action="delete-student-user"]').waitFor();
  assert.equal(await page.locator('[data-action="delete-student-user"]').count(),1);
  for(const name of [admin.display_name,counselor.display_name])assert.equal(await page.locator('tr').filter({hasText:name}).locator('[data-action="delete-student-user"]').count(),0);
  pass('管理员用户列表仅学生行显示删除入口，管理员及辅导员不显示');
  await row.locator('[data-action="delete-student-user"]').click();
  let modal=page.getByRole('dialog',{name:'删除学生账号',exact:true});
  await modal.waitFor();
  assert.match(await modal.innerText(),new RegExp(student.display_name));
  assert.match(await modal.innerText(),new RegExp(student.username));
  assert.match(await modal.innerText(),/登录账号、学号、申报、附件关联及识别任务/);
  assert.equal(await page.getByRole('dialog').count(),1);
  pass('一次确认窗口明确姓名、登录账号及删除范围');
  await modal.getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(deletions.length,0);
  pass('取消删除不发写请求');
  await row.locator('[data-action="edit-user"]').click();
  await page.getByRole('dialog',{name:'编辑系统账号',exact:true}).waitFor();
  await page.locator('.modal footer [data-action="delete-student-user"]').click();
  modal=page.getByRole('dialog',{name:'删除学生账号',exact:true});
  await modal.waitFor();
  assert.equal(await page.getByRole('dialog').count(),1);
  pass('学生编辑弹窗也有删除入口，进入同一个确认窗口');
  await page.locator('#confirm-delete-student-user').click();
  await page.locator('.modal-error').filter({hasText:'已暂停该学生的后台任务'}).waitFor();
  assert.equal(await page.getByRole('dialog').count(),1);
  assert.equal(await row.count(),1);
  assert.equal(deletions.length,1);
  pass('有在途任务时显示已暂停并等待的409说明，账号和同一确认窗口保留');
  await page.locator('#confirm-delete-student-user').click();
  await page.waitForFunction(()=>!document.querySelector('.modal'));
  await page.waitForFunction(name=>!document.querySelector('#admin-content')?.textContent.includes(name),student.username);
  assert.equal(deletions.length,2);
  assert.equal(deletions[1].csrf,'synthetic-csrf');
  pass('等待后再次删除发送带CSRF的DELETE，完成后关闭窗口并更新账号列表');
  for(const person of [admin,counselor]){
    await page.locator('tr').filter({hasText:person.display_name}).locator('[data-action="edit-user"]').click();
    assert.equal(await page.locator('.modal [data-action="delete-student-user"]').count(),0);
    await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click();
  }
  pass('管理员及辅导员编辑窗口也不提供删除学生操作');
  await page.evaluate(target=>{S.user={id:'student-session',base_identity:'STUDENT'};deleteStudentUserModal(target);},student);
  assert.equal(await page.getByRole('dialog').count(),0);
  pass('学生身份无法通过直接调用前端函数打开删除窗口');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{
  await mkdir(output,{recursive:true});
  await writeFile(resolve(output,'browser-admin-student-delete-mock.json'),JSON.stringify({source:'All API calls mocked; no real accounts, files or cloud modified',checks,errors,deletions,createdAt:new Date().toISOString()},null,2));
  await browser.close();
}
