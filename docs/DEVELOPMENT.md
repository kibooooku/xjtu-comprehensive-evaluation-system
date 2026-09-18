# 开发指南

## 环境

推荐使用 Node.js 24.12+、Java 21、Maven 3.9+、Python 3.11 和 Docker Compose。各模块可独立安装和测试；只有需要联调数据库时才启动 MySQL。

## 配置

复制根目录 `.env.example` 为 `.env`，使用本地专用密码。后端通过 `SPRING_DATASOURCE_*` 和 `APP_STORAGE_ROOT` 读取配置。不得在源码、测试或文档中加入真实密码和学生数据。

## 模块命令

```bash
# frontend
cd frontend
npm ci
npm run test
npm run build

# backend
cd backend
mvn test
mvn package

# ocr-service
cd ocr-service
python -m venv .venv
python -m pip install -r requirements-dev.txt
pytest

# infrastructure
docker compose --env-file .env -f infra/docker-compose.yml config
```

## OCR 安装策略

基础开发和接口测试只安装 `requirements-dev.txt`。开始 OCR 实现时，再安装 `requirements-ocr.txt`：

1. 使用 Python 3.11 的独立虚拟环境。
2. CPU 环境安装 PaddlePaddle 3.x；GPU 环境必须先按 CUDA 版本选择官方匹配包。
3. 再安装 PaddleOCR 3.x 推理包。
4. 将模型和下载缓存设置到 Git 忽略的 `ocr-models/` 或用户级缓存目录。
5. CI 的基础测试不下载模型；OCR 集成测试使用显式标记并在具备模型缓存的环境运行。

## 数据库变更

所有 schema 修改都在 `backend/src/main/resources/db/migration` 新增 Flyway migration。已共享或已发布的 migration 不得重写；需要修正时创建下一版本。

## 测试数据

只能使用虚构身份，例如“测试学生甲”和明确非真实格式的编号 `TEST-2026-001`。不得复制参考 Excel 的姓名、学号、成绩、活动或证明文本。

## 开发顺序

非平凡改动先由 `explorer` 确认入口、数据流和影响范围，主 agent 实现，`tester` 补充风险相关测试，最后由 `reviewer` 检查正确性、权限、安全和遗漏。写入型 agent 不并行修改同一模块。
