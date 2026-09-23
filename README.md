# 西交班级综合素质测评管理系统

本仓库是“软件系统分析与设计”课程项目的工程骨架。系统将综合素质测评中的申报条目、证明文件、OCR 文本与坐标、人工证据标注、确定性评分规则、审核记录和最终汇总建立可追溯关联。

当前版本支持学生草稿申报、私有 PDF 上传、人工证据标注、结构化加分条目与确定性评分、正式提交、班委审核及驳回后重新提交。对有原生文本层的 PDF，还可生成身份信息候选区域；必须由学生确认后才保存为证据。提交保留 PDF、标注与评分的不可变快照。暂不包含 OCR 或 Excel 导出。

## 架构

- `frontend`：Vue 单页应用，后续负责结构化表单、PDF.js 预览、OCR 候选展示和人工框选。
- `backend`：Spring Boot 主业务服务，负责认证授权、状态机、确定性计分、PDFBox 原生文本分析、文件元数据和审核记录。
- `ocr-service`：FastAPI 文档处理边界，预留给后续扫描件 OCR；原生 PDF 文本提取目前由后端 PDFBox 完成。
- `infra`：本地 MySQL Docker Compose 配置。

OCR 只识别文字和坐标，并辅助定位姓名、学号、日期或机构名称。证明真实性、活动有效性和最终审核结果由人工决定；评分由可测试、可追溯的确定性规则引擎完成。

## 技术栈

- Frontend：Vue 3、TypeScript、Vite、Vue Router、Element Plus、PDF.js、Vitest
- Backend：Java 21、Spring Boot 3、Maven、Spring Security、MySQL 8、Flyway、JUnit 5
- OCR service：Python 3.11、FastAPI、PaddleOCR（可选 OCR 依赖组）、pytest
- Infrastructure：Docker Compose、MySQL 8

实际锁定版本见 `frontend/package-lock.json`、`backend/pom.xml` 和 `ocr-service/requirements*.txt`。

## 目录结构

```text
.
├─ frontend/                 Vue 应用
├─ backend/                  Spring Boot 应用
├─ ocr-service/              FastAPI 应用
├─ docs/                     需求、架构、开发说明和待确认事项
├─ infra/docker-compose.yml  本地 MySQL
├─ .codex/                   项目级 Codex 和子代理配置
├─ .env.example              环境变量示例
└─ AGENTS.md                 项目开发约束
```

## 本地开发

前置条件：Node.js 24.12+、Java 21、Maven 3.9+、Python 3.11 和 Docker Compose。复制 `.env.example` 为 `.env`，并设置本地密码；不要提交 `.env`。

### MySQL

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d
docker compose --env-file .env -f infra/docker-compose.yml down
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

默认访问 `http://localhost:5173`。构建和测试：

```bash
npm run build
npm run test
```

### Backend

```bash
cd backend
mvn spring-boot:run
```

首次手工验证可启用仅限开发环境的虚构数据：

    APP_DEMO_PASSWORD=<仅保存在本机的密码> mvn spring-boot:run -Dspring-boot.run.profiles=dev

开发账号为 student-demo（学生）和 committee-demo（班委），密码由本机 APP_DEMO_PASSWORD 环境变量提供；变量缺失时 dev profile 会拒绝启动。这些账号只由 dev profile 初始化，不会由正式 Flyway migration 写入生产数据库。前端凭证仅保存在页面内存中；正式部署必须使用独立账号开通流程和 HTTPS。
健康检查：`GET http://localhost:8080/api/health`。测试和打包：

```bash
mvn test
mvn package
```

### OCR service

```bash
cd ocr-service
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

健康检查：`GET http://localhost:8000/health`。测试：

```bash
pytest
```

需要开始实现 OCR 时，再根据 CPU/GPU 与操作系统安装 `requirements-ocr.txt` 中的 Paddle 依赖。模型下载目录必须指向 Git 忽略的本地缓存。

