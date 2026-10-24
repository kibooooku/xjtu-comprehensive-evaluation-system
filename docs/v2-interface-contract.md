# V2协作接口（已实现）

旧 applications / immutable revisions / rounds / review / scoring 保持为小项业务内核，新增 category_submissions + category_items 分组，不销毁旧数据。root拥有db.py/main.py/organization.py、app/migrations.py、app/jobs.py。workflow agent拥有workflow.py、app/submissions.py及对应测试。materials agent拥有materials.py/integrations.py及证据/识别测试。frontend agent拥有static和browser脚本。

## root数据库新增表

- system_settings: id(字符串键),value_json(JSON),changed_by?(users),updated_at。
- category_submissions: id,enrollment_id,category_id,created_at,updated_at，unique(enrollment_id,category_id)。
- category_items: id,submission_id,application_id,sort_order,confirmed_at?,deleted_at?,created_at，unique(application_id)。
- background_jobs: id,application_id,owner_id,kind(OCR|AI),state(QUEUED|RUNNING|SUCCEEDED|FAILED|STALE),stage,progress,total,input_revision_id,input_fingerprint,result_json,error_message?,force(bool),attempts,worker_token?,heartbeat_at?,created_at,started_at?,finished_at?。

root app/migrations.py 提供 initialize_v2()，保留所有application建立分组、初始当前学年保留V1首个可选学年，以及 active_year(conn)->dict、assert_current_year(conn,year_id)（非当前HTTP409）、ensure_group(conn,application,confirmed=True)->(submission,item)。seed后initialize_v2即可。变更之前本地SQLite备份。

## 新分组API（workflow agent）

APIRouter(prefix='/api') in submissions.py；main会include。
- GET /submissions?year_id=&class_id=&scope=mine|review|class&state= 返回数组，每组 {id,category_id/category_name/category_code,year_id/year_name,class_id/class_name,student_user_id/student_name,item_count,counts:{DRAFT,...},state,requested_total,approved_total,items:[existing app summary + confirmed_at,deleted_at,analysis_status,ocr_status,latest_job]}。一组只返回权限允许的小项，但review scope整组可比较已批准/驳回/待审（不能只给待审行）。
- POST /submissions {category_id} 当前学生当前学年get-or-create；GET /submissions/{id} 返回组+全部未删除items（各item为application_detail），允许include_deleted=true查询历史。可读范围同application.read；ADMIN禁止。
- POST /submissions/{id}/items {staged?:bool=true} 创建小项，默认confirmed_at=None；继承旧create_application内部事务服务或直接sameconnection创建，不嵌套write事务。
- POST /submissions/{id}/confirm-upload {item_ids:[application_id]} 确认这批成功上传的每项至少一文件，confirmed_at=now，提交后 root jobs.enqueue(application_id,user,'OCR')（不在写事务里嵌套enqueue）。未确认项在主要列表/成绩/待审统计隐藏且不能提交/识别。
- POST /submissions/{id}/submit {item_ids:[application_id]} 逐小项调用相同提交规则，返回 {results:[{id,ok,detail?}],...}，允许部分成功、每项独立事务。不重提已通过/待审。
- DELETE /submissions/{id}/items/{application_id} 仅本人DRAFT/REJECTED/WITHDRAWN可软删（applications.archived_at+category_items.deleted_at）；保留历史，不能删待审/批准。
- 旧POST /applications 仍可调用供旧客户端/测试，改为缺省当前学年；显式传非当前学年拒绝；ensure_group(...confirmed=True)。PUT/文件/截图/撤回/submit学生写操作必须当前学年，旧待审review允许。旧列表隐藏archived/staged。
- exact file hash去重在materials.upload_file与workflow.update file_ids共同调用submissions.assert_unique_files(conn,application_id,file_ids_or_hashes)；相同parent其他任何非删除项使用相同hash则HTTP409，返回已有小项ID；同小项补传相同hash直接返回已存在file（不重复生成file/page）。跨category允许。历史重复只标记，不强制删除。

