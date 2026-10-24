# 设计制图工具说明

这些文件用于生成设计模型、图稿和审阅资料，不是德育分系统的业务实现。直接打开已交付 `.vpp` 工程或浏览图稿，不需要安装或运行这些工具。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| render_graphviz.mjs | 从JSON语义模型独立生成DOT、清晰SVG、共享布局和数据流编号字典 |
| prepare_models.py | 将语义JSON、展示标签和布局整理成Visual Paradigm生成器输入XML |
| DesignPlugin.java／plugin.xml | 调用本机Visual Paradigm OpenAPI生成原生类图、DFD及ER模型和工程 |
| VisualParadigm制图插件.zip | 本任务生成器的编译包，仅在需要重新制图时使用 |
| publish_review.py | 本机Chrome渲染PNG、生成静态图稿浏览页与缩略索引 |
| audit_design.py | 生成完整字段字典／索引，核对节点和关系计数、PNG、字段外键与原生工程只读完整性 |
| ApiInspect.java | 查看本机Visual Paradigm API签名的辅助工具 |

## 制图依赖与重生成顺序

- 本次使用本机 Visual Paradigm 18.1、其自带Java 11、本机Node和Python、Chrome、Pillow。
- 清晰矢量图使用开源 [Viz.js / Graphviz](https://github.com/mdaines/viz-js)，工具包固定为 `@viz-js/viz` 3.31.0，MIT许可。版本和来源记录在 `源文件/renderer-provenance.json`。
- 为避免向应用项目引入依赖，下载和编译缓存只放临时 `.work`；不修改Node／Python全局环境，不修改业务运行配置。重新运行时需按本机路径准备该工具缓存。
- 重生成顺序为：`render_graphviz.mjs` → `prepare_models.py` → Visual Paradigm生成器 → `publish_review.py` → `audit_design.py`。

## Visual Paradigm生成器的边界

生成器会清空其输入工程中的普通模型，以创建这一套设计，所以**只能使用复制的空白／种子工程**，不能把正在编辑的用户工程作为输入。为避免误运行，生成器要求显式提供 `-Ddeyu.seed.copy.confirmed=true`；未提供即拒绝执行。

本次使用安装目录内置 `resources/BaggageSchemas.vpp` 的工作区副本，并只把结果保存到本任务设计输出目录；没有修改安装目录中的种子文件，也没有操作用户原先打开的untitled工程。后续重新生成时宜输出到新目录，保留人工调整后的旧设计稿。

主要参数为：

```text
-Ddeyu.seed.copy.confirmed=true
-Ddeyu.manifest=<design-model.xml绝对路径>
-Ddeyu.output=<新的设计输出目录>
com.vp.cmd.Plugin -project <复制的种子.vpp> -upgrade-project -pluginid deyu.design.review.v1
```

原生接口、类图、DFD、ER实体和外键均由Visual Paradigm自身创建。原生导出保留试用版水印；清晰图是从语义JSON独立绘制的另一套输出，不读取、修改或去除原生图片水印。

## 校验范围

制图校验只检查本次设计资料，未访问 `secrets`、未发起DeepSeek／腾讯OCR请求、未建立业务数据库、未启动Web服务。VPP工程的容器完整性以只读方式检查；这不表示已经进行了业务应用测试。
