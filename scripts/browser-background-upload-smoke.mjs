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
 const context=await browser.newContext({viewport:{width:1440,height:1050}}),page=await context.newPage(),groups=new Map(),items=new Map(),writes=[],reads=[],holds=new Map(),failFiles=new Map(),held=[],control={lostConfirm:false,holdJobs:false,oldJobs:null,holdAuth:false,oldAuth:null,yearChanged:false};let nextId=0;
 const itemView=item=>({id:item.id,title:item.title||'等待核对',requested_score:'0.30',state:item.state||'DRAFT',student_user_id:user.id,student_name:user.display_name,class_id:classroom.id,class_name:classroom.name,year_id:year.id,year_name:year.name,category_id:item.categoryId,category_name:categories.find(c=>c.id===item.categoryId).name,category_code:categories.find(c=>c.id===item.categoryId).code,lock_version:1,confirmed_at:item.confirmed_at,file_count:item.files.length,files:item.files.map((name,index)=>({id:item.id+'-file-'+index,original_name:name})),evidence_summary:{complete:false}});
 const groupView=group=>({id:group.id,category_id:group.categoryId,category_name:categories.find(c=>c.id===group.categoryId).name,category_code:categories.find(c=>c.id===group.categoryId).code,student_user_id:user.id,student_name:user.display_name,class_id:classroom.id,class_name:classroom.name,year_id:year.id,year_name:year.name,approved_total:'0.00',items:[...items.values()].filter(item=>item.groupId===group.id&&item.confirmed_at).map(itemView)});
 if(editor){groups.set('upload-c',{id:'group-upload-c',categoryId:'upload-c'});items.set('editor-item',{id:'editor-item',categoryId:'upload-c',groupId:'group-upload-c',title:'已有未提交条目',confirmed_at:'mock-confirmed',files:[]});}
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),path=url.pathname,method=request.method(),json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(method==='GET'){
   reads.push(path);
   if(path==='/api/session')return json({user,csrf_token:'mock-csrf'});
   if(path==='/api/bootstrap')return json({user,years:[year],active_year:year,current_year:year,classes:[classroom],categories,permissions:['application.read','application.write','application.submit','activity.read','organization.read'],demo_accounts:[]});
   if(path==='/api/analysis-jobs'){if(control.holdJobs){await new Promise(resolve=>control.oldJobs=resolve);return json({jobs:[{id:'private-old-job',application_id:'private-old-app',title:'OLD-OWNER-PRIVATE-TITLE',kind:'OCR',state:'RUNNING',created_at:'mock'}]});}return json({jobs:[]});}
   if(path==='/api/old-auth'){await new Promise(resolve=>control.oldAuth=resolve);return json({detail:'old owner expired'},401);}
   if(path==='/api/submissions')return json([...groups.values()].map(groupView));
   if(path.startsWith('/api/submissions/')){const group=[...groups.values()].find(value=>value.id===path.split('/')[3]);if(group)return json(groupView(group));}
   if(path==='/api/activities')return json([]);
   if(path==='/api/applications/editor-item'){const item=items.get('editor-item');return json({...itemView(item),can_edit:true,can_review:false,revision:{title:item.title,requested_score:'0.30',payload_json:{}},files:[],crops:[],history:[],versions:[]});}
  }else{
   let body;if(path.endsWith('/files')){const data=request.postDataBuffer()?.toString('latin1')||'';body={filename:/filename="([^"]+)"/.exec(data)?.[1]};}else body=request.postDataJSON();
   const record={method,path,body};writes.push(record);allWrites.push(record);
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
 const f=await fixture({editor:true}),{page}=f;
 await page.locator('[data-action="v2-select-item"][data-id="editor-item"]').click();await page.locator('#workspace-title').fill('仍在编辑的名称');await page.evaluate(()=>window.savedEditor=document.querySelector('#workspace-item-form'));
 f.holds.set('held-a.pdf',true);const first=await f.enqueue('upload-a',['held-a.pdf']);await until(()=>f.held.some(value=>value.filename==='held-a.pdf'));
 assert.equal(await page.locator('.modal').count(),0);assert.equal(await page.evaluate(()=>hasPendingLocalUploads()),true);assert.equal(await page.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}),true);
 const second=await f.enqueue('upload-b',['fast-b.pdf']);await f.waitBatch(second,'COMPLETED');
 assert.equal(await page.evaluate(id=>S.uploadBatches.find(batch=>batch.id===id).state,first),'RUNNING');assert.equal(await page.evaluate(()=>window.savedEditor===document.querySelector('#workspace-item-form')),true);assert.equal(await page.locator('#workspace-title').inputValue(),'仍在编辑的名称');
 const secondRow=[...f.items.values()].find(item=>item.categoryId==='upload-b');assert.ok(secondRow?.confirmed_at);assert.ok(await page.locator(`[data-group-id="${secondRow.groupId}"]`).count());
 await page.locator('.category-entry[data-category="upload-c"]').click();await page.locator('#organizer-main-files').setInputFiles({name:'not-yet-confirmed.pdf',mimeType:'application/pdf',buffer:Buffer.from('not sent')});f.release('held-a.pdf');await f.waitBatch(first,'COMPLETED');assert.equal(await page.locator('.modal').count(),1);assert.equal(await page.locator('.organizer-row').count(),1);assert.equal(await page.evaluate(()=>window.savedEditor===document.querySelector('#workspace-item-form')),true);
 await page.locator('#organizer-cancel').click();assert.equal(await page.evaluate(()=>hasPendingLocalUploads()),false);assert.equal(await page.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}),false);
 pass('确认立即关窗，另一大类并行完成；原表单与新上传窗口均保留，新组增量出现且离页提醒只覆盖未完成本地文件');
 await f.context.close();

 const q=await fixture();q.holds.set('limit-a.pdf',true);q.holds.set('limit-b.pdf',true);const qa=await q.enqueue('upload-a',['limit-a.pdf']),qb=await q.enqueue('upload-b',['limit-b.pdf']);await until(()=>q.held.length===2);const qc=await q.enqueue('upload-c',['limit-c.pdf']);assert.equal(await q.page.evaluate(id=>S.uploadBatches.find(batch=>batch.id===id).state,qc),'QUEUED');assert.equal(q.writes.filter(value=>value.body?.filename==='limit-c.pdf').length,0);
 await q.page.evaluate(()=>go('activities'));q.release('limit-a.pdf');await q.waitBatch(qa,'COMPLETED');await q.waitBatch(qc,'COMPLETED');assert.equal(await q.page.evaluate(()=>S.view),'activities');assert.equal(await q.page.evaluate(()=>S.uploadWorkers),1);q.release('limit-b.pdf');await q.waitBatch(qb,'COMPLETED');pass('最多两批上传并行，第三批真实排队；站内切页后持续上传，完成不会抢回申报页');await q.context.close();

 const y=await fixture();y.holds.set('year-a.pdf',true);y.holds.set('year-b.pdf',true);const ya=await y.enqueue('upload-a',['year-a.pdf']),yb=await y.enqueue('upload-b',['year-b.pdf']);await until(()=>y.held.length===2);const yc=await y.enqueue('upload-c',['year-c.pdf']);y.control.yearChanged=true;await y.page.evaluate(()=>{S.boot.active_year={id:'new-academic-year',name:'新统计学年'};});y.release('year-a.pdf');await y.waitBatch(ya,'COMPLETED');await y.waitBatch(yc,'FAILED');assert.equal(y.writes.find(value=>value.path==='/api/submissions'&&value.body.category_id==='upload-c').body.year_id,year.id);assert.equal(y.writes.filter(value=>value.body?.filename==='year-c.pdf').length,0);y.release('year-b.pdf');await y.waitBatch(yb,'COMPLETED');pass('排队期间管理员切换学年，批次仍携带确认时旧学年，由服务端拒绝而不串入新学年');await y.context.close();

 const r=await fixture();r.failFiles.set('retry-supplement.pdf',1);const retryId=await r.enqueue('upload-a',['retry-main.pdf','good-other-row.pdf'],['retry-supplement.pdf']);await r.waitBatch(retryId,'FAILED');assert.ok([...r.items.values()].find(item=>item.files.includes('good-other-row.pdf')).confirmed_at);assert.equal([...r.items.values()].find(item=>item.files.includes('retry-main.pdf')).confirmed_at,null);
 await r.page.locator(`[data-upload-action="retry"][data-batch="${retryId}"]`).click();await r.waitBatch(retryId,'COMPLETED');assert.equal(r.writes.filter(value=>value.body?.filename==='retry-main.pdf').length,1);assert.equal(r.writes.filter(value=>value.body?.filename==='retry-supplement.pdf').length,2);assert.equal(r.writes.filter(value=>value.path.endsWith('/items')).length,2);assert.ok([...r.items.values()].every(item=>item.confirmed_at));pass('附件失败不阻塞其它小项，重试复用小项和已保存文件，全部附件成功后才确认安排识别');await r.context.close();

 const lost=await fixture();lost.control.lostConfirm=true;const lostId=await lost.enqueue('upload-a',['lost-confirm.pdf']);await lost.waitBatch(lostId,'FAILED');const persisted=[...lost.items.values()][0];persisted.state='PENDING_PEER';assert.ok(persisted.confirmed_at);await lost.page.locator(`[data-upload-action="retry"][data-batch="${lostId}"]`).click();await lost.waitBatch(lostId,'COMPLETED');assert.equal(lost.writes.filter(value=>value.path.endsWith('/confirm-upload')).length,1);assert.equal(lost.writes.filter(value=>value.path.endsWith('/files')).length,1);pass('确认响应丢失且小项已被提交时，重试读取服务端确认状态，不重传或重复确认');await lost.context.close();

 const o=await fixture();o.holds.set('owner-first.pdf',true);const oldId=await o.enqueue('upload-a',['owner-first.pdf'],['owner-second.pdf']);await until(()=>o.held.length===1);o.control.holdJobs=true;await o.page.evaluate(()=>{window.oldPoll=pollJobs();});await until(()=>Boolean(o.control.oldJobs));await o.page.evaluate(()=>{window.oldAuth=api('/old-auth').catch(()=>{});});await until(()=>Boolean(o.control.oldAuth));
 await o.page.evaluate(async()=>{S.user={...S.user,id:'other-owner',display_name:'另一账号'};S.boot.user=S.user;renderShell();await go('activities');});o.release('owner-first.pdf');o.control.holdJobs=false;o.control.oldJobs();o.control.oldAuth();await o.page.evaluate(()=>Promise.all([window.oldPoll,window.oldAuth]));await o.waitBatch(oldId,'FAILED');
 assert.equal(o.writes.filter(value=>value.body?.filename==='owner-second.pdf').length,0);assert.equal(await o.page.locator('#upload-batches').innerText(),'');assert.equal(await o.page.evaluate(()=>S.user.id),'other-owner');assert.equal(await o.page.evaluate(()=>S.jobs.some(job=>job.id==='private-old-job')),false);
 await o.page.evaluate(async user=>{S.user=user;S.boot.user=user;renderShell();await go('applications');},user);o.page.once('dialog',dialog=>dialog.accept());await o.page.locator(`[data-upload-action="discard"][data-batch="${oldId}"]`).click();assert.equal(await o.page.evaluate(()=>hasPendingLocalUploads()),false);pass('跨账号停止后续文件写入并隐藏原队列；迟到任务/401不能污染新会话，失败批次可明确放弃');await o.context.close();

 const s=await fixture();s.holds.set('screenshot-a.pdf',true);s.holds.set('screenshot-b.pdf',true);await s.enqueue('upload-a',['screenshot-a.pdf']);await s.enqueue('upload-b',['screenshot-b.pdf']);await until(()=>s.held.length===2);await s.page.locator('#toast-root').evaluate(element=>element.replaceChildren());await s.page.screenshot({path:resolve(out,'17-background-upload-queue-mock.png'),animations:'disabled'});s.release('screenshot-a.pdf');s.release('screenshot-b.pdf');await s.page.waitForFunction(()=>S.uploadBatches.every(batch=>batch.state==='COMPLETED'));await s.context.close();
 assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));throw error;}
finally{await writeFile(resolve(out,'browser-background-upload-mock.json'),JSON.stringify({source:'Every API mocked; fixture writes only; no real database or provider calls',checks,errors,mock_writes:allWrites,createdAt:new Date().toISOString()},null,2));await browser.close();}
