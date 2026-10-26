# OCR本地保护与云端资源包

## 本次误报

现场OCR任务的停止原因是 `LocalBudgetExceeded`，来自系统默认每接口每月100次的本地调用保护。印章接口有100条本地尝试记录，其中41条成功、58条为 `FailedOperation.OcrFailed`、1条为请求前的 `ImageTooLarge`。未观察到腾讯返回资源包耗尽。前端又通过“免费”“预算”等文字推断云余额，错误显示成“腾讯云免费额度不足”。

本地默认保护上限与本机显式配置均调整为每接口每月1000次尝试；保留原始计数及失败记录，不伪造云端用量或清零保护。腾讯高精度文字的免费包为每月1000次，印章使用行业文档识别共享免费包1000次，具体可用余额仍以账户实际资源包和腾讯控制台为准。[腾讯免费资源说明](https://cloud.tencent.com/document/product/866/35945)

`FailedOperation.OcrFailed` 在官方错误码表中是不计费错误。本地尝试含失败和预留，因此不能从本地100条记录推断腾讯扣了100次或免费包耗尽。[错误码计费说明](https://cloud.tencent.com/document/product/866/45470)

## 停止来源

OCR结果保留兼容字段 `stop_reason`，增加 `stop_code`、`stop_source`、`stop_message`、`stop_details`，并向依赖的AI任务传播。前端优先使用结构化来源，旧记录只按明确错误码兼容；不根据警告中的“免费/额度”等自然语言猜余额。

| 来源 | 例子 | 用户提示原则 |
| --- | --- | --- |
| LOCAL_LIMIT | LocalBudgetExceeded | 达到本地调用上限，未查询腾讯实际余额 |
| PROVIDER_RESOURCE | 明确的ResourcePackageRunOut | 腾讯返回资源包不可用或耗尽 |
| RATE_LIMIT | RequestLimitExceeded、一般Quota限制 | 腾讯限制请求频率或配额，不宣称余额不足 |
| BILLING | InArrears、ChargeStatusException | 腾讯服务计费状态异常，不直接等同免费包耗尽 |
| AUTH | AuthFailure、Unauthorized | 密钥或子用户权限问题 |
| NETWORK | NetworkError、HTTPError | 请求超时、连接或HTTP服务异常 |
| CONFIGURATION | NotConfigured | 云识别未配置或关闭 |
| INPUT_LIMIT | ImageTooLarge、LimitExceeded.TooLargeFileError | 当前页输入限制，不能误报为资源不足 |
| PROVIDER_UNAVAILABLE | 未开通或服务资源不可用 | 服务状态问题，余额未知 |
| UNKNOWN | 无可确认的错误码 | 说明识别停止，保留结果，提示原因未确认 |

`/api/integrations/status` 明确公开本地尝试数与本地限制，实际云余额标记为未知。系统不声称已查询腾讯控制台，也不请求扩展子用户权限。账户继续关闭后付费；不购买资源包、不自动切换服务商，腾讯明确返回资源不可用时停止调用。成功OCR缓存及已完成的本地PDF文字提取继续保留。

## 数据边界

新规则对所有识别任务生效；旧结果通过错误码正确解释，不改写历史识别事实。增加本地限额本身不会发起OCR。恢复被本地保护拦下的可编辑任务时使用正常任务队列，先复用成功缓存，保持学生手工内容、截图和已冻结审核记录的保护。

## 验证

相关后端118项及追加错误适配4项通过，验证新旧字段、明确错误来源、大小限制、缓存与AI依赖传播；前端OCR政策21组及页面稳定性14项通过，覆盖13种停止情形和“含免费说明的正常结果不得误判”。[浏览器报告](screenshots-v2/browser-ocr-policy-mock.json)、[虚构界面截图](screenshots-v2/18-ocr-stop-source-mock.png)。自动测试均使用模拟服务，无真实云调用。

实际受影响的可编辑OCR任务通过正常队列恢复后成功结束，4项任务均已处理，43页原生文字复用、1页补充文字成功，停止标记为false。该次恢复实际腾讯文字1次成功、印章3次（1次成功、2次OcrFailed），未调用AI、未改分或提交；腾讯没有返回资源包耗尽。调用前另做数据库备份，私有恢复记录保留在被Git忽略的instance目录。
