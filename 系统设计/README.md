# 德育分统计系统设计稿

日期：2026-10-24；状态：等待设计确认，业务实现未开始。

- [打开设计图总览](设计图总览.html)：14张图，支持缩放。
- [总设计说明与待审阅细节](设计评审说明.md)。
- [可编辑Visual Paradigm工程](VisualParadigm/德育分统计系统-设计评审.vpp)。
- [完整数据库字段字典](数据字典.md)。
- [RBAC与领域设计](RBAC与领域设计说明.md)。
- [数据库设计与一致性约束](数据库设计说明.md)。
- [数据流层次分析](数据流分析说明.md)及[完整流编号字典](数据流字典.md)。

## 图稿索引

| 图号 | 名称 | SVG | PNG |
| --- | --- | --- | --- |
| 01-rbac-organization | RBAC与班级组织领域类图 | [矢量图](图稿/01-rbac-organization.svg) | [图片](图稿/01-rbac-organization.png) |
| 02-application-evidence-review | 申请版本、证据与审核领域类图 | [矢量图](图稿/02-application-evidence-review.svg) | [图片](图稿/02-application-evidence-review.png) |
| 03-activity-scoring-publicity | 活动资格、计分与公示领域类图 | [矢量图](图稿/03-activity-scoring-publicity.svg) | [图片](图稿/03-activity-scoring-publicity.png) |
| 04_dfd_context | 数据流图：上下文图 | [矢量图](图稿/04_dfd_context.svg) | [图片](图稿/04_dfd_context.png) |
| 05_dfd_level1 | 数据流图：一级分解 | [矢量图](图稿/05_dfd_level1.svg) | [图片](图稿/05_dfd_level1.png) |
| 06_dfd_p2_level2 | 数据流图：P2 材料识别与人工确认 | [矢量图](图稿/06_dfd_p2_level2.svg) | [图片](图稿/06_dfd_p2_level2.png) |
| 07_dfd_p3_level2 | 数据流图：P3 正式申报与奖项替代恢复 | [矢量图](图稿/07_dfd_p3_level2.svg) | [图片](图稿/07_dfd_p3_level2.png) |
| 05a_dfd_level1_external | 一级图审阅切片：外部实体及进程间数据流 | [矢量图](图稿/05a_dfd_level1_external.svg) | [图片](图稿/05a_dfd_level1_external.png) |
| 05b_dfd_level1_storage | 一级图审阅切片：逻辑存储读写数据流 | [矢量图](图稿/05b_dfd_level1_storage.svg) | [图片](图稿/05b_dfd_level1_storage.png) |
| 08-er-overview | 数据库 ER 总览：33 张逻辑表 | [矢量图](图稿/08-er-overview.svg) | [图片](图稿/08-er-overview.png) |
| 09-er-rbac | 数据库 ER：账号、组织与 RBAC | [矢量图](图稿/09-er-rbac.svg) | [图片](图稿/09-er-rbac.png) |
| 10-er-evidence | 数据库 ER：文件、识别与证据 | [矢量图](图稿/10-er-evidence.svg) | [图片](图稿/10-er-evidence.png) |
| 11-er-workflow | 数据库 ER：版本、审核与奖项替代 | [矢量图](图稿/11-er-workflow.svg) | [图片](图稿/11-er-workflow.png) |
| 12-er-catalog | 数据库 ER：活动资格、计分规则与公示 | [矢量图](图稿/12-er-catalog.svg) | [图片](图稿/12-er-catalog.png) |

## 交付与验证

- 33张表、298个字段、76个字段级外键；ER总览展示42条主要关系。
- 14张SVG均校验节点／关系数量，14张PNG均可解析；33张原生表字段数量与源模型一致、无重复字段名。
- 原生VP工程保存成功；其容器以只读方式检查，未创建业务数据库。
- Visual Paradigm试用版的原生水印导出保存在VisualParadigm/原生导出；清晰图从相同语义模型独立绘制，不是去水印处理。
- 源文件保留JSON、DOT、统一模型XML、布局和制图工具；制图工具不是业务应用。
- 本次未读取密钥或调用DeepSeek／腾讯云接口。

## 阅读顺序

建议先看01（RBAC）、04（系统边界）、08（ER全貌），再看02与07（版本和替代恢复）以及对应ER分图。一级数据流完整图较宽，可以使用浏览页缩放，05a与05b为互补切片。

设计评审说明第7节列出四项设计细化，确认设计后仍等待明确实现指令。