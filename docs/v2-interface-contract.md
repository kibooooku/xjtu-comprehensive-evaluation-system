# V2接口与申报合同（2026-10-26）

`applications / immutable revisions / rounds / review / scoring` 为小项业务内核，`category_submissions + category_items` 按大类归组，不销毁旧数据。本文是当前接口合同；早期文档中的竞赛自动替代、项目取最高和独立确认分值流程已由[申报简化与活动匹配](申报简化与活动匹配.md)更新。

## 分组与任务数据

- system_settings: id(字符串键),value_json(JSON),changed_by?(users),updated_at。
- category_submissions: id,enrollment_id,category_id,created_at,updated_at，unique(enrollment_id,category_id)。
- category_items: id,submission_id,application_id,sort_order,confirmed_at?,deleted_at?,created_at，unique(application_id)。
- background_jobs: id,application_id,owner_id,kind(OCR|AI),state(QUEUED|RUNNING|SUCCEEDED|FAILED|STALE),stage,progress,total,input_revision_id,input_fingerprint,result_json,error_message?,force(bool),attempts,worker_token?,heartbeat_at?,created_at,started_at?,finished_at?。

`app/migrations.py` 提供 `initialize_v2()`，保留所有application并建立分组，以及 `active_year(conn)->dict`、`assert_current_year(conn,year_id)`（非当前HTTP409）、`ensure_group(conn,application,confirmed=True)->(submission,item)`。seed后运行迁移；变更之前本地SQLite备份。科研和文体分类升级为 `SUM`，任职保留 `MAX`；已有审核轮次、旧关联记录和历史快照不被重新演算。

## 分组API

`submissions.py` 路由前缀为 `/api`。

- GET /submissions?year_id=&class_id=&scope=mine|review|class&state= 返回数组，每组 {id,category_id/category_name/category_code,year_id/year_name,class_id/class_name,student_user_id/student_name,item_count,counts:{DRAFT,...},state,requested_total,approved_total,items:[existing app summary + confirmed_at,deleted_at,analysis_status,ocr_status,latest_job]}。一组只返回权限允许的小项，但review scope整组可比较已批准/驳回/待审（不能只给待审行）。
- POST /submissions {category_id} 当前学生当前学年get-or-create；GET /submissions/{id} 返回组+全部未删除items（各item为application_detail），允许include_deleted=true查询历史。可读范围同application.read；ADMIN禁止。
- POST /submissions/{id}/items {staged?:bool=true} 创建小项，默认confirmed_at=None；继承旧create_application内部事务服务或直接sameconnection创建，不嵌套write事务。
- POST /submissions/{id}/confirm-upload {item_ids:[application_id]} 确认这批成功上传的每项至少一文件，confirmed_at=now，事务提交后调用 jobs.enqueue(application_id,user,'OCR')，完成材料处理后自动衔接AI。未确认项在主要列表/成绩/待审统计隐藏且不能提交/识别。
- POST /submissions/{id}/submit {item_ids:[application_id],expected_versions?:{application_id:version},score_confirmed?:true} 逐小项执行相同提交规则，返回 {results:[{id,ok,detail?}],submitted}，允许部分成功、每项独立事务。提交本身确认当前分值，包括0；不以自动计分待核定状态增加确认步骤。不重提已通过/待审项，冲突项保留草稿，客户端保留失败项勾选。
- DELETE /submissions/{id}/items/{application_id} 仅本人DRAFT/REJECTED/WITHDRAWN可软删（applications.archived_at+category_items.deleted_at）；保留历史，不能删待审/批准。
- 旧POST /applications 仍可调用供旧客户端/测试，改为缺省当前学年；显式传非当前学年拒绝；ensure_group(...confirmed=True)。PUT/文件/截图/撤回/submit学生写操作必须当前学年，旧待审review允许。旧列表隐藏archived/staged。
- exact file hash去重在materials.upload_file与workflow.update file_ids共同调用submissions.assert_unique_files(conn,application_id,file_ids_or_hashes)；相同parent其他任何非删除项使用相同hash则HTTP409，返回已有小项ID；同小项补传相同hash直接返回已存在file（不重复生成file/page）。跨category允许。历史重复只标记，不强制删除。

单项 `POST /applications/{id}/submit {expected_version?,score_confirmed?:true}` 同样直接确认并冻结当前值。未保存修改先 `PUT`，提交携带保存后的版本；无修改不要求先保存。保留所有权、当前学年、公示、原文件、基本数字格式及活动资格校验，取消同一竞赛的自动关联、拒绝、替代和恢复。审查员负责比较重复竞赛，科研/文体按已通过分值累加后封顶，任职及志愿年度上限保持原有规则。

## 后台任务

