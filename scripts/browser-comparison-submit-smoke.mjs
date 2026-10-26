/** Isolated confirmation flow: all APIs mocked, no business database or cloud writes. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000';
const student={id:'submit-student',username:'submit-student',display_name:'提交验收同学',base_identity:'STUDENT',roles:[{code:'STUDENT'}]};
const current={id:'submit-year',name:'2025-2026学年'},previous={id:'submit-previous-year',name:'2024-2025学年'};
const category={id:'submit-category',code:'COLLECTIVE',name:'集体活动',default_cap:'3.00'};
let title='周末广场舞',score='0.30',version=4;
let scenario={state:'DRAFT',canEdit:true,owner:true,year:current.id,permission:true,role:'STUDENT',submitReject:false,saveReject:false};
const user=()=>({...student,base_identity:scenario.role});
const item=(id='submit-a')=>({id,title:id==='submit-a'?title:'另一个已勾选的小项',requested_score:id==='submit-a'?score:'0.90',state:id==='submit-a'?scenario.state:'DRAFT',category_id:category.id,category_name:category.name,category_code:category.code,student_user_id:scenario.owner?student.id:'another-student',student_name:student.display_name,class_id:'submit-class',class_name:'模拟班',year_id:scenario.year,year_name:scenario.year===current.id?current.name:previous.name,lock_version:version,file_count:1,files:[{id:'submit-file-'+id,original_name:'模拟证明材料.pdf'}],evidence_summary:{has_participation:true,has_activity_name:true,has_activity_time:true,has_seal:true,complete:true}});
const group=()=>({id:'submit-group',category_id:category.id,category_name:category.name,category_code:category.code,year_id:scenario.year,year_name:scenario.year===current.id?current.name:previous.name,class_id:'submit-class',class_name:'模拟班',student_user_id:scenario.owner?student.id:'another-student',student_name:student.display_name,approved_total:'0.00',items:[item(),item('submit-b')]});
const detail=()=>({...item(),can_edit:scenario.canEdit&&scenario.state==='DRAFT',can_review:false,can_withdraw:scenario.state==='PENDING_PEER',revision:{id:'submit-revision',title,requested_score:score,payload_json:{}},files:[],crops:[],history:[],reviews:[],versions:[]});
const boot=()=>({user:user(),years:[current,previous],active_year:current,current_year:current,classes:[{id:'submit-class',name:'模拟班'}],categories:[category],permissions:['application.read','application.write','activity.read','organization.read',...(scenario.permission?['application.submit']:[])],demo_accounts:[]});
const reads=[],writes=[],checks=[],errors=[];
const pass=message=>{checks.push(message);console.log('PASS '+message);};
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1050}});
page.on('pageerror',error=>errors.push(error.message));
await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname,method=request.method();
  const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(method==='GET'){
    reads.push(path);
    if(path==='/api/session')return json({user:user(),csrf_token:'mock-csrf'});
    if(path==='/api/bootstrap')return json(boot());
    if(path==='/api/submissions')return json([group()]);
    if(path==='/api/analysis-jobs')return json({jobs:[]});
    if(path==='/api/activities')return json([]);
    if(path==='/api/applications')return json([item(),item('submit-b')]);
    if(path==='/api/applications/submit-a')return json(detail());
  }else{
    const body=request.postDataJSON();writes.push({method,path,body});
    if(path==='/api/applications/submit-a'&&method==='PUT'){
      if(scenario.saveReject)return json({detail:'模拟保存冲突，请刷新后重试'},409);
      title=body.title;score=body.requested_score;version++;return json(detail());
    }
    if(path==='/api/applications/submit-a/submit'&&method==='POST'){
      assert.equal(body.expected_version,version,'提交必须使用保存后的版本');
      if(scenario.submitReject)return json({detail:'本班本学年正在公示，暂停正式提交'},409);
      scenario.state='PENDING_PEER';scenario.canEdit=false;version++;return json(detail());
    }
  }
  errors.push('Unexpected mock API '+method+' '+path);return json({detail:'Only fixture APIs are allowed'},400);
});
const submissions=()=>writes.filter(write=>write.method==='POST'&&write.path.endsWith('/submit'));
let navigation=0;
const open=async(view='applications')=>{
  await page.goto(base+'/?submit-check='+(++navigation)+'#'+view);await page.locator('[data-action="v2-select-item"][data-id="submit-a"]').click();await page.locator('#workspace-item-form').waitFor();
};
const submit=()=>page.locator('[data-action="v2-submit-item"]');
const out=resolve('docs/screenshots-v2');await mkdir(out,{recursive:true});
try{
  await open();assert.equal(await submit().count(),1);assert.equal(await page.locator('[data-action="v2-save-item"]').count(),1);
  await page.locator('[data-item-check="submit-b"]').check();
  await page.locator('#workspace-title').fill('春季周末广场舞（2次）');await page.locator('#workspace-score').fill('0.60');
  await submit().click();await page.locator('#workspace-confirm-submit').waitFor();
  assert.equal(writes.length,1);assert.equal(writes[0].method,'PUT');assert.equal(writes[0].path,'/api/applications/submit-a');
  assert.match(await page.locator('.modal').innerText(),/春季周末广场舞（2次）/);assert.match(await page.locator('.modal').innerText(),/0\.60/);
  await page.screenshot({path:resolve(out,'14-comparison-submit-mock.png'),animations:'disabled'});
  await page.locator('.modal-footer [data-action="close-modal"]').click();assert.equal(submissions().length,0);assert.equal(await page.locator('#workspace-title').inputValue(),title);
  pass('对照区保存与提交分开；未保存修改先保存，确认展示新名称分值，取消不会提交');

  const beforeInvalid=writes.length;await page.locator('#workspace-title').fill('');await submit().click();
  assert.equal(await page.locator('#workspace-confirm-submit').count(),0);assert.equal(writes.length,beforeInvalid);
  pass('名称未填写时先进行必填校验，不保存或正式提交');

  await page.locator('#workspace-title').fill('春季周末广场舞（2次）');scenario.submitReject=true;
  await submit().click();await page.locator('#workspace-confirm-submit').waitFor();await page.locator('#workspace-confirm-submit').click();
  await page.locator('.modal-error').waitFor();assert.match(await page.locator('.modal-error').innerText(),/正在公示/);
  assert.equal(await page.locator('#workspace-confirm-submit').isVisible(),true);assert.equal(await page.locator('#workspace-title').inputValue(),title);
  pass('后端拒绝提交时保留当前稿和确认弹窗，展示具体原因并允许重试');

  scenario.submitReject=false;await page.locator('#workspace-confirm-submit').click();await page.locator('.modal').waitFor({state:'detached'});
  await page.waitForFunction(()=>S.workspaceItem?.state==='PENDING_PEER'&&!S.workspaceItem?.can_edit);
  assert.equal(await submit().count(),0);assert.equal(await page.locator('[data-action="v2-save-item"]').count(),0);
  assert.equal(writes.some(write=>write.path.includes('submit-b')),false);assert.equal(await page.evaluate(()=>S.checkedItems.has('submit-b')),true);
  assert.equal(submissions().length,2);assert.equal(submissions().every(write=>write.path==='/api/applications/submit-a/submit'),true);
  pass('确认仅提交当前小项并携带保存后版本；其它勾选项不受影响，成功后进入待审只读状态');

  for(const [label,changes,view] of [
    ['只读',{canEdit:false},'applications'],
    ['已批准',{state:'APPROVED',canEdit:false},'applications'],
    ['历史学年',{year:previous.id},'history'],
    ['无提交权限',{permission:false},'applications'],
    ['非本人',{owner:false},'applications'],
    ['辅导员查看',{role:'COUNSELOR',owner:false,canEdit:false},'classApplications'],
  ]){
    scenario={state:'DRAFT',canEdit:true,owner:true,year:current.id,permission:true,role:'STUDENT',submitReject:false,saveReject:false,...changes};
    await open(view);assert.equal(await submit().count(),0,label+'不应有提交按钮');
  }
  pass('只读、已批准、历史、无提交权限、非本人及辅导员查看均隐藏学生提交按钮');

  scenario={state:'DRAFT',canEdit:true,owner:true,year:current.id,permission:true,role:'STUDENT',submitReject:false,saveReject:true};
  await open();await page.locator('#workspace-title').fill('未成功保存的修改');const beforeSaveFailure=submissions().length;await submit().click();
  await page.locator('.toast').filter({hasText:'模拟保存冲突'}).waitFor();assert.equal(await page.locator('#workspace-confirm-submit').count(),0);
  assert.equal(submissions().length,beforeSaveFailure);assert.equal(await page.locator('#workspace-title').inputValue(),'未成功保存的修改');assert.equal(await page.evaluate(()=>S.workspaceDirty),true);
  pass('保存失败时停止提交并保留未保存输入');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{
  await writeFile(resolve(out,'browser-comparison-submit-mock.json'),JSON.stringify({source:'All APIs mocked; write entries are fixture requests only, no actual API/database/cloud writes',checks,errors,writes,reads,createdAt:new Date().toISOString()},null,2));
  await browser.close();
}
