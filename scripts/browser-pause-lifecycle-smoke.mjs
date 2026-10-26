/** All API requests are intercepted; fixtures never touch the business DB or cloud. */
import {chromium} from '../.tools/browser/node_modules/playwright/index.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8000',out=resolve('docs/screenshots-v2');
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true}),checks=[],errors=[],allWrites=[];
const pass=message=>{checks.push(message);console.log('PASS '+message);};
const until=async predicate=>{for(let n=0;n<300;n++){if(predicate())return;await new Promise(resolve=>setTimeout(resolve,20));}throw new Error('Mock fixture wait timed out');};
const user={id:'upload-student',username:'upload-student',display_name:'上传验收同学',base_identity:'STUDENT',roles:[{code:'STUDENT'}]},year={id:'upload-year',name:'2025-2026学年'},classroom={id:'upload-class',name:'模拟班'};
const categories=['a','b','c'].map((letter,index)=>({id:'upload-'+letter,code:['COLLECTIVE','POLITICS','SERVICE'][index],name:['集体活动','理论学习','社会服务'][index],default_cap:'3.00'}));

async function fixture({editor=false}={}){
 const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage(),groups=new Map(),items=new Map(),writes=[],reads=[],holds=new Map(),failFiles=new Map(),held=[],control={lostConfirm:false,holdJobs:false,oldJobs:null,holdAuth:false,oldAuth:null,yearChanged:false,paused:false,jobs:[]};let nextId=0;
 const itemView=item=>({id:item.id,title:item.title||'等待核对',requested_score:'0.30',state:item.state||'DRAFT',student_user_id:user.id,student_name:user.display_name,class_id:classroom.id,class_name:classroom.name,year_id:year.id,year_name:year.name,category_id:item.categoryId,category_name:categories.find(c=>c.id===item.categoryId).name,category_code:categories.find(c=>c.id===item.categoryId).code,lock_version:1,confirmed_at:item.confirmed_at,file_count:item.files.length,files:item.files.map((name,index)=>({id:item.id+'-file-'+index,original_name:name})),evidence_summary:{complete:false}});
 const groupView=group=>({id:group.id,category_id:group.categoryId,category_name:categories.find(c=>c.id===group.categoryId).name,category_code:categories.find(c=>c.id===group.categoryId).code,student_user_id:user.id,student_name:user.display_name,class_id:classroom.id,class_name:classroom.name,year_id:year.id,year_name:year.name,approved_total:'0.00',items:[...items.values()].filter(item=>item.groupId===group.id&&item.confirmed_at).map(itemView)});
 if(editor){groups.set('upload-c',{id:'group-upload-c',categoryId:'upload-c'});items.set('editor-item',{id:'editor-item',categoryId:'upload-c',groupId:'group-upload-c',title:'已有未提交条目',confirmed_at:'mock-confirmed',files:[]});}
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method(),json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(method==='GET'){
   reads.push(path);
   if(path==='/api/session')return json({user,csrf_token:'mock-csrf'});
   if(path==='/api/demo-accounts')return json({accounts:[]});
   if(path==='/api/bootstrap')return json({user,years:[year],active_year:year,current_year:year,classes:[classroom],categories,permissions:['application.read','application.write','application.submit','activity.read','organization.read'],demo_accounts:[]});
   if(path==='/api/analysis-jobs'){if(control.holdJobs){await new Promise(resolve=>control.oldJobs=resolve);return json({jobs:[{id:'private-old-job',application_id:'private-old-app',title:'OLD-OWNER-PRIVATE-TITLE',kind:'OCR',state:'RUNNING',created_at:'mock'}]});}return json({jobs:control.jobs,control:{paused:control.paused,reason:control.paused?'MANUAL':''}});}
   if(path==='/api/old-auth'){await new Promise(resolve=>control.oldAuth=resolve);return json({detail:'old owner expired'},401);}
   if(path==='/api/submissions')return json([...groups.values()].map(groupView));
   if(path.startsWith('/api/submissions/')){const group=[...groups.values()].find(value=>value.id===path.split('/')[3]);if(group)return json(groupView(group));}
   if(path==='/api/activities')return json([]);
   if(path==='/api/applications/editor-item'){const item=items.get('editor-item');return json({...itemView(item),can_edit:true,can_review:false,revision:{title:item.title,requested_score:'0.30',payload_json:{}},files:[],crops:[],history:[],versions:[]});}
  }else{
   let body;if(path.endsWith('/files')){const data=request.postDataBuffer()?.toString('latin1')||'';body={filename:/filename="([^"]+)"/.exec(data)?.[1]};}else body=request.postDataJSON();
   const record={method,path,body};writes.push(record);allWrites.push(record);
   if(path==='/api/analysis-control/presence')return json({paused:control.paused});
   if(path==='/api/analysis-control/leave'){control.paused=true;return json({paused:true});}
   if(path==='/api/analysis-control'){control.paused=body.action==='pause';return json({paused:control.paused});}
   if(path==='/api/logout')return json({ok:true});
   if(path==='/api/submissions'&&method==='POST'){assert.equal(body.year_id,year.id);if(control.yearChanged)return json({detail:'当前统计学年已改变，旧学年材料不能填报'},409);let group=groups.get(body.category_id);if(!group){group={id:'group-'+body.category_id,categoryId:body.category_id};groups.set(body.category_id,group);}return json(groupView(group));}
   if(path.endsWith('/items')&&method==='POST'){const group=[...groups.values()].find(value=>value.id===path.split('/')[3]);assert.ok(group);const item={id:'upload-item-'+(++nextId),groupId:group.id,categoryId:group.categoryId,files:[],confirmed_at:null};items.set(item.id,item);return json({id:item.id});}
   if(path.endsWith('/files')&&method==='POST'){
    const item=items.get(path.split('/')[3]);assert.ok(item);const filename=body.filename;
    if(holds.has(filename)){await new Promise(resolve=>held.push({filename,release:resolve}));}
    if(failFiles.get(filename)){failFiles.set(filename,failFiles.get(filename)-1);return json({detail:'模拟文件保存失败，可重试'},503);}
    item.files.push(filename);return json({id:'mock-file',pages:[]});
   }
   if(path.endsWith('/confirm-upload')&&method==='POST'){
    const group=[...groups.values()].find(value=>value.id===path.split('/')[3]);
    for(const id of body.item_ids){const item=items.get(id);assert.equal(item.groupId,group.id);assert.ok(item.files.length);item.confirmed_at='mock-confirmed';}
    if(control.lostConfirm){control.lostConfirm=false;return route.abort('connectionreset');}
    return json({submission:groupView(group),confirmed_item_ids:body.item_ids,jobs:[],ocr_errors:[]});
   }
  }
  errors.push('Unexpected mock API '+method+' '+path);return json({detail:'Only mocked fixture APIs allowed'},400);
 });
 await page.goto(base+'/#applications');await page.locator('.category-entry').first().waitFor();
 const enqueue=async(category,names,supplements=[])=>{
  await page.locator(`.category-entry[data-category="${category}"]`).click();
  await page.locator('#organizer-main-files').setInputFiles(names.map(name=>({name,mimeType:'application/pdf',buffer:Buffer.from('Mock PDF, never parsed by a real API')})));
  if(supplements.length)await page.locator('[data-supplement-input]').first().setInputFiles(supplements.map(name=>({name,mimeType:'application/pdf',buffer:Buffer.from('Mock supplement')})));
  await page.locator('#organizer-confirm').click();await page.locator('.modal').waitFor({state:'detached'});
  return page.evaluate(()=>S.uploadBatches.at(-1).id);
 };
 const waitBatch=(id,state)=>page.waitForFunction(({id,state})=>S.uploadBatches.find(batch=>batch.id===id)?.state===state,{id,state});
 const release=filename=>{holds.delete(filename);for(const entry of held.filter(entry=>entry.filename===filename))entry.release();};
 return {page,context,groups,items,writes,reads,holds,held,control,failFiles,enqueue,waitBatch,release};
}