`enqueue(application_id,user,kind='AI',force=False)` 使用自有db.write返回job(dict)，检查owner、当前学年、confirmed，pending/approved禁止排任务；对相同指纹同种活跃任务复用；AI默认跳过已分析且无新增文件的项与已提交项。未分析的人工稿可以首次获得建议但不自动覆盖；force允许重分析但只给建议不覆盖。OCR成功缓存复用。

- POST /applications/{id}/jobs {kind:'OCR'|'AI',force?:false} → {job,...}。
- POST /analysis-jobs/bulk {submission_id?:id, application_ids?:[],kind:'AI'} → {jobs,skipped}，不传ids默认当前学生当前学年全部confirmed可编辑未分析项。
- GET /analysis-jobs?active_only=false → {jobs:[...]}，仅本人；GET /analysis-jobs/{id} → job。结果/状态全在SQLite，服务端处理，刷新/换页面/关浏览器不中止。按用户独立限制OCR文件级并发最多3、AI小项级并发最多3，两个队列独立，超出排队。AI先等待或复用该项已有OCR；不占用AI分析槽位干等OCR，也不取消未完成OCR。
- 确认上传后自动排材料处理，OCR/本地文字层处理完成后自动衔接该小项AI；学生无需再点击一键分析才开始。已有手工稿只提供建议，空白新稿仍按指纹与状态检查决定是否自动填表。
- 后台调用 `run_ocr(application_id,user,progress=None)` 与 `run_ai(application_id,user,progress=None)`；progress(stage,done,total)。返回dict(status SUCCEEDED|MANUAL_REQUIRED,...)；run_ai复用 `_analyze` 逻辑。
- AI成功后比对revision_fingerprint与input_revision_id及当前状态：完全未变且初始空白新稿（未人工编辑）才调用 `materials.apply_analysis(...)` 自动应用；force/人工稿仅保存建议。旧年/删项/改稿→STALE不覆盖。点击一键分析不停止正在进行的OCR，AI等待或复用已有结果。

## 证据

_evidence_suggestions严格VALIDITY名称/日期/印章三种，meta kind NAME|TIME|SEAL；NAME只保留一张完整名称块。PARTICIPATION按participation_role=PRIMARY|OUTCOME最多两张，均原页整宽+1/4高；身份与本人结果可同框时仅PRIMARY一张，否则补OUTCOME图。身份优先准确学号、其次姓名，班级单独不够；结果须关联本人，源图坐标红框，不跨页拼接。

crop_view须提供 {file_name,page_number(1-based),page_id,box,highlights:[{box,field}],other_matches:[{page_id,page_number,file_name,box,field}],validity_kind?,participation_role?,outcome_kind?,outcome_value?,outcome_text?}；field新增OUTCOME，旧参与图无role视PRIMARY。metadata存现有masks_json，保留manual/automatic/page_hash及公示保护。手动参与图按角色分别替换，均整宽1/4并可调整y、页面；OUTCOME单图不计作身份已齐，冻结批准历史图不改写。

run_ocr只OCR+生成建议/可安全生成新稿截图，不调用DeepSeek；run_ai首先run_ocr缓存，再_analyze；AI具体标题拒绝泛称、不得编活动名，提示人工补全。

## 活动匹配与资格

只有 `RESEARCH`、`ARTS`、`POST` 显示并自动检索活动。学生、审查员和辅导员按已有 `activity.read` 权限读取公开活动元数据，不返回其它学生的私有证明、截图或维护者信息。

- `GET /activities/search?q=&year_id=&category_id=&limit=20&offset=0`：`q` 最多250字，`limit` 为1—100，`offset>=0`。省略学年使用当前学年；按当前类别/学年检索全库，再分页返回 `{items,total,limit,offset,query}`，不先截取前300条。名称与所有有效别名均可搜索，中文空格及标点宽容处理。
- `GET /activity-matches/{id}?year_id=&category_id=`：直接返回一条DTO，供旧选中值只读显示。跨类别、旧年或停用值保留原ID和名称；不适用或停用时为 `UNKNOWN`，不能自动重新匹配停用项。
- DTO：`{id,canonical_name,name,aliases:[string],decision:VALID|INVALID|UNKNOWN,status_label,scope_label,eligibility:{decision,version_id,scope},match_score,match_kind,exact_match}`。`name` 兼容旧调用；`match_score` 为0—1，`match_kind` 为 `EXACT_NAME|EXACT_ALIAS|RELATED`。
- 非空关键词按相似度排序，不能因候选有效而超过更接近的无效活动。空关键词用于浏览，按有效、无效、待认定及名称稳定分页。候选显示当前认定状态，用户可更换或清除。
- `retrieve_activity_candidates(conn,texts,category_id,year_id,limit=30,current_activity_id=None)` 先检索全库，再向AI提供最相关候选。AI只可返回候选中的合法ID；唯一明确全名或别名可兜底，普通近似、无关材料或多个明确候选不强配。错误类型/不存在的ID被丢弃；应用建议时重新检查活动是否仍启用。
- 资格统一按活动、大类、学年判断。优先使用当前学年的通用认定；没有通用认定，或通用与历史具体范围决策冲突时为 `UNKNOWN` 交辅导员。历史隐藏的届次、赛道、组别不参与当前提交判断。搜索状态、详情和实际审批复用同一判定逻辑。
- 三类当前 `VALID` 认定可免活动有效性重复审批，`UNKNOWN` 经学生初审后交辅导员，`INVALID` 提示更正匹配。其它四类不因旧隐藏 `activity_id` 或资格阻塞提交；保存/分析保留其旧字段。

