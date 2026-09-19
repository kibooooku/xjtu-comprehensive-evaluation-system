# 西交班级综合素质测评管理系统

本仓库是“软件系统分析与设计”课程项目的工程骨架。系统将综合素质测评中的申报条目、证明文件、OCR 文本与坐标、人工证据标注、确定性评分规则、审核记录和最终汇总建立可追溯关联。

当前版本支持学生草稿申报、私有 PDF 上传与人工证据标注，以及正式提交、班委审核、驳回后修改并重新提交。每次正式提交保留不可变 PDF 与标注快照，审核记录可按 submissionVersion 追溯。暂不包含 OCR、评分或 Excel 导出。

## 架构

- `frontend`：Vue 单页应用，后续负责结构化表单、PDF.js 预览、OCR 候选展示和人工框选。
- `backend`：Spring Boot 主业务服务，后续负责认证授权、状态机、规则计算、文件元数据和审核记录。
- `ocr-service`：FastAPI 文档处理边界，后续负责 PDF 文字层提取、PaddleOCR 调用及文本块坐标输出。
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

证据区域使用未旋转 PDF 页面左上角为原点的归一化坐标（x、y、width、height 均为页面宽高的比例），不持久化 Canvas 或屏幕像素。后端验证页码为正数和矩形边界；当前未在服务端解析 PDF 页数，因此尚不能验证页码不超过实际页数。前端 PDF.js 只允许在实际页面范围内翻页。

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