## 后台任务（root）

`enqueue(application_id,user,kind='AI',force=False)` 使用自有db.write返回job(dict)，检查owner、当前学年、confirmed，pending/approved禁止排任务；对相同指纹同种活跃任务复用；AI默认跳过已分析且无新增文件的项与已提交项。未分析的人工稿可以首次获得建议但不自动覆盖；force允许重分析但只给建议不覆盖。OCR成功缓存复用。
- POST /applications/{id}/jobs {kind:'OCR'|'AI',force?:false} → {job,...}。
- POST /analysis-jobs/bulk {submission_id?:id, application_ids?:[],kind:'AI'} → {jobs,skipped}，不传ids默认当前学生当前学年全部confirmed可编辑未分析项。
- GET /analysis-jobs?active_only=false → {jobs:[...]}，仅本人；GET /analysis-jobs/{id} → job。结果/状态全在SQLite，lifespan启动单后台worker，刷新/换页面/关浏览器不中止。AI排任务会先运行/复用OCR。
- 后台调用材料agent的 `run_ocr(application_id,user,progress=None)` 与 `run_ai(application_id,user,progress=None)`；progress(stage,done,total)。返回dict(status SUCCEEDED|MANUAL_REQUIRED,...)；run_ai复用原_analyze逻辑，不能自动覆盖。
- root后台在AI成功后比对revision_fingerprint与input_revision_id及当前状态：完全未变且初始空白新稿(未人工编辑)才调用 materials.apply_analysis(...)自动应用；force/人工稿仅保存建议。旧年/删项/改稿→STALE不覆盖。

## 证据（materials agent）

_evidence_suggestions严格VALIDITY名称/日期/印章三种，新增meta kind NAME|TIME|SEAL；PARTICIPATION只一张、原页整宽+1/4高，优先学号，其次姓名班级，源图中坐标红框，不拼接。
crop_view须提供 {file_name,page_number(1-based),page_id,box,highlights:[{box,field}],other_matches:[{page_id,page_number,file_name,box,field}],validity_kind?}；无需改旧evidence_crops schema，metadata存现有 masks_json，保留manual/automatic/page_hash，不被publicredact覆盖。新生成图片本身红框，页码由UI明确显示；原图页无改写。手动PARTICIPATION强制整宽1/4并可调整y、页面；已有AI参与截图替换为一张但不得动冻结批准历史图。
run_ocr只OCR+生成建议/可安全生成新稿截图，不调用DeepSeek；run_ai首先run_ocr缓存，再_analyze；AI具体标题拒绝泛称、不得编活动名，提示人工补全。

## 学年（root）

GET /bootstrap新增 active_year/current_year，years保留供历史/教职工查看。GET /admin/statistics-year → {active_year,years}；PUT /admin/statistics-year {year_id} 管理员admin.classes权限，必要新建学年 POST /admin/academic-years {code,name} 同时建各class_year/公示与最新现班学生enrollments，保留历史。学生当前填报页不再选学年，历史入口可以查看旧年且只读。

## 前端（frontend agent）

主要我的申报页面每类一行卡片或大类展开表格；七类入口直接打开上传整理弹窗；文件先存在浏览器本地groups，默认主文件一小项，可每组添附件；点确认后依次建staged item+上传文件，再confirm-upload后才开始OCR。途中禁止重复点击，不在modal选文件时触发OCR。上传完成只刷新表格而不跳入旧单项详情。
category详情为表格，行按小项状态/分值/文件/具体名/证据/操作，选中行左侧原件/截图右侧信息并列对照，review按小项，不锁整大类；旧单项编辑可复用嵌入/抽屉，识别按钮靠近上传区。全站后台任务栏+轮询，即使切菜单也不销毁；各类别/行OCR中/未分析/AI中/完毕/失败提示。一键分析跳过旧结果和锁定项。当前学年admin设置，学生只读显示，历史另入口。所有写API保留CSRF及权限处理。
