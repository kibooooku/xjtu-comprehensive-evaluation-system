# 接口实现依据

核查日期：2026-10-24。实现只依赖官方接口资料；这些链接是技术依据，不改变已对齐的业务需求。

- [腾讯云高精度通用文字识别](https://cloud.tencent.com/document/api/866/34937)：`GeneralAccurateOCR`，版本2018-11-19，接收图片Base64，返回文字和原图多边形坐标。
- [腾讯云印章识别](https://cloud.tencent.com/document/api/866/45807)：`SealOCR`，使用`SealInfos`提供多个印章的位置与文字。识别结果只辅助人工核验，不证明印章真伪。
- [腾讯云签名方法v3](https://cloud.tencent.com/document/api/213/30654)：TC3-HMAC-SHA256，UTC日期、实际HTTP请求字节参与签名。
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)与[JSON输出](https://api-docs.deepseek.com/guides/json_mode/)：兼容HTTP聊天接口，显式JSON输出，服务端再次校验结果。默认使用当前文档的`deepseek-flash`，可用`DEEPSEEK_MODEL`调整。

腾讯云账户关闭后付费是免费使用的账户边界。本地每月预算只是一道额外保护，不能代表主子账户共享的云端剩余额度；超预算、额度用尽、凭据或网络失败都进入手工流程。程序不购买资源包、不启用后付费、不自动重试失败请求。
