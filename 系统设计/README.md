# 当前系统 UML 与 ER 图

版本日期：2026-10-26；以当前实现及静态数据库元数据为准。

- [打开设计图总览](设计图总览.html)：4张UML与5张ER，支持缩放及原图下载。
- [项目改动简要总结](../项目改动简要总结.md)。
- [可编辑 Visual Paradigm 工程](VisualParadigm/德育分统计系统-设计评审.vpp)。
- [完整数据字典](数据字典.md)及[当前设计说明](设计评审说明.md)。

| 图号 | 内容 | SVG | PNG |
| --- | --- | --- | --- |
| 01-rbac-organization | UML：RBAC 与班级学年组织 | [矢量图](图稿/01-rbac-organization.svg) | [图片](图稿/01-rbac-organization.png) |
| 02-application-evidence-review | UML：大类申报、小项、内容版本与审核 | [矢量图](图稿/02-application-evidence-review.svg) | [图片](图稿/02-application-evidence-review.png) |
| 03-activity-scoring-publicity | UML：活动认定、政策计分、公示与导出 | [矢量图](图稿/03-activity-scoring-publicity.svg) | [图片](图稿/03-activity-scoring-publicity.png) |
| 04-materials-background-jobs | UML：材料、证据与 OCR / AI 独立后台并行 | [矢量图](图稿/04-materials-background-jobs.svg) | [图片](图稿/04-materials-background-jobs.png) |
| 08-er-overview | 数据库 ER 总览 · 37 张实际表 | [矢量图](图稿/08-er-overview.svg) | [图片](图稿/08-er-overview.png) |
| 09-er-rbac | 数据库 ER · 组织与RBAC | [矢量图](图稿/09-er-rbac.svg) | [图片](图稿/09-er-rbac.png) |
| 10-er-evidence | 数据库 ER · 材料、识别与后台任务 | [矢量图](图稿/10-er-evidence.svg) | [图片](图稿/10-er-evidence.png) |
| 11-er-workflow | 数据库 ER · 大类申报与逐项审核 | [矢量图](图稿/11-er-workflow.svg) | [图片](图稿/11-er-workflow.png) |
| 12-er-catalog | 数据库 ER · 活动、计分、公示与设置 | [矢量图](图稿/12-er-catalog.svg) | [图片](图稿/12-er-catalog.png) |

ER四张分图按外键所在子表分组，联合覆盖全部84条真实FK；总览仅显示主干，图例明确说明。旧竞赛项目和替代记录作为兼容结构保留，不表示启用旧业务。

本次不更新或交付数据流图。旧DFD源文件仅作历史留存，不在当前图册中。制品均使用元数据与类/服务职责，不含真实账号或材料。