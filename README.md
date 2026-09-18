# 西交班级综合素质测评管理系统

本仓库是“软件系统分析与设计”课程项目的工程骨架。系统将综合素质测评中的申报条目、证明文件、OCR 文本与坐标、人工证据标注、确定性评分规则、审核记录和最终汇总建立可追溯关联。

当前版本已完成首个纵向业务切片：数据库用户认证、班级成员与学生/班委角色、学生草稿申报、私有 PDF 上传和本人重新查看。暂不包含 OCR、评分、审核、证据框选或 Excel 导出。

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