匹配说明固定为：**匹配已认定活动，便无需辅导员审批活动有效性**。该提示对应复用当前有效认定，候选的实际状态同时展示，个人材料和计分仍需审核。

## 学年

GET /bootstrap新增 active_year/current_year，years保留供历史/教职工查看。GET /admin/statistics-year → {active_year,years}；PUT /admin/statistics-year {year_id} 管理员admin.classes权限，必要新建学年 POST /admin/academic-years {code,name} 同时建各class_year/公示与最新现班学生enrollments，保留历史。学生当前填报页不再选学年，历史入口可以查看旧年且只读。

## 前端与计分状态

七类仅显示 `title`（具体活动与条目名称）、`requested_score`（申报分值）和 `payload_json.note`（备注）。只有科研、文体竞赛、任职追加 `payload_json.result_label`（一个自由填写的获奖/评定等级）及 `activity_id` 搜索组件；不分别显示赛事级别、届次、赛道、组别、作品、岗位、作者顺序、工时或破纪录级别。自动计分事实仍保存在后端依据中，更新表单合并保留未展示的历史字段。

`score_resolution={status:RULE_APPLIED|NEEDS_REVIEW|MANUAL|UNANALYZED,value:两位字符串|null,retained_value,missing_facts:[{code,label}],message,rule,source}` 在summary/detail/AI建议中提供。未决不是自动计算的0分，正常规则0与手填0照常显示。页面普通保存携带 `score_confirmed:true` 和当前分值；提交本身也确认当前分值，不需要独立确认按钮。API仅保存标题且未显式确认时不核定0。表单保存和单项提交携带加载时 `expected_version`，批量携带逐项版本；脏表单不跟随后台内容推进版本，409保持输入。详情见[七大类自动计分验收](七大类自动计分验收.md)。

“我的申报”每类一份申报及展开表格；七类入口直接打开上传整理弹窗，文件先存在浏览器本地groups，默认主文件一小项，可每组添附件。点确认立即关闭整理窗口并回到列表，在后台依次建staged item、上传文件、confirm-upload，再开始OCR；可继续上传其它大类。禁止重复确认，不在弹窗选文件时触发OCR，不跳入旧单项页面。

category详情为表格，行按小项状态/分值/文件/具体名/证据/操作，选中行左侧原件/截图右侧信息并列对照，review按小项，不锁整大类；旧链接统一定位到大类对照区，识别按钮靠近上传区。全站后台任务栏+轮询，即使切菜单也不销毁；各类别/行OCR中/未分析/AI中/完毕/失败提示。一键分析跳过旧结果和锁定项。当前学年admin设置，学生只读显示，历史另入口。所有写API保留CSRF及权限处理。

对照核对统一在大类页展示；移除“完整版本与历史”和旧单份文件页入口，审核记录与历史快照仍在后端保存。本人参与及有效性证据在原件之前，材料处理和AI分析显示耗时提示，轮询不重建脏表单。提交按钮直接提交，绿色批准直接完成，红色驳回才弹出理由输入。

学生、学生审查员和辅导员工作区顶部的“查看评分标准”使用 `target="_blank"` 打开 `GET /api/reference/scoring-standard`，返回原校PDF并受登录保护。该参考文件不属于学生私有上传材料。

## 验证依据

六份浏览器JSON报告共83项（简化表单19、单项提交6、稳定性14、统一对照12、OCR队列13、真实隔离链路19），错误均为0。真实隔离链路使用独立HTTP服务、SQLite和原生调度，确认每用户OCR文件、AI小项并发峰值各3、每项OCR与AI自动各排一次、AI满额时OCR独立运行，未发送手动分析POST。文字OCR、印章及模型各9次均为离线替身，外网请求、真实云调用和真实业务写入均为0。

后台任务及材料相关回归82项全部通过，包含 `tests/test_job_concurrency.py` 13项事件同步并发验证；另有 `tests/test_ai_activity_matching.py` 23项分析到应用验证。完整报告入口见[V2验收](v2/acceptance.md)；完整回归命令为 `python -m pytest -q`，全量结果以CI运行记录为准。
