# 2026-09-21 实际用户 ConnectTimeout 修复

用户 trace：`artifacts/voice/20260921-195013-8255b6/events.jsonl`。

- 语音转写已完成；计算器已执行清空和输入算式，随后请求下一步 Jev 决策失败。
- 最后 3 次到 `api.typesafe.ai/v1/systemone` 的连接分别在 4,005 / 4,017 / 4,012 ms 超时，整个模型请求 13,550 ms 后暂停执行。
- 更早的 Jev 和 OpenRouter 请求也出现连接超时，重试后才成功。不是单纯的语音识别或工具执行失败。
- 原报错 `no action executed` 指该模型请求之后没有派发新操作，不能理解为整个会话什么都没做。

## 已部署改动

使用持久 HTTPX AsyncClient / AnyIO 连接池，支持 Happy Eyeballs；连接预算 10 秒、响应读取 25 秒、空闲连接保留 60 秒。没有绕过 TLS 验证，没有替换服务域名，没有修改系统 VPN，没有重放计算器操作。

连接失败提示现明确区分 Jev / OpenRouter、建连超时 / 响应超时、尝试次数，并说明此前已执行动作不会撤销。模型请求仍有 3 次有界重试，工具修改不重试。

停止已失败且执行禁用的录音会话、保存状态、重启 8767 服务。新 runtime 与工作树启动快照一致，证据 `artifacts/network-repair/deployed.json`。此前的异步连接池和前台观测工作树改动已随这次重启加载；这不代表尚未解决的 Raise 循环已经通过。

## 验证和剩余问题

冻结同一失败请求做纯模型调用，不操作桌面：Jev / OpenRouter / 再次 Jev 均返回有效响应，耗时分别为 42,648 / 16,051 / 16,610 ms。证据在 `artifacts/network-repair/verified-requests`。调用成功不代表延迟合格。

无认证的连接阶段探针显示，当时 Jev TCP 5,308 ms、TLS 6,007 ms，OpenRouter 同期总耗时 1,699 ms。当前链路波动明显，不能把所有问题归因于 IPv6，也不能承诺放宽超时就能消除外部网络故障。原始证据 `transport-phases.json`；直接 TCP 3 秒探针失败记录在 `current-reachability.json`。

117 个离线 pytest、Ruff、前端 JS 语法与构建通过。没有以这些离线检查替代网络稳定性或真实任务验收。

## 后续独立故障：文本参数 abstention（尚未部署）

用户会话 `artifacts/voice/20260921-210015-fd598e/events.jsonl` 在计算器输入后，OpenRouter 返回合法的 `{"text": null}`；旧执行器将其当作通用 ValueError，暂停执行。这与 ConnectTimeout 无关，也不是 JSON 格式错误。

现在严格区分单字段 null 与格式错误：null 不派发操作，同一输入版本下保持聆听并在工作台显示“文本模型未提供参数”；如果生成期间已有新语音，重新观察并决策。后续输入可正常唤醒。格式错误和网络异常仍明确暂停；不会把空参数写入应用，也不会循环重试同一个输入。此改动只解决错误暂停，不证明模型能自行纠正选错的操作或已有计算器内容。

验证：142 个离线 pytest、Ruff、两个前端 JS 语法检查、离线构建通过。当前用户仍在录音，因此没有重启公共服务，也没有操作其应用做真实回归。
