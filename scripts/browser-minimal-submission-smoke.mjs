/** All APIs mocked. No actual business writes, credentials or provider calls. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000',output=resolve('docs/screenshots-v2');
const definitions=[['COLLECTIVE','集体活动'],['IDEOLOGY','思想政治理论学习'],['SERVICE','社会服务'],['RESEARCH','学术科研及创新创业'],['ARTS','文体竞赛'],['POST','学生组织社团任职'],['REWARD','奖励分']];
const categories=definitions.map(([code,name],index)=>({id:'minimal-cat-'+code,code,name,default_cap:String([3,3,4,10,6,4,5][index])}));
const year={id:'minimal-year',name:'2025-2026学年'},classroom={id:'minimal-class',name:'虚构验收班'};
let identity='STUDENT';
const user=()=>({id:'minimal-user',username:'minimal-user',display_name:'简洁表单验收同学',base_identity:identity,roles:[{code:identity},...(identity==='STUDENT'?[{code:'REVIEWER',class_id:classroom.id}]:[])]});
const permissions=()=>['application.read','application.write','application.submit','activity.read','organization.read','review.peer','review.counselor','export.read'];
const activities=[{id:'matched-valid',canonical_name:'青禾夜跑活动',aliases:['星光跑'],decision:'VALID',status_label:'已认定有效',scope_label:'当前学年'},
 {id:'matched-invalid',canonical_name:'青禾重复活动',aliases:['青禾无效'],decision:'INVALID',status_label:'已认定无效'},
 {id:'matched-unknown',canonical_name:'青禾待认定活动',aliases:[],decision:'UNKNOWN',status_label:'待认定'}];
const records=new Map();
for(const category of categories)for(const suffix of ['a','b']){const id=category.code+'-'+suffix;records.set(id,{id,category,version:1,title:category.name+'虚构活动'+suffix,value:suffix==='a'?'0.00':'1.50',state:'DRAFT',activityId:null,payload:{award_level:'PROVINCIAL',award_grade:'SECOND',assessment_result:category.code==='POST'?'GOOD':'',edition_key:'旧届次保留',track_key:'旧赛道保留',group_key:'旧组别保留',work_key:'旧作品保留',record_level:'SCHOOL',note:'原备注'},manual:false});}
const resolution=record=>record.manual?{status:'MANUAL',value:record.value,retained_value:record.value,missing_facts:[],message:'学生已确认。'}:{status:'NEEDS_REVIEW',value:null,retained_value:record.value,missing_facts:[{code:'FIXTURE',label:'本人的计分依据'}],message:'旧缓存包含无需理由的提示不应显示。'};
const item=record=>({id:record.id,title:record.title,requested_score:record.value,category_id:record.category.id,category_code:record.category.code,category_name:record.category.name,year_id:year.id,year_name:year.name,class_id:classroom.id,class_name:classroom.name,student_user_id:'minimal-user',student_name:'简洁表单验收同学',state:record.state,lock_version:record.version,student_modified_score:record.manual,score_resolution:resolution(record),file_count:1,files:[{id:'file-'+record.id,original_name:'虚构证明.png'}],evidence_summary:{has_participation:true,has_activity_name:true,has_activity_time:true,has_seal:true,complete:true}});
const detail=record=>({...item(record),can_edit:identity==='STUDENT'&&record.state==='DRAFT',can_review:false,can_withdraw:record.state==='PENDING_PEER',viewing_history:false,revision:{id:'revision-'+record.id,title:record.title,requested_score:record.value,activity_id:record.activityId,payload_json:record.payload},files:[],crops:[],history:[],reviews:[],activity:activities.find(value=>value.id===record.activityId),eligibility:{decision:activities.find(value=>value.id===record.activityId)?.decision||'UNKNOWN'}});
const groups=()=>categories.map(category=>({id:'group-'+category.code,category_id:category.id,category_code:category.code,category_name:category.name,year_id:year.id,year_name:year.name,class_id:classroom.id,class_name:classroom.name,student_user_id:'minimal-user',student_name:'简洁表单验收同学',approved_total:'0.00',items:[...records.values()].filter(record=>record.category.code===category.code).map(item)}));
const checks=[],errors=[],writes=[],searches=[];const pass=value=>{checks.push(value);console.log('PASS '+value);};
let heldSubmit=null,holdNextSubmit=false,forceSaveConflict=false;
const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1500,height:1050}});page.on('pageerror',error=>errors.push(error.message));
await page.route('**/api/**',async route=>{
 const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method(),json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 if(method==='GET'){
  if(path==='/api/session')return json({user:user(),csrf_token:'minimal-mock-csrf'});
  if(path==='/api/bootstrap')return json({user:user(),years:[year],active_year:year,current_year:year,classes:[classroom],categories,permissions:permissions(),demo_accounts:[]});
  if(path==='/api/submissions')return json(groups());
  if(path==='/api/analysis-jobs')return json({jobs:[]});
  if(path==='/api/dashboard')return json({stats:{pending_peer:0,pending_counselor:0},recent:[]});
  if(path==='/api/activities/search'){searches.push(Object.fromEntries(url.searchParams));const q=url.searchParams.get('q')||'';if(q==='慢')await new Promise(resolve=>setTimeout(resolve,500));return json({items:q==='快'?[{...activities[0],canonical_name:'最新搜索结果'}]:q==='慢'?[{...activities[1],canonical_name:'过期搜索结果'}]:activities.filter(value=>!q||value.canonical_name.includes(q)||value.aliases.some(alias=>alias.includes(q))),total:3});}
  if(path.startsWith('/api/activity-matches/'))return json(activities.find(value=>value.id===path.split('/').at(-1))||{id:path.split('/').at(-1),canonical_name:'历史认定活动',decision:'UNKNOWN',status_label:'待认定'});
  if(path.startsWith('/api/applications/')){const record=records.get(path.split('/').at(-1));if(record)return json(detail(record));}
 }else{
  const body=request.postDataJSON();writes.push({path,method,body});
  const id=path.split('/')[3],record=records.get(id);
  if(method==='PUT'&&record){
   if(forceSaveConflict||body.expected_version!==record.version)return json({detail:'记录已被更新，请刷新后重试'},409);
   assert.equal(body.score_confirmed,true);record.title=body.title;record.value=Number(body.requested_score).toFixed(2);record.payload=body.payload_json;
   if(Object.hasOwn(body,'activity_id'))record.activityId=body.activity_id;record.version++;record.manual=true;return json(detail(record));
  }
  if(path.endsWith('/submit')&&path.startsWith('/api/applications/')&&record){
   assert.equal(body.score_confirmed,true);if(body.expected_version!==record.version)return json({detail:'记录已被更新，请刷新后重试'},409);
   if(holdNextSubmit){holdNextSubmit=false;await new Promise(resolve=>heldSubmit=resolve);}
   record.manual=true;record.state='PENDING_PEER';record.version++;return json(detail(record));
  }
  if(path.startsWith('/api/submissions/')&&path.endsWith('/submit')){
   assert.equal(body.score_confirmed,true);const results=body.item_ids.map(id=>{const value=records.get(id);if(body.expected_versions[id]!==value.version)return {id,ok:false,detail:'模拟该项版本冲突'};value.manual=true;value.state='PENDING_PEER';value.version++;return {id,ok:true};});return json({results});
  }
 }
 errors.push('Unexpected mocked API '+method+' '+path);return json({detail:'Only mocked API allowed'},400);
});
let navigation=0;async function open(id){await page.goto(base+'/?minimal='+(++navigation)+'#applications');await page.locator('#submission-groups [data-group-id]').first().waitFor();await page.evaluate(id=>openApplicationWorkspace(id),id);await page.locator('#workspace-item-form').waitFor();}
async function cleanDirty(){await page.evaluate(()=>{S.workspaceDirty=false;});}
try{
 for(const [code,name] of definitions){
  await open(code+'-a');const form=page.locator('#workspace-item-form'),fields=await form.locator('input:not([type=hidden]),select,textarea').evaluateAll(values=>values.map(value=>value.name)),extra=['RESEARCH','ARTS','POST'].includes(code);
  assert.deepEqual(fields.sort(),(extra?['title','requested_score','result_label','', 'note']:['title','requested_score','note']).sort());
  assert.equal(await form.locator('select').count(),0);assert.equal(await page.locator('[data-action="v2-confirm-score"]').count(),0);
  assert.doesNotMatch(await form.innerText(),/赛事届次|赛道|作品 \/|破纪录级别|任职月数|志愿服务工时|无需.*理由|分值已手动填写|规范活动匹配/);
  assert.equal(await form.locator('#workspace-result-label').count(),extra?1:0);assert.equal(await form.locator('#workspace-activity-search').count(),extra?1:0);
  if(extra)assert.match(await form.innerText(),/匹配已认定活动，便无需辅导员审批活动有效性/);
  const link=page.locator('a[href="/api/reference/scoring-standard"]');assert.equal(await link.count(),1);assert.equal(await link.getAttribute('target'),'_blank');assert.match(await link.getAttribute('rel'),/noopener/);
  pass(name+'仅显示规定的核心字段，评分标准新标签链接存在');
 }
 await open('RESEARCH-a');assert.equal(await page.locator('#workspace-result-label').inputValue(),'省级二等奖');await page.locator('#workspace-result-label').fill('国际赛事金奖');await page.locator('#workspace-note').fill('备注中写作品与特殊计分');await page.locator('[data-action="v2-save-item"]').click();await page.waitForFunction(()=>!S.workspaceDirty);assert.equal(records.get('RESEARCH-a').payload.result_label,'国际赛事金奖');assert.equal(records.get('RESEARCH-a').payload.work_key,'旧作品保留');pass('自由等级和备注可保存，旧结构化事实保留但不形成多余输入框');
 await open('POST-a');assert.equal(await page.locator('#workspace-result-label').inputValue(),'良好');const search=page.locator('#workspace-activity-search');await search.fill('青禾');await page.locator('[data-activity-choice]').first().waitFor();assert.match(await page.locator('#workspace-activity-options').innerText(),/已认定有效/);assert.match(await page.locator('#workspace-activity-options').innerText(),/已认定无效/);assert.match(await page.locator('#workspace-activity-options').innerText(),/待认定/);await page.locator('[data-activity-choice="0"]').click();assert.equal(await page.locator('#workspace-activity').inputValue(),'matched-valid');await page.locator('[data-action="v2-save-item"]').click();await page.waitForFunction(()=>!S.workspaceDirty);assert.equal(records.get('POST-a').activityId,'matched-valid');assert.equal(searches.at(-1).category_id,'minimal-cat-POST');pass('任职也可按名称或别名搜索活动，候选展示三种认定状态并可选用');
 await page.locator('#workspace-activity-clear').click();assert.equal(await page.locator('#workspace-activity').inputValue(),'');await page.locator('[data-action="v2-save-item"]').click();await page.waitForFunction(()=>!S.workspaceDirty);assert.equal(records.get('POST-a').activityId,null);pass('活动匹配可清除更正，不自动选择候选');
 await open('ARTS-a');const raceSearch=page.locator('#workspace-activity-search');await raceSearch.fill('慢');await page.waitForTimeout(280);await raceSearch.fill('快');await page.waitForTimeout(900);assert.match(await page.locator('#workspace-activity-options').innerText(),/最新搜索结果/);assert.doesNotMatch(await page.locator('#workspace-activity-options').innerText(),/过期搜索结果/);pass('活动检索的迟到响应不覆盖新关键词结果');await cleanDirty();
 await open('ARTS-a');await page.locator('#workspace-activity-search').fill('长'.repeat(300));await page.waitForTimeout(400);assert.ok(searches.at(-1).q.length<=250);pass('活动检索按接口限制截断超长关键词，避免已选长名称或粘贴触发422');await cleanDirty();
 const hidden=records.get('REWARD-a');hidden.activityId='matched-valid';hidden.payload.result_label='历史等级保留';await open('REWARD-a');await page.locator('#workspace-note').fill('仅改奖励备注');await page.locator('[data-action="v2-save-item"]').click();await page.waitForFunction(()=>!S.workspaceDirty);assert.equal(hidden.activityId,'matched-valid');assert.equal(hidden.payload.result_label,'历史等级保留');assert.equal(Object.hasOwn(writes.at(-1).body,'activity_id'),false);pass('不显示等级和匹配的四类保存时不清空历史隐藏数据');
 const direct=records.get('COLLECTIVE-a');await open(direct.id);holdNextSubmit=true;const before=writes.filter(value=>value.path===`/api/applications/${direct.id}/submit`).length;await page.locator('[data-action="v2-submit-item"]').click();await page.waitForFunction(()=>workspaceSubmissions.size===1);await page.evaluate(id=>submitWorkspaceItem(id),direct.id);assert.equal(writes.filter(value=>value.path===`/api/applications/${direct.id}/submit`).length,before+1);assert.equal(await page.locator('.modal').count(),0);heldSubmit();await page.waitForFunction(()=>workspaceSubmissions.size===0);assert.equal(direct.state,'PENDING_PEER');assert.equal(direct.value,'0.00');pass('单项点击直接提交未决0，无确认或关联弹窗，连续点击只发一次提交');
 await open('ARTS-b');await page.locator('#workspace-title').fill('保存后立即提交的文体条目');await page.locator('#workspace-score').fill('1.25');const start=writes.length;await page.locator('[data-action="v2-submit-item"]').click();await page.waitForFunction(()=>S.workspaceItem?.state==='PENDING_PEER');assert.deepEqual(writes.slice(start).map(value=>value.method),['PUT','POST']);assert.equal(records.get('ARTS-b').value,'1.25');assert.equal(await page.locator('.modal').count(),0);pass('未保存的名称和分值先保存，再按保存后的版本直接提交');
 await open('SERVICE-a');await page.evaluate(()=>{S.checkedItems=new Set(['SERVICE-a','SERVICE-b']);return submitSelected('group-SERVICE');});assert.equal(records.get('SERVICE-a').state,'PENDING_PEER');assert.equal(records.get('SERVICE-b').state,'PENDING_PEER');assert.equal(await page.locator('.modal').count(),0);const batch=writes.findLast(value=>value.path==='/api/submissions/group-SERVICE/submit');assert.deepEqual(batch.body.item_ids,['SERVICE-a','SERVICE-b']);assert.deepEqual(Object.keys(batch.body.expected_versions),['SERVICE-a','SERVICE-b']);pass('批量直接提交全部选中项，携带逐项版本与当前分值确认，无额外弹窗');
 await open('RESEARCH-a');await page.evaluate(()=>{S.checkedItems=new Set(['RESEARCH-a','RESEARCH-b']);});records.get('RESEARCH-b').version++;await page.evaluate(()=>submitSelected('group-RESEARCH'));assert.equal(records.get('RESEARCH-a').state,'PENDING_PEER');assert.equal(records.get('RESEARCH-b').state,'DRAFT');assert.equal(await page.evaluate(()=>S.checkedItems.has('RESEARCH-a')),false);assert.equal(await page.evaluate(()=>S.checkedItems.has('RESEARCH-b')),true);assert.equal(await page.locator('.modal').count(),0);pass('批量部分冲突时成功项完成、失败项保留勾选和草稿，不追加确认窗口');
 await open('IDEOLOGY-a');await page.locator('#workspace-title').fill('冲突时保留的输入');records.get('IDEOLOGY-a').version++;records.get('IDEOLOGY-a').value='2.00';await page.locator('[data-action="v2-save-item"]').click();await page.locator('.toast').filter({hasText:'记录已被更新'}).waitFor();assert.equal(await page.locator('#workspace-title').inputValue(),'冲突时保留的输入');assert.equal(await page.evaluate(()=>S.workspaceDirty),true);assert.equal(records.get('IDEOLOGY-a').value,'2.00');pass('旧版本保存返回409并保留输入，不覆盖另一页面的新分值');await cleanDirty();
 for(const role of ['STUDENT','COUNSELOR']){identity=role;await page.goto(base+'/?minimal-review='+(++navigation)+'#review');await page.locator('#submission-groups').waitFor();assert.equal(await page.locator('a[href="/api/reference/scoring-standard"][target="_blank"]').count(),1);}pass('学生审查员和辅导员审核页也可在新标签查看评分标准');
 identity='STUDENT';await open('RESEARCH-b');await page.locator('#workspace-result-label').fill('省级二等奖');await page.locator('.workspace-comparison:has(#workspace-item-form)').screenshot({path:resolve(output,'20-minimal-submission-mock.png'),animations:'disabled'});assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{if(heldSubmit)heldSubmit();await mkdir(output,{recursive:true});await writeFile(resolve(output,'browser-minimal-submission-mock.json'),JSON.stringify({source:'All APIs mocked; fixture-only writes; no actual business/cloud calls',checks,errors,mock_writes:writes,searches,createdAt:new Date().toISOString()},null,2));await browser.close();}
