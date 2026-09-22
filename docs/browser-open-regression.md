# 打开浏览器、前台显示、打开 Google：真实故障回归

2026-09-21，原始用户语音：`嗯，打开一个浏览器，推到前台，然后打开谷歌这个地址。`

## 原始故障证据

`artifacts/voice/20260921-183840-39b124/events.jsonl`：ASR 正确识别整句（v11）。执行历史仅有一次 Chrome `inspect`，没有网页导航或前台切换。此后模型反复 LISTEN。不能把“读取 Chrome”计作“打开浏览器成功”。

同一冻结输入重放在 `artifacts/browser-open/debug`：原请求选择 LISTEN；共享候选目标后选择 TYPE_TEXT。原桥接只在各目标题提供候选，操作题缺少原版共有的目标上下文。

## 本次修正

- 把所有已观测操作目标放入共同 state；仍只消费被选操作的目标题。
- 在已连接的浏览器层提供 NEW_TAB，不再要求先进入现有网页。支持继续观测新建的 about:blank 标签页。
- 明确 SWITCH_APP 是读取操作，不隐含启动、前台切换或导航；前台显示仍由独立 SHOW_TAB 执行。
- 共享观测增加页面 URL、前台应用信息及其缓存年龄；SHOW_TAB 后不把切换前的缓存误当成失败证据。
- SHOW_TAB 返回后通过常量 AppKit 只读脚本记录即时前台 bundle ID。脚本没有用户/模型参数，不操作 UI、不修改 cua-relay；实际操作仍走 Browser Harness / MCP。这样后续用户切走窗口不会抹去刚才确实切到前台的证据。
- 在仅做过 inspect 的情况下也可审查过早 LISTEN；不把“没有进行过修改”当作无需审查的理由。
- URL 参数提示允许文本模型把明确的公共网站名称解析成完整 URL；没有网站名称映射表或语音关键词匹配。
- 模型 HTTP 连接超时从每地址 25 秒缩短为 2 秒，响应读取仍为 25 秒，原有模型请求重试策略不变。没有重试浏览器修改。

## 实验结果与失败保留

- live-v1/v2：测试环境认证/连接初始化问题，未形成有效业务回归。
- live-v3：新建 Google 并调用显示标签页；延迟读取前台时已是另一应用，不能据此认定显示接口失效。随后即时检查确认 `Target.activateTarget` 能切到 Chrome。
- live-v4：已加载 Google 时只读取 Chrome 并 LISTEN，缺少前台状态，失败。
- live-v5：导航和即时前台检查通过，但重复 SHOW_TAB；不能算稳定体验。
- live-v6：文本模型返回 `{"text": null}`，执行器停止；失败保留。
- live-v7：导航和前台通过，仍重复 SHOW_TAB。
- **live-v8（最终代码）**：按原 trace 的时间顺序回放 11 次转写更新，经过正式 `Workbench → LiveSession → AutomaticDesktop → Jev / OpenRouter → Browser Harness`。
  - 3 次真实修改：NEW_TAB → NAVIGATE → SHOW_TAB。
  - Google 标题和 URL 独立读取正确；SHOW_TAB 后立即读取系统前台为 `com.google.Chrome`。
  - 录音工作台标签页保持原 URL，没有执行错误；没有重复显示动作。
  - 首次转写至前台 20,178 ms；最终转写至前台 12,258 ms；9 次模型请求。
  - 两次文本请求分别 5,943 ms 和 2,392 ms。仍不是即时体验。

最终证据：`artifacts/browser-open/live-v8/{verification,foreground,measurements,state}.json`，原始请求/响应/工具 trace 位于其会话子目录。`scripts/check_open_browser.py` 是手动付费实验，不进入 pytest。它回放的是转写事件，不是新的物理麦克风/ASR 测试；不能据此声称声纹、闲聊过滤或全部 100 题已通过。

网络独立探针 `network-timing.json` 显示 51,091 ms 中 TCP 连接消耗约 50,267 ms；缩短连接超时后的 `network-short-connect.json` 为 5,277 ms，TCP 约 4,281 ms。解析顺序是两个 IPv6 地址再到 IPv4，这与前两个地址等待超时的现象吻合；不能把这些时间算作模型推理耗时。

清理了本次实验创建的 4 个多余标签页，保留最终 Google 标签页，记录见 `artifacts/browser-open/cleanup.json`。

检查：Ruff、114 个离线 pytest、app.js 语法检查、uv build 通过。本地服务已重启加载修正。未提交或推送代码。

## 后续补测：单次通过不等于稳定（同日）

