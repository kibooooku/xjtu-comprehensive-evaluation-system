# 火山 AI MediaKit 标准 OCR 开通与测试

更新日期：2026-10-25。用户已选择先测试 MediaKit 标准版，实际效果确认前，独立探针不替换正式申报系统的腾讯 OCR。

## 1. 开通与创建 Key

1. 登录[火山引擎控制台](https://console.volcengine.com/)，完成账号实名认证。
2. 打开[AI MediaKit 设置页](https://console.volcengine.com/imp/ai-mediakit/settings)。若页面提示尚未开通，按提示开通 AI MediaKit。也可在控制台产品搜索中搜索“AI MediaKit／智能处理”。
3. 进入“API Key 管理”，创建专用 Key，名称可填 `xjtu-ocr-test`。使用 MediaKit 专用 Key 即可，无需为本次测试准备旧版 ImpService 的 AK/SK。
4. 将 Key 保存到项目的 `secrets/volcengine_mediakit.env`，不要粘贴到聊天、源码或截图中。

```dotenv
VOLCENGINE_MEDIAKIT_API_KEY=在等号后填入MediaKit专用Key
```

项目已经忽略整个 `secrets/`。测试程序只在本机后端读取密钥，终端及报告不显示密钥。

[官方凭据说明](https://docs.volcengine.com/docs/Intelligentprocessing/Basicconceptsandpreparationwork?lang=en)明确区分 MediaKit 专用 API Key 与 IAM 通用 API Key。本次采用前者，减少配置步骤。

## 2. 费用及上传方式

测试仅使用标准版，最多8页，不会自动升级到加强版，也不自动重试失败请求。按[公开文字 OCR 计费](https://docs.volcengine.com/docs/Intelligentprocessing/image-tool-billing?lang=en)1.38元/千次估算，8次成功文字识别约0.01104元；这是 OCR 刊例估算，不是云账单，也未将其他可能的计费项算作已核实免费。

充值在火山引擎统一费用中心操作，可直接打开[账户充值页](https://console.volcengine.com/finance/fund/recharge)，或依次进入“费用中心 → 资金管理 → 充值”。是否可以调用取决于账户、服务开通和计费状态；未充值不等于有免费OCR额度，请以用量和账单为准。[官方资金说明](https://docs.volcengine.com/docs/BillingCenter/FundingFAQ?lang=zh)

使用 MediaKit 自带本地上传：申请临时上传地址，上传页图，再用返回的 `mediakit://` 文件标识识别。无需另建 TOS 存储桶，也无需把学生证明材料开放为公网链接。

官方说明预签名上传地址24小时有效，上传素材30天后自动清理。[本地上传流程及保留期](https://docs.volcengine.com/docs/Intelligentprocessing/multisource-media-input-and-local-upload?lang=en)

## 3. 测试范围与评价

从项目已有材料中选取少量代表页，包括普通单页活动证明、先前 AI 分析失败但 OCR 成功的材料、密集参与名单、荧光夜跑证明与名单页。测试输出保存在被 Git 忽略的 `instance/mediakit-test/`，不写正式申报数据库，不改变已审核分值。

逐页检查：

- 具体活动名称、日期能否完整识别；
- 账户姓名、学号、班级的精确命中及对应原页位置；
- 文字框能否用于红框定位，是否过宽或遗漏；
- 密集名单中的识别表现、接口耗时、失败原因和实际尝试次数。

若缺少人工确认的真值，不将识别文字数量或置信度直接称为准确率。有既存腾讯结果的页面可离线对照，不为对照额外调用腾讯。

标准 OCR 返回的是文字块坐标，不能当成专门的印章识别或材料真实性核验。本次先评价文字和坐标，印章仍需单独方案。

## 4. 独立探针

配置好 Key 后，可先离线检查，再明确启用实测：

```powershell
.venv/Scripts/python.exe scripts/probe_mediakit.py --manifest instance/mediakit-test/manifest.json
.venv/Scripts/python.exe scripts/probe_mediakit.py --manifest instance/mediakit-test/manifest.json --cloud --max-calls 8
```

样本清单中的 `image_path` 指向本机页图；可选 `expected_tokens` 保存本地精确核对字段。真实姓名学号仅留在被忽略的测试目录。使用相同页图和版本时，成功结果优先复用缓存，避免重复计费；失败和超时不会自动重试。

MediaKit 图像接口不直接接收500页 PDF。测试按原件页图逐页送入，单图不超过10MB，短边不超过2160像素、长边不超过3840像素；必要时等比例缩小并将返回坐标映射回原图。[OCR API及图片限制](https://docs.volcengine.com/docs/Intelligentprocessing/submit-image-text-recognition-ocr-task-api?lang=zh)

正式需求仍是用户在2026-10-25确认的“长 PDF 自动整份识别，最多500页，额度不足时停止”。本次8页是评估服务效果的测试范围。

## 当前状态

用户已充值10元并更新Key，鉴权已验证通过。8页现有材料均已取得文字结果，6页有既存腾讯文字OCR缓存，可在本机 `instance/mediakit-test/report.html` 对照原图、文字框和缓存字段检查。

共9次文字OCR请求，包含针对1页坐标格式差异的1次诊断重测；按公开单价估算0.01242元，实际以云账单为准。实测原始OCR接口耗时约0.646—1.391秒/页，平均1.054秒，不含本地渲染、预处理及上传。完整8页报告的16张原图/文字框图已通过本地浏览器显示检查，无外部网页请求。

夜跑证明的一处文字框上下坐标逆序5像素。探针保留该处识别文字并标注定位异常，不交换坐标或绘制错误红框；其余正常框继续显示。已保存的白名单结果经离线重解析复用，没有再调用该页。普通文字OCR并不提供专门的印章检测或真实性鉴别。

现有系统96项回归与独立探针9项模拟测试通过；真实学生材料、密钥、私有测试ledger和报告均未进入Git。正式申报系统仍使用原腾讯接口，MediaKit先作为独立评估方案。

若再次遇到HTTP403且具体提示API Key不存在，可回到MediaKit的API Key管理页，确认Key启用，复制完整密钥值而非Key ID或名称，更新同一个本地配置文件后再测。此类鉴权错误不通过追加充值解决。
