# 西交德育分统计管理系统

面向班级的德育分申报、材料识别、人工审核、公示与Excel汇总系统。按本项目需求访谈与设计重新实现，使用 **FastAPI + SQLAlchemy + SQLite + 原生JavaScript**，运行时不依赖外部前端CDN。

## 启动

需要 Python 3.12+。Windows 双击根目录 **`启动系统.cmd`**，随后访问 **http://127.0.0.1:8000**。首次启动自动创建33张表、八个演示班级、两个学年及演示账号；重复启动保留数据。

手动启动：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
```

Linux/macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

默认仅监听本机。端口可在 `.env` 中设置 `PORT`；`instance/` 保存数据库、受保护文件、会话密钥与本地识别预算，均不提交到Git。

## 演示账号

统一初始密码：**`Demo@2026`**。全部姓名与学号是虚构演示数据。

| 账号 | 身份 | 演示范围 |
| --- | --- | --- |
| `student` | 学生 王欣然 | 软件2401，本人填报与本班公示 |
| `student2` | 学生 赵一诺 | 软件2401，便于检查同学可见范围 |
| `monitor` | 班长 张知远 | 软件2401，兼有学生功能 |
| `reviewer1` | 学生审查员 陈思齐 | 软件2401，不能审核本人 |
| `reviewer2` | 学生审查员 李沐阳 | 软件2401，另一名独立审查员 |
| `counselor` | 辅导员 周老师 | 信息2401—2406、软件2401—2402 |
| `admin` | 系统管理员 | 账号、组织及矩阵；无业务材料或审核权限 |

其他班级分别配置一名班长、两名审查员。账号创建/导入和带班关系由管理员维护，班长可调整本班审查员但不能少于两人。新建班级先处于筹备状态，配置完整后启用。学生更换班级仅调整指定学年，不覆盖历史；已有申请的学年不能静默重绑定。

`DEMO_MODE=false` 隐藏演示快捷账号入口；它不会自动删除既有演示账号或更改密码。设置后的既有数据仍保留，可在账号管理中维护。

## 完整流程

1. 学生选择学年与七类入口之一，上传PDF/JPG/PNG，单文件不超过20MB；同一申请允许多个附件。
2. 可点击真实OCR/AI分析，查看建议及不确定性，再显式应用。也可直接手填，框选有效性、参与证明截图和额外学号遮挡区。修改计分无需填写理由，审核端保留“手动修改过计分”的标记。
3. 正式提交进入本班共享学生初审。一名非本人审查员即可完成本轮；四类初审通过即通过，科研、文体及奖励类的未知活动资格转辅导员，已认定有效且范围匹配的活动免重复认定资格。
4. 辅导员分别处理活动资格与个人材料结论。个人材料错误不会把活动误加入无效库。修改资格提供受影响历史清单，不自动撤销历史成绩。
5. 竞赛按赛事、届次、赛道、组别、作品确认同项目；高分优先、同分看层级。新高项**提交时**替代旧项，失败或撤回恢复旧轮次原状态，保留后续独立人工决定。
6. 成绩按70基准、七类上限、同项目取最高、任职取最高及志愿年度子项上限计算。所有已通过项统一汇总，公示与导出共用计算服务。
7. 所有当前正式申请审核完毕后，辅导员开启班级学年公示。公示期间可保存草稿，禁止正式提交与重提；仍可驳回已通过条目并实时更新成绩。页面提示至少三个工作日，由辅导员手动关闭。
8. 班长或辅导员导出15列、双层表头的Excel，包括全班无申请同学的70分，扣分为0，签字栏留空。

## 真实AI与OCR配置

凭据仅后端读取，选择以下方式之一：

- 环境变量或本地 `.env`：`DEEPSEEK_API_KEY`、`TENCENTCLOUD_SECRET_ID`、`TENCENTCLOUD_SECRET_KEY`。
- 保持本项目的文件：`secrets/deepseek_api_key.txt`、`secrets/tencent_ocr.env`。腾讯文件兼容 `TENCENT_OCR_SECRET_ID` / `TENCENT_OCR_SECRET_KEY` 等常见命名。

腾讯调用 **GeneralAccurateOCR、SealOCR**，DeepSeek使用可配置的 `DEEPSEEK_MODEL`，默认 `deepseek-flash`。接口状态只返回已配置与本地预算，不返回任何凭据。官方参考见[接口来源](docs/source-references.md)。

只使用腾讯免费资源包：账户侧须关闭后付费。本地每接口默认每月最多100次，配置 `OCR_GENERAL_MONTHLY_LIMIT`、`OCR_SEAL_MONTHLY_LIMIT` 可进一步降低；本地计数不代表腾讯云共享免费余额。相同页面、相同参数复用成功OCR，不自动重试失败或购买资源。额度/网络/AI异常均可手动继续申报。

`CLOUD_ANALYSIS_ENABLED=false` 可关闭云识别。`OCR_MAX_ANALYSIS_PAGES` 默认12，超过只分析前部分并提示，其余页面可手动查看。公示截图须有成功文字OCR支撑的脱敏版本；未就绪时显示提示，不开放原图。审核端可查看完整原件和私有截图。

## 权限、历史与数据

采用“角色—资源操作—数据范围”矩阵，逐条授权相交后求并，再施加强制规则：基础身份互斥、目标班级范围、禁止自审、管理员不得读取材料、每班一名班长及至少两名审查员、公示提交锁。前端隐藏菜单与后端鉴权同时存在；直接请求API也受同样约束。

已提交内容冻结，重填生成修订，审核与事件绑定原轮次；详情可切换历史版本。SQLite写操作使用 `BEGIN IMMEDIATE`，将公示检查、提交、替代、恢复与审查置于共同事务边界。密码scrypt哈希，HttpOnly/SameSite会话、CSRF、来源检查和密码更改后旧会话失效；文件不通过静态目录公开。

没有将用户原始示例证明、原始学生汇总表、密钥或数据库提交到仓库。评分规则以[可随仓库分发的规则说明](docs/scoring-rules.md)和[最终需求](需求对齐记录-grill-me.md)为准。

## 验收与开发

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check app/static/app.js
```

自动测试使用临时数据库与虚构材料，云适配使用受控替身，**不会消耗云额度**。真实服务小样可显式运行 `python scripts/smoke_cloud.py --cloud`，每次可能产生一次DeepSeek生成，并对一页材料调用每类OCR一次（成功缓存可复用）。

浏览器验收脚本为 `scripts/browser-smoke.mjs`，需在 `.tools/browser` 安装Playwright并有Chrome，运行方法见脚本。浏览器会创建标注为验收的数据并下载一次Excel；不调用云接口。

- [验收记录](docs/acceptance-report.md)
- [UML、DFD与ER图册](系统设计/设计图总览.html)
- [设计说明](系统设计/设计评审说明.md)
- [完整数据字典](系统设计/数据字典.md)
- [Visual Paradigm可编辑工程](系统设计/VisualParadigm/德育分统计系统-设计评审.vpp)
- 本地API文档：http://127.0.0.1:8000/api/docs