## 项目文档

- [项目规格](docs/PROJECT_SPEC.md)
- [架构说明](docs/ARCHITECTURE.md)
- [开发指南](docs/DEVELOPMENT.md)
- [待确认问题](docs/OPEN_QUESTIONS.md)

仓库不保存真实班级汇总表、学生姓名、学号、成绩或证明材料。测试和演示数据必须完全虚构。


## 人工证据标注

登录后创建草稿并上传 PDF，点击“查看并标注本人 PDF”。PDF.js 页面支持翻页、缩放和鼠标拖拽矩形；选好 IDENTITY（身份信息）或 VALIDITY（材料有效性）后保存。点击已保存的区域可修改类型或重新框选，也可删除。再次登录并打开该草稿时，会从后端恢复已保存的区域。

证据区域使用未旋转 PDF 页面左上角为原点的归一化坐标（x、y、width、height 均为页面宽高的比例），不持久化 Canvas 或屏幕像素。后端验证页码为正数和矩形边界；对能正常解析的 PDF，后端保存实际页数并拒绝超出页数的标注；解析失败时页数未知，仍允许人工流程。前端 PDF.js 只允许在实际页面范围内翻页。

PUT /api/declarations/{id}/pdf 可替换本人草稿的 PDF；操作会在同一数据库事务中清除旧标注，并递增 documentVersion。替换前页面会提示用户。证据区域 POST、PUT、DELETE 必须在 If-Match 请求头提交当前数字版本；旧标签页的过期写入返回 409，需重新打开 PDF。旧文件在提交成功后尽力删除，新文件在事务回滚时删除。当前没有 OCR 或有效性自动判断。


## 提交与审核状态机

申报只能按以下业务操作转换：DRAFT → PENDING → APPROVED/REJECTED；学生对已驳回申报执行“修改申报”后 REJECTED → DRAFT，修改完成可再次提交。第一次正式提交的 submissionVersion 为 1，之后每次正式提交加 1。documentVersion 仅表示 PDF 替换次数，两者互不混用。

正式提交前，后端要求当前 PDF、至少一个 IDENTITY 区域及至少一个 VALIDITY 区域。提交时会把 PDF 和当时的证据区域复制为不可变快照；驳回后即使替换 PDF，旧审核仍指向原提交版本的快照。当前快照仅用于内部追溯，尚未提供历史 PDF 页面。

DRAFT 仅申报所有者可读写。PENDING、APPROVED、REJECTED 允许所有者与本班 CLASS_COMMITTEE 读取；班委不能编辑学生申报，并且不能审核自己的申报。学生在 PENDING 状态不能改标题、PDF 或证据。审核通过不能携带驳回原因；驳回必须选择原因，OTHER 还须填写说明。所有状态检查和班级权限均在后端执行。

主要接口：

- POST /api/declarations/{id}/submit：所有者正式提交；If-Match 为当前 PDF 的 documentVersion。
- POST /api/declarations/{id}/revise：所有者将驳回申报转回草稿；If-Match 为当前 submissionVersion。
- PATCH /api/declarations/{id}：仅草稿所有者修改标题。
- GET /api/declarations/{id}/reviews：查看审核历史。
- GET /api/review/classes/{classId}/pending：本班班委待审队列。
- POST /api/review/declarations/{id}/decision：本班班委提交审核决议，JSON 包含 submissionVersion、result；驳回时还需 reasonCode，OTHER 还需 customReason。

本机没有 Docker CLI 时，仅使用 H2 MySQL 模式验证 Flyway；真实 MySQL 8 仍需在具备 Docker 的环境中单独验证。

## 结构化加分条目与确定性计分