try{
 const f=await fixture(),{page}=f;
 await until(()=>f.writes.some(row=>row.path==='/api/analysis-control/presence'));
 f.control.jobs=[{id:'visible-ocr',application_id:'fake-application',kind:'OCR',state:'RUNNING',stage:'OCR识别第1页',created_at:'2026-10-26T10:00:00Z',started_at:'2026-10-26T10:00:00Z',progress:1,total:5,result_json:{}}];
 f.holds.set('paused-main.pdf',true);const batch=await f.enqueue('upload-a',['paused-main.pdf'],['paused-supplement.pdf']);await until(()=>f.held.length===1);
 await page.evaluate(()=>pollJobs());await page.locator('[data-job-action="expand"]').click();await page.locator('#jobs-list .job-row').waitFor({state:'visible'});
 await page.locator('[data-dock-action="toggle"]').click();assert.equal(await page.locator('#background-jobs').evaluate(el=>el.getBoundingClientRect().height<=60),true);assert.equal(await page.locator('.upload-queue-list').isVisible(),false);assert.equal(await page.locator('#jobs-list').isVisible(),false);
 for(let index=0;index<3;index++)await page.evaluate(()=>pollJobs());await page.evaluate(()=>go('activities'));assert.equal(await page.locator('#background-jobs').evaluate(el=>el.getBoundingClientRect().height<=60),true);pass('顶部箭头统一收起上传及OCR/AI整块面板，轮询和站内切页保持单行');
 const toggle=page.locator('[data-dock-action="toggle"]');await toggle.focus();await page.keyboard.press('Enter');await page.locator('#jobs-list .job-row').waitFor({state:'visible'});assert.equal(await page.locator('.upload-queue-list').isVisible(),true);pass('键盘可重新展开整个面板，上传和后台进度均恢复');
 await page.locator('[data-dock-action="pause"]').click();await until(()=>f.control.paused);f.release('paused-main.pdf');await f.waitBatch(batch,'PAUSED');assert.equal(f.writes.filter(row=>row.body?.filename==='paused-main.pdf').length,1);assert.equal(f.writes.filter(row=>row.body?.filename==='paused-supplement.pdf').length,0);assert.equal(f.writes.filter(row=>row.path.endsWith('/confirm-upload')).length,0);pass('暂停上传保留当前已返回文件，不启动下一附件或确认，也不把暂停当失败');
 assert.equal(await page.evaluate(()=>{S.taskControlRevision=8;syncTaskControl({paused:false,revision:7});return S.tasksPaused;}),true);pass('迟到的旧心跳结果不能解除人工暂停');
 await page.locator('[data-dock-action="resume"]').click();await f.waitBatch(batch,'COMPLETED');assert.equal(f.writes.filter(row=>row.body?.filename==='paused-main.pdf').length,1);assert.equal(f.writes.filter(row=>row.body?.filename==='paused-supplement.pdf').length,1);assert.equal(f.writes.filter(row=>row.path.endsWith('/confirm-upload')).length,1);pass('继续时复用已保存主文件，只传剩余附件，再确认一次');
 const before=f.writes.filter(row=>row.path==='/api/analysis-control').length;await page.evaluate(()=>go('applications'));assert.equal(f.writes.filter(row=>row.path==='/api/analysis-control').length,before);pass('站内路由不发送暂停命令');
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await until(()=>f.writes.some(row=>row.path==='/api/analysis-control/leave'));assert.equal(f.control.paused,true);assert.equal(f.writes.filter(row=>row.path==='/api/analysis-control/leave').at(-1).body.document_id.length>8,true);pass('真正离开文档发送带页面标识的keepalive暂停通知');
 f.control.paused=false;await page.evaluate(()=>heartbeatTasks());await page.locator('[data-action="logout"]').click();await page.locator('#login-form').waitFor();const logoutWrites=f.writes.filter(row=>row.path==='/api/analysis-control'||row.path==='/api/logout');assert.equal(logoutWrites.at(-2).body.reason,'LOGOUT');assert.equal(logoutWrites.at(-1).path,'/api/logout');pass('退出登录先暂停服务端任务，再清会话');
 await f.context.close();assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{await writeFile(resolve(out,'browser-pause-lifecycle-mock.json'),JSON.stringify({source:'All APIs mocked; synthetic local files; no real business or cloud calls',checks,errors,mock_writes:allWrites,createdAt:new Date().toISOString()},null,2));await browser.close();}
