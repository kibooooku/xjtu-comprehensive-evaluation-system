# 当前UML与ER制图工具

当前入口为render_current_design.mjs、publish_current_design.mjs、run_current_vp.py、audit_current_design.py；旧V1工具仅作历史记录，不再用于当前交付，避免重新生成旧DFD和33表说明。

语义源在源文件/当前版本/uml-model.json与er-model.json，分别来自领域职责核对和app.db.metadata静态数据。使用项目内Node/Python/Chrome；Graphviz依赖@viz-js/viz 3.31.0安装在被忽略的.tools/design，Playwright使用既有.tools/browser。不修改全局依赖。

重生成顺序：node render_current_design.mjs → node publish_current_design.mjs → python run_current_vp.py → 将staging原生工程/审计复制到交付目录 → python audit_current_design.py（均在项目根用本目录完整路径运行）。

VP生成器只清空内置种子的工作区副本；隔离配置/插件/编译缓存全部位于.tools/design，不修改已打开的用户工程或其它插件。原生项目保留全部37表、333字段、84FK、18唯一索引。清晰SVG/PNG是Graphviz独立绘制，不是去除VP试用水印。

审计只访问静态元数据和本次图稿/VPP，不配置业务数据库或读取学生材料/密钥。本次交付只有4UML与5ER。