后续优化更换为 HTTPX AsyncClient + AnyIO 的持久连接池，同步调用层与串行执行保持不变。免费接口的两组新连接对照：同步 5,142 / 5,677 ms，异步 1,813 / 1,304 ms，原始证据 `artifacts/browser-open/async-network-probe.json`。异步连接使用 Happy Eyeballs，避免依次等待不可达地址；2 秒连接预算在真实 VPN 环境出现三次 ConnectTimeout，因此最终连接预算放宽到 10 秒，读取仍为 25 秒，连接池保活 60 秒。

`live-v9` 的转写回放三项结果检查仍通过，但从最终转写到前台为 **21,638 ms**，比 v8 更慢；Jev 单次请求出现 6,500 / 4,344 ms，不能用网络探针结果声称端到端提速。记录见其 `measurements.json`。

又用 macOS Tingting 合成同一句话，经 PCM 实时送入本地 Qwen3-ASR（绕过物理麦克风和 AudioWorklet），而非直接注入转写：

- `audio/result.json`：ASR 正确，创建后台页并导航 Google，但没有记录 SHOW_TAB 即时前台核验便 LISTEN，前台结果未证实。
- `audio/result-fresh-focus.json`：模型连接三次 ConnectTimeout，零修改；这是网络失败，不是语音识别失败。
- `audio/result-background-explicit.json`：初始工作台页面下，模型读 Chrome 原生窗口后反复 Raise，同一任务在 124.8 秒实验预算后停止，**失败**。没有把超时或工具返回记为成功。原始 trace：`artifacts/voice-audio/20260921-194750-54324d/events.jsonl`。

为减少信息歧义，工作树已增加每次观察时的实时 NSWorkspace 前台读取，并明确 NEW_TAB 创建的是后台页。这些改进没有消除上述 Raise 循环，故不能宣称该指令已稳定修复。新连接池、实时前台读取和后台工具描述的本轮改动尚未重启到公共 8767 服务；公共服务仍是上一轮 runtime `388f6ceb9f19e57c`。保留该区别，下一轮需优先定位原生 Chrome 路径与自观察工作台对决策的影响。

本轮 Ruff、116 个离线 pytest、两份前端 JS 语法检查及 uv build 通过；这些检查不替代失败的真实语音实验。

## 控制工作台边界与文本响应格式（20:20 后）

- `audio/result-discovery-show.json` 曾通过原生地址栏把录音工作台导航到 Google。这不是成功：测试已恢复原工作台标签页；桥接层现在在原生 AX 观察命中自身工作台 origin 时不暴露该窗口的修改动作，保留浏览器级 NEW_TAB 和已发现任务页的 SHOW_TAB。此边界与浏览器发现排除控制页一致，不基于语音关键词。
- 已发现标签页可直接成为 SHOW_TAB 目标，不需要先读取页面；执行前核对实际 URL，执行后记录系统前台应用。
- `audio/result-control-boundary.json` 从明确显示的工作台开始，以实时 PCM 经本地 ASR 输入。Jev 显示已观察到的空白任务页，然后选择 NAVIGATE。OpenRouter 返回正确网址但附带孤立 Markdown 结束标记，旧解析器拒绝，故此次仍为失败；原始 trace `artifacts/voice-audio/20260921-202048-0e5355/events.jsonl`，响应 seq 72。
- 新解析器只兼容 JSON 外的 Markdown 围栏，不从说明文字中提取值；仍拒绝多结果、额外字段、空值。原始 seq 72 响应经新解析器得到原样网址，无模型重试、无桌面操作。覆盖字面文本内部围栏保真及非法输出拒绝的离线测试。
- 下一次实机测试在启动前被 Browser Harness 阻止：日志明确为 Chrome remote debugging turned off。没有把这次启动失败算作业务回归，也没有修改用户正在使用的页面。
- 本轮修复已加载到 8767 服务，runtime `b49643c6000dbdae`；`artifacts/browser-open/text-framing-deployed.json` 记录部署与冻结响应验证。132 个 pytest、Ruff、两份前端 JS 语法检查、离线构建通过。完整语音→导航→前台→工作台保持的修复后实机验收仍待验证。

## 连接状态传递修复（工作树，尚未部署）

当前公共会话仍处于 recording/enabled，不重启或运行桌面实验。静态检查发现：discover 已记录 browser_connected/errors，但 AutomaticDesktop 丢弃了这些字段，导致 Jev 不知道网页工具为何缺席。

现在观察结果、Jev 请求、会话快照和 trace 共享 tool_availability：明确浏览器是否连接、发现错误、哪些观察界面是受保护的录音工作台。前端在录音区显示断连与保护原因。恢复后下一次观察同时清除错误并重新提供网页工具。未知连接状态保持 null，不伪报成功。该改动不新增工具、不使用关键词路由，也不代表浏览器已经重新连接。

验证：断连/恢复的同一目录状态测试确认 Jev 请求与 NEW_TAB 可用性一致；143 个离线测试、Ruff、两个 JS 语法检查及离线构建通过。真实浏览器恢复与语音任务仍待验证。