V5 增加 `score_rule_set`、`score_rule`、`score_item` 和 `submission_score_item`，并将班级显式绑定到规则集版本。学校版规则集 `XJTU_SCHOOL_2018_V1` 的来源为用户提供的《西安交通大学本科生综合素质测评成绩评定办法》第 6 页“学术科研及创新创业·学科竞赛”。高水平国际、国家级一/二/三等奖分别为 10/9/8；省级为 8/6/4；校级、地方行政部门、行业/企业、学会/协会一/二/三/优秀奖为 4/3/2/1；特等奖按同级一等奖计。只实现上述有明确依据的组合。书院规则版本及覆盖口径仍待确认，不能从示例 Excel 推导。规则行视为不可变：规则调整必须用新的 Flyway migration 新增规则集版本及规则行，再明确更新班级绑定；禁止原位 UPDATE 旧规则分值或来源。已提交快照始终保留原版本及原分数。

学生在草稿中维护多条结构化 ScoreItem。`GET /api/score-rules?classId={classId}` 提供该班级当前启用规则和来源；`GET/POST /api/declarations/{id}/score-items`、`PUT/DELETE /api/declarations/{id}/score-items/{itemId}` 提供条目操作。写请求的 `If-Match` 为当前 `submissionVersion`，服务端锁定申报行并要求所有者处于 DRAFT。请求不接受 `calculatedScore` 作为计分依据，服务端按启用规则重新匹配；无匹配规则时拒绝。正式提交要求至少一项有效条目，事务内再次计分，并把当时的分类、级别、奖项、分数、规则版本及来源写入不可变提交快照。班委只能读取非草稿条目及系统建议分，不能改分。

“同项目取最高”原则与学术科研类别 10 分上限已在项目规范确认，但项目唯一性尚未定义，跨多份申报的类别汇总不在本轮范围。因此当前分数是逐项建议分，不是最终累计分或封顶分；后续统计切片需先确认 `docs/OPEN_QUESTIONS.md` 中的口径。

## 原生 PDF 文本身份候选

V6 为用户增加可选的学号、姓名，为当前 PDF 增加 pageCount 和文本分析状态（TEXT_AVAILABLE、NO_TEXT、FAILED），并允许正式证据来源 PDF_TEXT_AUTO。学生登录后可在页面填写本人身份信息。上传或替换 PDF 时，后端用 Apache PDFBox 在本机同步读取原生文本层、页数和每个字符的位置；文本块使用与人工标注相同的未旋转页面左上角归一化坐标。扫描件没有可搜索文本时显示 NO_TEXT，解析失败显示 FAILED，均保留人工框选能力。原始文本块仅在进程内缓存最近 8 份分析结果，缓存键为 PDF 记录 ID 与 documentVersion；服务重启后首次查看会从私有存储重新分析，不新增文本块数据库表。

草稿所有者可调用 GET /api/declarations/{id}/identity-candidates 查看候选。算法先找学号完全匹配；仅当找不到学号时才找姓名完全匹配。多处命中全部展示，不自动猜测正确位置。学生在 EvidenceEditor 查看候选所在页及覆盖框后，须明确点击确认；POST /api/declarations/{id}/identity-candidates/{candidateId}/confirm 携带当前 documentVersion 的 If-Match，后端按当前 PDF 重新验证候选并创建 IDENTITY/PDF_TEXT_AUTO 证据区域。忽略候选后仍可人工框选。候选接口仅供草稿所有者使用，班委及其他学生不能读取。

替换 PDF 会清除旧标注、更新页数和分析状态、递增 documentVersion，并使旧候选失效。解析限于 10 MB 上传限制下最多 200 页和 100,000 个文本位置；超过限制记为 FAILED，不阻止人工流程。解析使用 PDFBox 临时文件缓存，并在进入文字位置排序缓存前实施数量限制。本功能只处理原生文本层，不识别扫描图片、模糊姓名或材料有效性。页面整体旋转已有测试；页面内局部旋转文字的候选框仍需人工核对。

Flyway V4 与 V6 使用 MySQL 8.0.19 起支持的 DROP CONSTRAINT 语法；真实 MySQL 尚待具备 Docker 的环境验证。
