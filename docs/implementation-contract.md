# 实现接口约定

本次已获用户明确授权实现并更新远程仓库，按2026-10-24最终需求及设计执行。技术栈：FastAPI + SQLAlchemy Core + SQLite + 原生 JS/CSS，同源页面。所有API以 `/api` 开头，JSON直接返回对象/数组，错误 `{detail: 中文说明}`。UUID字符串主键，时间ISO字符串，分值以字符串传输、Decimal两位精度计算。数据表与 `app/schema.json` 一致。

## 后端共享约定

`app.db`: `tables` 字典 SQLAlchemy Table；`read()` / `write()` 上下文返回 Connection（写事务 BEGIN IMMEDIATE）；`one(conn, table_name, id=None, **where)` 返回dict或None；`rows(conn, table_name, **where)` 返回dict列表；`add(conn, table_name, **values)` 自动填id和通用时间返回dict；`change(conn, table_name, id, **values)` 返回dict；`now()` ISO UTC；`uid()` UUID；`money(value)` Decimal两位HALF_UP。所有日期字段以String保留ISO，JSON字段SQLAlchemy JSON，金额Numeric。

`app.security`: `current_user(request)` FastAPI依赖返回User dict；`allowed(conn,user,permission,class_id=None,owner_id=None)`逐条grant权限与范围相交，附强制身份策略；`require(...)` 不满足HTTP403；`student_enrollment(conn,user,year_id)` 返回Enrollment（不存在HTTP400）；`class_context(conn,enrollment_id)`返回 `{enrollment, student, user, class_year, class, year}`。管理员不能查看业务材料或审核，不可通过权限矩阵绕过。写事务由调用方管理。

工作流由 `app.workflow.router` 路由 /api；材料与导出由 `app.materials.router` 路由 /api；身份组织基础接口由root编写。各router内APIRouter(prefix='/api')。`create_app(database_url=None, storage_dir=None, seed_demo=True)`工厂在 `app.main`；db采用模块默认engine但测试工厂可配置，测试顺序运行。各服务存储路径读取 `app.db.storage_root`，不在全局import时缓存值。

## 前端接口

- `GET /api/session` → `{user:null}` 或 `{user:{id,username,display_name,base_identity,roles:[{code,class_id}]},csrf_token}`。
- `POST /api/login {username,password}` → 同session；HttpOnly同源cookie；后续写API请求带 `X-CSRF-Token`。`POST /api/logout`；`POST /api/password {current_password,new_password}`。
- `GET /api/bootstrap` → `{user,years:[{id,code,name}],classes:[{id,name}],categories:[{id,code,name,default_cap,...}],permissions:[string],demo_accounts:[{username,name,role}]}`。
- `GET /api/dashboard?year_id=&class_id=` → `{stats:{applications,pending,approved,total_score,baseline},category_scores:[{name,score,cap}],recent:[application...]}`。
- `GET /api/classes/{class_id}/members?year_id=` → `{class,year,members:[{id,user_id,display_name,student_number?,roles:[]}],publicity:{is_open,...}}`。
- `PUT /api/classes/{class_id}/reviewers {user_ids:[...]}`，仅班长，至少两人。
- `GET /api/applications?year_id=&class_id=&scope=mine|review|class&state=` → 数组，字段包含 `{id,title,category_id,category_name,requested_score,state,student_name,class_name,year_name,student_modified_score,round_id,lock_version,updated_at}`。
- `POST /api/applications {year_id,category_id}` → 详情。`GET /api/applications/{id}` → `{...列表字段,revision:{id,title,requested_score,student_modified_score,activity_id,payload_json,...},files:[{id,original_name,pages:[{id,page_index,width_px,height_px,url}]}],crops:[{id,evidence_type,url,public_url,...}],history:[...],reviews:[...],round:{...}|null,can_edit,can_review}`。
- `PUT /api/applications/{id} {title,requested_score,activity_id?,payload_json:{edition_key,track_key,group_key,work_key,award_level,volunteer_hours?,subtype?,note?},file_ids?:[...]}` → 详情。学生改分无理由要求；七类大类不能迁移。
- `POST /api/applications/{id}/submit {expected_version?,related_application_id?}`；`POST /api/applications/{id}/withdraw {expected_version?}`。
- `POST /api/applications/{id}/review {action:'APPROVE'|'REJECT',reason?,comment?,expected_version?,idempotency_key?,activity_decision?:'VALID'|'INVALID'}`。未知三类交辅导员，允许活动有效但材料驳回；已通过可辅导员驳回。
- `GET /api/activities?search=` → 数组 `{id,canonical_name,aliases:[...],eligibilities:[...]}`；`POST /api/activities {canonical_name,aliases:[],category_id,decision,edition_key?,track_key?,group_key?,academic_year_id?,reason?}`；`PUT /api/activities/{id}`同；`DELETE /api/activities/{id}`逻辑移除认定；`GET /api/activities/{id}/impact`受影响历史申请数组。
- `GET /api/scores?class_id=&year_id=` → `{class,year,publicity,members:[{student_id,user_id,display_name,student_number?,baseline,total,categories:[{id,code,name,raw,score,cap,items:[...]}]}]}`；`GET /api/publicity?class_id=&year_id=`同结构，仅公示开启后普通学生可读；其中items含crops和reviewer_name，不含原件链接或学号。
- `PUT /api/publicity {class_id,year_id,is_open}`。仅辅导员，开启前无当前待审，公示期间禁正式提交。
- `GET /api/export?class_id=&year_id=` 下载15列Excel（班长/辅导员）。
- `POST /api/applications/{id}/files` FormData `file`→ 文件对象含pages；`DELETE /api/applications/{id}/files/{file_id}`。
- `POST /api/applications/{id}/crops {page_id,evidence_type:'VALIDITY'|'PARTICIPATION',box:[x,y,w,h],masks?:[[x,y,w,h]]}` → crop；`DELETE /api/applications/{id}/crops/{crop_id}`。canvas使用原图像素坐标，截图两类均允许多张。
- `POST /api/applications/{id}/analyze {}` → `{status,analysis?,suggestion?,message,...}`；真实接口失败可手工继续。`POST /api/applications/{id}/apply-analysis {analysis_id}`应用建议，显式点击不覆盖迟到编辑。`GET /api/integrations/status` → 配置布尔状态/本地限额，不回传任何密钥。
- `GET /api/admin/users`；`POST /api/admin/users {username,display_name,base_identity,password,student_number?,class_id?,counselor_class_ids?:[]}`；`PUT /api/admin/users/{id}`；`POST /api/admin/import` FormData CSV UTF-8，同字段；`GET /api/admin/roles` → `{roles,permissions,matrix:[{role_id,permission_id,is_allowed,data_scope}]}`；`PUT /api/admin/roles/{role_id}/permissions {permissions:[{permission_id,is_allowed,data_scope}]}`；`POST /api/admin/classes {name,code,...}`；`PUT /api/admin/classes/{class_id}/assignments {monitor_id,reviewer_ids:[...],counselor_ids:[...]}`。

前端使用中文桌面优先响应式，侧边导航、学年/班级选择、完整登录、申请与证据框选、审核、活动库、公示、成员、管理员账号和矩阵。避免仅做静态演示。无外部CDN运行依赖；可用内联SVG图标；所有用户文字转义。
