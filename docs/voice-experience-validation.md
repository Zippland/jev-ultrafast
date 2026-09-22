# 持续语音体验验证 · 2026-09-21

目标是持续接收语音、跟随补充和改口，并在用户停顿时继续尚未完成的操作。
当前链路已经可试用；尚未验证任意长任务，也尚未达到所有操作都近乎即时。
前一版本的原始结果保留在 [第一版记录](voice-v1-validation.md)，不覆盖失败数据。

## 本轮实现

- 应用发现第一次等待真实列表，此后后台刷新；不再每步等待约 2.4 秒的 `list_apps`。
  缓存年龄和读取失败都记录在 trace。选定目标后仍读取和校验真实状态。
- 自动路由复用原生操作返回的完整 AX；下一次操作前仍重新获取租约、核对指纹。
- 改口判断集中到真正派发前，覆盖选择、文本生成和状态读取期间的新语音。
  判断的是具体动作、参数和生成文字；没有关键词／正则意图判断。
- 移除两分钟自动关麦。ASR 的活动音频段仍最多 45 秒，定稿队列最多 2 段，前端上传有界。
  当前会话仍有 256 段／48000 字和每次激活 80 次决策的资源边界，不能视为无限长任务支持。
- 当前界面提供模型可选的 `WAIT`，等待后重新观察，无需用户新增语音；`LISTEN` 则休眠等新输入。

## 同音频复测

下表从 ASR 就绪开始计时，包含用户说话时长、网络和真实应用操作；不包含首次加载 ASR。
这是少量同音频实机对照，网络与识别时序有波动，不是总体性能结论。

| 用例 | 首次派发：之前 → 本轮 | 最后操作返回：之前 → 本轮 | 独立结果 |
| --- | --- | --- | --- |
| 计算器清空，计算 2 + 3 | 11.860 → 8.896 秒 | 56.194 → 31.120 秒 | 新 AX 显示 5 |
| TextEdit 写入后，浏览器填同文并搜索 | 15.162 → 12.381 / 12.688 秒 | 29.031 → 17.300 / 18.178 秒 | 正文、输入、一次搜索均为“语音联动成功” |

计算器本轮 9 次 Jev、0 次 OpenRouter，5 次操作；之前 8 次 Jev。
跨 App 两次复测各 13 次 Jev、2 次 OpenRouter，3 次操作；之前 11 次 Jev、2 次 OpenRouter。
以上复测 HTTP 尝试次数均等于模型请求次数；没有把传输重试隐藏在计数中。
执行提速不等于请求更少，时序改变也会改变部分转写与判断重叠的次数。
一次后台应用发现发生 `Official direct Computer Use broker cleanup failed`，旧列表继续可用；错误保留在 trace。

最后一次跨 App 验收时，TextEdit 的当前窗口已经变为另一文档，按测试标题绑定的 AX 检查因此拒绝。
运行 trace 显示写入目标始终是 `artifacts/cu100/JEV-CU100-SANDBOX.txt`。
随后用只读 AppleScript 按完整路径核对该文档正文，并与浏览器独立事件记录核对，全部符合预期；
验收没有切换或修改另一文档。不能把 App 当前窗口等同于指定文档身份。

## 超过两分钟的连续会话

一条 131.389 秒的中文 WAV：先要求填写但不要搜索，在第 125 秒追加改口并要求搜索。
前端实际采集 134.4 秒，使用真实 MediaStream、AudioWorklet、本地 Qwen、Jev、OpenRouter 和浏览器。

- 首次填写后进入 `LISTEN`，长停顿期间没有模型空转请求。
- 第 125 秒的新语音继续进入同一录音会话；修改后只搜索一次，未提前搜索。
- **逐字准确性失败**：预期“连续语音甲／连续语音乙”，Qwen 分别识别成“连续语音夹／连续语音语”。
  下游照转写执行。这条用例仅确认持续收音和后续跟随正常，不能计为完整任务成功。

## 证据与检查

- `artifacts/voice-smoke/v2-v3-verification.json`：比较数据、模型调用与准确性判定。
- `artifacts/voice-smoke/v2-calculator-independent.json`：新 AX 结果。
- `artifacts/voice-smoke/v2-crossapp-independent.json`：第一次复测的独立 AX / DOM。
- `artifacts/voice-smoke/v3-crossapp-independent.json`、`v3-document-oracle.json`：最终按路径核对的文档及浏览器事件。
- `artifacts/voice-smoke/v2-continuous-browser.json`：长停顿测试的浏览器事件。
- `artifacts/voice/20260921-164058-534f1f/`：计算器。
- `artifacts/voice/20260921-164302-ff5afe/`：持续语音。
- `artifacts/voice/20260921-164659-bbfa1f/`、`20260921-164935-355e43/`：跨 App 两次复测。

92 项离线测试、3 项音频重采样检查、JS 语法、Ruff 和构建全部通过。
上述音频来自中文合成 WAV，并非物理麦克风验收；100 题全量验收尚未完成。
原生工具仍通常需要数秒，ASR 同音字错误仍会影响结果，长任务及任意原生窗口路由仍需验证和完善。

试用地址 `http://127.0.0.1:8767/`，在 Chrome 中开启麦克风；首次加载本地模型需要等待。
说完后可以保持安静，执行会继续；关闭麦克风会暂停后续派发。所有模型和工具记录均可在页面下载。

### v9：加载最新桥接层后的真实计算器复测（2026-09-21）

确认公开工作台录音已结束且执行暂停后，重启8767加载最新代码。隔离PCM回放经本地Qwen ASR、JEV、独立cua-relay操作真实计算器；文本辅助探针没有接入。
初始独立AX记录为23×17=391（全部清除可见）。模型依次选择清除、2、加、3、等于；结束后另建MCP连接独立读到2+3与5，结果通过。

- ASR就绪至首个动作派发9820ms，至最后动作返回35438ms，10次模型请求。
- 结果：`artifacts/voice-smoke/v9-verification.json`；前后AX：`v9-before.json`、`v9-independent.json`。
- Trace：`artifacts/voice-audio/20260921-180536-9b7f68/events.jsonl`。
- 该运行没有覆盖部分表达式清空失败，也没有验证新状态读取复用的真实加速；不能据此声称性能改善。它仍显著慢于“几乎即时”的目标。
- 音频为实时PCM回放，不经过物理麦克风或AudioWorklet。没有改变用户的默认模型分工。

### v9 耗时拆解及后台目录竞争实验

`v9-latency-breakdown.json` 以ASR ready至最后action.returned为窗口，分别裁剪实际span：
模型8次共4966ms、get_app_state 6次共13257ms、click 5次共14996ms。
任务结束后的LISTEN与结果审查不在这个窗口内，因此这里8次与整轮10次模型请求不矛盾。
list_apps在窗口中5次共19329ms，但与前景调用重叠，不能累加为总耗时。

新增只读实验 `scripts/check_relay_contention.py`：两个独立公共MCP连接，预热后交替比较
单独读取计算器与并发list_apps时读取计算器，各3次，所有请求和响应保存在
`artifacts/relay-contention/v1/events.jsonl`，运行脚本副本与结果同目录保存。

- solo：3484、3520、3529ms，中位数3520ms。
- concurrent：3537、3571、3578ms，中位数3571ms。
- 本轮中位数差51ms（约1.4%），不足以支持“后台应用发现造成主要延迟”的判断。
- 读取耗时与v9不同，说明跨轮比较存在环境波动；不以跨轮差值宣称优化。

这轮没有修改目录刷新策略，也未改变CU仓库。优先问题仍是公共CU调用本身的秒级开销，
后续应验证在保持JEV选择与文本模型生成参数分工的前提下，能否用现有文本输入工具减少必要交互次数。

### 连续键盘输入选择实验与v10实测

`benchmarks/addressing/keyboard_choice.py` 用6份真实计算器快照做付费、无GUI对照。
`artifacts/addressing/keyboard-choice-v1` 中仅扩展INSERT_TEXT工具说明，6次均未改变原来的选择。
v2同时加入通用连续输入偏好，清空0快照改选INSERT_TEXT，文本模型生成`2+3`（1214ms）；
其他阶段仍清空、点击加/3/等于、完成后LISTEN。共18次JEV请求、1次文本请求，未部署生产策略。

`v10-keyboard-audio.json` 为随后真实PCM→Qwen→JEV→CU回放：本轮隔离进程只加入策略偏好，
**未带上v2修改后的工具说明**，所以不是v2完整配置的实测复现。实际仍点击清空、2、加、3、等于，
独立新MCP读取确认2+3=5，10次模型调用，ASR ready至最后动作返回25616ms。
调用次数未减少，不能把相比v9耗时下降归因为连续输入优化。
Trace为`artifacts/voice-audio/20260921-181030-4a8ec7/events.jsonl`，该目录保存实验入口副本。
后续需补测完整配置；默认策略未改变。

### v11：完整连续输入配置的真实验证

完整配置同时扩展INSERT_TEXT说明并加入通用连续输入偏好。真实PCM经Qwen识别后，JEV选择
清空→INSERT_TEXT→等于，文本模型实际生成`2+3`；独立新MCP连接读取到表达式2+3和结果5。
操作从之前的5次减少为3次，ASR ready至首个派发7305ms、最后返回19913ms，模型调用9次。
单轮耗时不能当作稳定加速比；调用次数下降是本轮可直接验证的变化。

证据：`artifacts/voice-smoke/v11-measurements.json`、`v11-independent.json`；完整trace及实验入口副本：
`artifacts/voice-audio/20260921-181219-e7183e/`。
`keyboard-choice-revised-v1`追加验证三份人工回改文字：取消→LISTEN、改二加四→4、背景话语→继续3，均符合预期。

据此将相同工具说明与通用偏好纳入actions.py/mixed.py。未增加计算器特例、文本匹配或自动操作脚本；
没有改变JEV选择目标、OpenRouter只生成字面参数的职责。部分表达式清空、跨应用泛化及物理麦克风体验仍未验证通过。
实验入口check_voice_keyboard.py现转调当前生产回放器，不再重复追加提示。

### v12：当前生产策略的 TextEdit → Browser Use 实测

使用现有crossapp.wav实时PCM回放，经本地Qwen、当前JEV策略和OpenRouter驱动真实应用。
准备阶段通过TextEdit窗口菜单选择JEV-CU100-SANDBOX.txt，并核对完整文件URL；用户的“未命名”文档未编辑。
将测试正文设为“待进行跨应用测试”，重置已有8770实验页为voice-crossapp-v12后运行。

独立新MCP读取确认专用文档正文为“语音联动成功”；新BrowserTools观察确认关键词同文；
网页独立事件记录确认仅一次同文搜索。五项检查全通过，见`artifacts/voice-smoke/v12-verification.json`。
原生AX、网页观察及事件证据分别为v12-independent.json、v12-browser-observation.json、v12-browser-oracle.json。

实际动作：选择原正文→替换正文→填写网页关键词→点击搜索，共4次；包含一次冗余的选择文字，未宣称最优流程。
18次模型请求，ASR ready至首个操作派发11230ms、最后返回21585ms。
Trace：`artifacts/voice-audio/20260921-181611-2d34f5/events.jsonl`。

这证明当前策略在本次任务中可由模型自动选择TextEdit的CU及浏览器通道，不是多任务稳定性、物理麦克风或长程验收通过。

### v13长停顿失败与v14字段替换修复

测试器check_voice_audio.py原固定120秒截止，会截断131.389秒素材，现改为音频总时长+120秒执行余量。
v13通过真实浏览器验证第125秒的第二段继续进入同一录音；115345ms静默区间内模型调用为0，最终只搜索一次。
但第一段出现连续插入“连续”及“连续语音夹”，导致字段为“连续连续语音夹”；整条用例失败。
最后改口后字段恢复为ASR所写的“连续语音语”，仍与预期“连续语音乙”不同，不能算逐字正确。

收窄mixed.POLICY：用户指定完整字段值时选TYPE_TEXT（包括修订早先未完成的转写）；INSERT_TEXT用于明确插入，
或没有可替换字段的应用键盘输入，连续输入偏好只适用于后者。没有用字符匹配/正则决定用户意图。
`artifacts/addressing/field-replacement-v1` 中两份失败快照均改选TYPE_TEXT；计算器快照改选点击2，
因此此前连续输入提速不应继续被视为当前策略稳定保留的能力。

v14真实回放第一段音频，网页独立事件值为“连续语音夹”，未重复前缀、未搜索；修复了该次重复追加，
ASR“甲→夹”错误仍保留。108项测试、Ruff、JS语法及构建通过，空闲时重启8767加载修复。

证据：v13-verification.json、v13-browser-oracle.json、v14-browser-oracle.json（均在artifacts/voice-smoke）。
Trace：artifacts/voice-audio/20260921-181823-59c4c7/及20260921-182152-448c8d/。
本轮不是物理麦克风测试；未宣称长程、全部输入更新或所有应用已可靠。

### 本地ASR语言与场景上下文对照

当前worker对Qwen调用为language=None、initial_prompt=None。识别适配层支持显式language和system_prompt，
因此新增`python -m scripts.check_asr_language <输出目录>`在配置的本地ASR解释器里直接对照。
实验使用相同取消感知解码路径；输入为整条WAV，不经过VAD/流式分段，也不操作应用、不调用远端模型。

`artifacts/asr-language/v1/results.json`：4条既有合成音频 × 自动语言/强制中文/强制中文与通用电脑操作上下文，12次本地推理。
计算器与跨应用指令三种设置内容一致；连续语音第一段仍将甲写为夹，第二段仍将乙写为语，三种配置均未修复。
上下文仅说明用户在中文口述计算器、文本编辑和浏览器操作，未提供预期答案或甲乙词表。
运行脚本、识别源码及音频/模型配置哈希同目录保留。固定顺序单次运行包含预热差异，耗时不用于证明提速。

没有变更默认ASR参数，没有做同音字替换。这一结果只说明简单语言/领域上下文配置不能修复这两条合成素材；
不能区分合成器发音影响与真实麦克风识别质量，更不能证明声纹或旁人过滤能力。

## 2026-09-21 文本参数使用最新转写

历史 trace `artifacts/voice-audio/20260921-194424-ea3aa7/events.jsonl` 的 seq 31 显示：文本请求发起时最新输入已为 version 6，而 helper 仍使用 choice 开始时的 version 1。另一个 trace `20260921-181823-59c4c7` seq 196 同样为 version 14 对 15。选择模型耗时期间的新信息未送入文本模型，容易生成过时参数，随后被派发检查否决，再重新选择和生成。

LiveSession 现在在调用文本模型前重新取得输入快照，trace 同时记录文本输入的 version 和原选择的 choice_version。候选动作仍保持原选择版本，派发前的 Jev 语义核验仍然执行，不能因为文字使用了新版输入就视为旧目标自动有效。生成期间继续发生的输入更新也沿用已有核验。

两项并发测试覆盖：选择期间收到定稿后仅生成一次最新文字；核验接受才派发，核验拒绝不派发。原有生成期间改口、原生 freshness 期间改口、暂停与不确定修改不重放测试保留。134 项离线测试、Ruff、app.js 语法及离线构建通过。尚未取得此修改的真实语音端到端延迟改善数据，不据此宣称即时体验达标。

## 2026-09-21 浏览器断线不再阻塞计算器执行

公开工作台无活跃录音时，经实时 PCM→本地 Qwen ASR→Jev→真实 cua-relay 做两轮“请在计算器里清空，然后计算二加三”。没有用直接转写替代 ASR；没有物理麦克风验收。

- 修复前 `artifacts/voice-audio/20260921-203051-ae93f6`：浏览器远程调试关闭，每轮 discover 同步 ensure_daemon。7 次 observation 分别 13,543 / 11,467 / 9,875 / 9,876 / 9,916 / 9,919 / 11,521 ms。整个 trace 107,259 ms；Jev 把删除一个字符当作清空，最终是 72+3=75，BLOCKED。明确失败。
- 修复后 `artifacts/voice-audio/20260921-203314-e5ce03`：8 次 observation 为 3,456 / 44 / 1,938 / 55 / 46 / 49 / 45 / 54 ms，整个 trace 44,415 ms。之后单独调用 get_app_state，真实计算器显示 2+3、5，证据 `artifacts/voice-smoke/browser-reconnect-calculator-ax.json`。本次结果通过，但初始值和动作轨迹不同，不能把总耗时差全部归因于连接改动。

实现：每轮只做有界的只读目标发现；失败后将 ensure_daemon 放到独立线程，重连期间不保留旧标签页目标，不拖住原生应用循环。成功后仍实时读取目标，因此新建或关闭标签页不会被缓存掩盖。重连成功唤醒仍启用的语音会话重新观察，不恢复已暂停任务，也不伪造用户输入；失败重连不唤醒模型空转。

136 项离线测试、Ruff、app.js 语法、离线构建通过。第二轮加载了异步重连，但运行过程中补充的重连成功唤醒仅经离线并发测试验证。44 秒仍远未达到即时体验；清空行为在不同起始状态下也没有证明稳定。

## 2026-09-21 工具成本信息对照：尚不能部署为加速方案

对最新44秒计算器trace分解：大多数Jev请求414–747ms；get_app_state约1.7秒、click约2.8秒，逐字符点击导致多轮原生往返。先前已验证过连续输入提示，但当前策略仍有逐字符路径，不能重复宣称该问题已解决。

`benchmarks/addressing/tool_cost.py` 对同一真实trace三个冻结状态（清空后、输入2后、点击等于前）做baseline/加入历史公共MCP耗时中位数的六次付费选择，无GUI操作。证据 `artifacts/addressing/tool-cost-v1`。仅清空后从CLICK改为INSERT_TEXT；另两个状态仍选择原来的加号/等号，未省略必要操作。历史成本来自两轮trace，属于离线对照上下文，不是实时生产估计。

随后用正式OpenRouter文本参数生成器处理该INSERT_TEXT选择。结果只生成`2`，而非完整`2+3`；1649ms，证据 `artifacts/addressing/tool-cost-text-v1/result.json`。因此此变化没有证明减少操作数，可能反而增加文本模型调用。没有将成本提示加进生产策略。下一步需要检验choice/text之间的操作粒度是否一致，而不是继续添加偏好规则。此次仅新增实验脚本与证据，公共服务版本保持不变。

## 2026-09-21 choice/text 操作定义交接与真实复测

mixed_field_text 原先传目标、用户语音、观察、历史、字面参数说明，但不传 Jev 所选操作的共享定义。新实现从同一个 OPERATIONS/DESCRIPTIONS 表传 operation.name/description，不增加语音关键词或计算器特例，不让文本模型选择工具。

`benchmarks/addressing/text_handoff.py` 重放同一清空后状态，baseline 三次为2、2、2+3；带共享 INSERT_TEXT 操作定义三次均为2+3。证据 `artifacts/addressing/text-handoff-v1`，6次文本模型请求，无GUI操作。样本很小，不当作稳定成功率。

真实PCM→本地Qwen→Jev/OpenRouter→cua-relay复测：`artifacts/voice-audio/20260921-203841-18364b`。没有加入上一轮的历史成本提示。实际修改为全部清除→INSERT_TEXT（OpenRouter输出2+3）→等于；最后动作返回27,649ms（相对Trace启动，包含启动等待；不是纯模型耗时）。随后独立新MCP get_app_state确认表达式2+3、结果5，证据 `artifacts/voice-smoke/operation-contract-calculator-ax.json`。相比上一轮5次修改，本轮3次，但不能由单轮时间差推断稳定加速比，也不能证明所有初始状态下清空均正确。

137个离线测试、Ruff、app.js语法、离线构建通过；修复已重启加载到8767，运行指纹见 `artifacts/voice-smoke/operation-contract-deployed.json`。物理麦克风、连续改口、长程与跨应用广泛验收仍未完成。

## 2026-09-21 连续开麦与前后两种改口时机

保持已部署代码不变，Tingting 合成两段语音“请在计算器里清空，然后计算二加三”“不对，改成二加四”，拼接静音，以实时PCM通过本地Qwen ASR和真实cua-relay。测试入口没有注入正确转写或预先给模型答案。音频与结果均保存在 `artifacts/voice-smoke/correction`。

1. 中间静音2秒（combined.wav）：修正到达时还未输入原算式。模型清空、一次输入2+4、等于；独立MCP读取表达式2+4、结果6。修正定稿到最后操作返回9,164ms。Trace `artifacts/voice-audio/20260921-204041-9f52ab`。这是执行前修正通过，不能据此声称已经完成的任务也能改。
2. 中间静音22秒（late.wav）：原任务先输入2+3、等于，26,944ms进入LISTEN；34,785ms收到新的修正片段，重新观察并输入2+4、等于。独立MCP读取表达式2+4、结果6。修正定稿37,188ms，最后操作返回46,287ms，差9,099ms。Trace `artifacts/voice-audio/20260921-204130-8610af`。未关闭重开录音、没有手动resume；第二段确实在旧任务完成并进入聆听后到达。

证据 `measurements.json` / `late-measurements.json` 及各自 `independent-ax.json` 保存时间和独立观察。这两例只证明当前合成语音、计算器场景下的行为；不覆盖旁人声音、物理麦克风、跨应用改口或长任务，也不满足几乎即时的速度目标。此次未改生产代码、未重启服务。

## 2026-09-21 取消语音失败与修复

实时合成PCM第一段请求清空并计算二加三，1秒静音后说“停，不要继续了，取消刚才的计算”，末尾保留12秒静音。第一次 `artifacts/voice-audio/20260921-204344-e4058e` 中派发核验先否决清空，但下一轮choice又选择清空并实际执行，失败。即使没有输入算式，取消后清空已有结果仍是错误修改。

冻结该trace seq90原始请求，baseline三次均CLICK清空；仅明确“取消结束后续工作，不代表清空、撤销或修改应用状态，除非后来又授权新任务”三次均LISTEN。证据 `artifacts/addressing/cancel-scope-v1`。修改只在模型语义定义中，未添加用户文本关键词或正则分类。

正式POLICY补齐上述取消语义后，实时ASR/CU复测 `artifacts/voice-audio/20260921-204528-3146f9`：待执行动作被核验否决，后续LISTEN，整个运行0次修改派发；所有MCP调用仅list_apps/get_app_state。证据 `artifacts/voice-smoke/cancel/scope-verification.json`。实机复测起始显示值与失败轮不同；同状态归因依据来自冻结请求对照，不能把不同实机初始值当作严格A/B。

137个离线测试、Ruff、app.js语法、离线构建通过；已加载到8767，指纹见 `artifacts/voice-smoke/cancel/deployed.json`。取消语义的不同说法、取消后重新授权、跨应用取消仍需扩展验证。

### 测试集审计补充

CU100题集确有100题，但 `relay-v1-exclusive` 权威summary只有20条计算器记录、9条原始通过；status为stopped_model_connectivity，C21中断无最终评分。不能把题集数量当作已运行数量，也不能把它当作当前语音版本成功率。该旧批次不经过ASR且使用旧代码；当前零散语音成功例不能补写进旧批次凑满100题。

## 2026-09-21 取消后重新授权：失败保留与派发核验修复

`artifacts/voice-audio/20260921-204734-265ab1` 使用同一持续录音，取消后再说“现在重新开始，在计算器里清空，再算七加八”。发现两个独立失败：

- 旧PENDING核验在收到“不要继续了”后仍连续返回EXECUTE，15,317ms派发清空。说明上一轮只修POLICY不足以证明所有取消路径正确。
- 新任务被执行为7、8、加、等于，随后反复等于。主动SIGINT停止该隔离测试，保存结果为KeyboardInterrupt，不能算成功。动作记录 `artifacts/voice-smoke/cancel/restart-failure-actions.json`。

将取消的语义定义抽为共享CANCELLATION，POLICY与PENDING均使用，分别要求LISTEN与DISCARD。没有基于用户字符串的拦截。冻结上述seq63、68两个核验请求，原说明两次EXECUTE，新说明两次DISCARD，证据 `artifacts/addressing/cancel-pending-v1`。

取消单独实机复测 `artifacts/voice-audio/20260921-204918-b35fe5`：零次修改派发，pending否决；但末尾结果审查把停止状态显示成BLOCKED，尚不是完整交互通过。证据 `shared-scope-verification.json`。新任务计算错误与结果审查的取消语义仍待修复，不被派发保护的通过掩盖。

137项离线测试、Ruff、app.js语法与离线构建通过；共享取消定义已部署，运行指纹见 `artifacts/voice-smoke/cancel/shared-scope-deployed.json`。

## 2026-09-21 取消状态的结果审查

结果审查原先将“已取消”和“已完成”混入SATISFIED，实际取消trace却得到UNSATISFIED，继而强迫重选为BLOCKED。现将CANCELLED作为独立选项，先从时序语音确定仍被授权的任务，再核对其结果；使用同一CANCELLATION定义。只有UNSATISFIED触发纠正重选，取消不强迫动作。UI显示“当前任务已取消，继续聆听新指令”，不冒充任务成功。

付费只读快照检查 `artifacts/addressing/cancel-outcome-v1`：取消失败快照→CANCELLED；修正成功快照→SATISFIED；取消后新任务计算错误快照→UNSATISFIED，3/3符合预期。取消快照保留了上次outcome_review字段，因此该项不是完全相同请求的单因素对照；随后用正式实时链路验证。

真实PCM→Qwen→Jev→CU复测 `artifacts/voice-audio/20260921-205201-547426`：待执行清空被否决，0次修改派发；最后choice为LISTEN，outcome为CANCELLED，没有BLOCKED。该场景通过，不覆盖所有取消表达和重新授权。

138项离线测试、Ruff、app.js/voice.js语法及离线构建通过，代码与UI已重启加载到8767。当前运行指纹与验证见 `artifacts/voice-smoke/cancel/outcome-deployed.json`。重新授权后的错误数字顺序及等号循环仍未解决。

## 2026-09-21 重新授权错误顺序的因果诊断

复核 `20260921-204734-265ab1` 的seq157/173/189/205：模型依次看到0、7、78、78+，选择7、8、加、等于；目标AX索引与实际工具调用一致。错误来自选择，不是控制错位或看不到最新显示。

固定seq173与循环末尾seq422，8次Jev对照 `artifacts/addressing/restart-context-v1`：
- 原始中文时序上下文仍选8/等于。
- 仅最新语音在seq173改选加，但seq422转向Raise；直接裁剪历史既不能解决恢复，也会损害多步任务，因此不部署。
- 将全部语音忠实译成英文仍选8/等于；没有证据支持靠翻译解决。
- 移除执行历史两处都选Raise；历史不是可以随意丢弃的冗余。

保留全部语音、仅把各选择题goal呈现为按时间排列的文本，两次请求结果为8/删除，未稳定解决。证据 `artifacts/addressing/chronological-goal-v1`。

复用已有remaining_goal实验器，OpenRouter生成剩余任务上下文再交给Jev，共2次文本+6次选择请求，证据 `artifacts/addressing/restart-remaining-goal-v1`。在显示7的阶段，摘要正确描述先加再8，Jev随之正确选加；但在错误状态，摘要要求清空再算，Jev仍选等于。额外文本耗时2037/1016ms，既有收益也有失败，不能上线为可靠恢复方案。baseline在seq422此次选删除、之前选等于，说明选择本身也存在波动。

本轮只增加诊断脚本与原始证据，Ruff通过；未改生产策略或重启服务。下一步必须验证有正确上下文仍不能恢复的操作选择，而不是继续堆叠语音格式转换或丢弃原始信息。

## 2026-09-21 目标问题隔离与编号歧义排查

`benchmarks/addressing/target_isolation.py` 用相同seq173/422状态对比完整请求、operation+click_target、仅click_target，共6次真实Jev请求，无GUI修改。显示7时三种均选8，不能认为拆分请求就能纠正顺序；错误状态完整请求选等于，缩减问题选删除，尚无正确恢复证据。输入token从约19k降到6.7k，但目标仍错，不能把token下降当作任务改进。证据 `artifacts/addressing/target-isolation-v1`。

发现模型输入同时使用原生AX编号和每个目标问题的局部编号，存在表示歧义（例：AX[6]删除、点击目标[6]8）。另做两次只去掉选择题element字段方括号编号的对照，仍选8/等于，证据 `artifacts/addressing/target-labels-v1`。它不是这两例的充分解释，因此没有把删除编号或拆分请求当作修复上线。

目前确认的是模型在正确观测、完整语音及正确映射下的选择失败；后续应优先复测连续输入路径及其调用粒度，避免在每字符动作上反复增加未证实有效的格式调整。本轮生产代码保持不变；实验脚本Ruff通过。

## 2026-09-21 仅使用已有成本与当前任务整理

因果成本对照 `artifacts/addressing/causal-tool-cost-v1`：工具耗时只统计各决策之前已完成的MCP调用，不使用未来日志。重新授权后的0/7状态仍选7/8，不能用历史全量成本的早期正结果宣称实时成本提示有效。

`artifacts/addressing/active-task-v1`：OpenRouter只读完整语音账本，产出“请在计算器里清空，然后计算七加八”。将该任务作为选择题goal，原始语音和执行历史仍保留在state。三个固定界面中，0→7、7→加，恢复错误状态仍→等于。仅证明可能减少旧任务对正常顺序的干扰，没有证明错误恢复、取消泛化或提速。

新增隔离实验入口 `benchmarks/addressing/active_task_audio.py`：缓存严格按整份语音账本内容区分，不匹配语音关键词；Jev仍选择真实目标，执行器和最终派发核验保持不变。尝试真实语音回放时，公共工作台被检查为活跃，实验在创建会话前退出。`active-task-v1/gui-attempt.json`记录未启动；不得计为实测成功或失败。未修改或重启生产版本，Ruff通过。

## 2026-09-21 当前任务整理的语义覆盖检查

公共工作台查询到recording/enabled/validating，未启动任何桌面测试。新增 `benchmarks/addressing/active_task_cases.py`，8个手工语义探针覆盖取消、重新授权、引用取消语句、对第三人说话、跨App局部改口、多行指代、取消一个分支、未说完整。预期意义只用于人工审核，不发给模型；这不是CU100或真实执行通过率。

- `active-task-coverage-v1` 第一题取消返回空字符串，原非空参数解析报错。保留原始失败；候选任务整理不能沿用“必有待填写值”的隐含假设。
- 要求明确描述取消/未完成状态并保存逐题错误后，v2完整运行。引用取消与对小王说话两题误判为取消；其余多步骤修改、指代与部分撤回基本保留要求；未完成语音只返回泛化的写入任务。
- 将现有SPEECH_CONTEXT共享给候选整理器后，v3完整运行：引用文字原样保留，对小王说话不再撤销助手任务；跨App保留复制计算器结果并只改天气城市，多行只改第二行，取消分支只删除天气要求。未完成语音仍只回显“在文本编辑中写入”，没有按实验要求明确标记内容缺失，因此不记为全部协议通过。

三轮原始请求/响应分别保存 `artifacts/addressing/active-task-coverage-v1/v2/v3`。v1调用1次、v2/v3各8次文本模型；没有GUI修改，也没有将摘要层接入生产。仅证明候选文本整理在这些探针的语义表现，不证明下游Jev正确或物理语音可靠。Ruff通过，公共服务未重启。

## 2026-09-21：按用户要求，语音入口统一 Computer Use（待加载）

用户明确取消语音入口的 Browser Use 工具。Workbench 不再创建浏览器目录、调用 CDP 或启动 Browser Harness 重连；目录仅提供运行中的原生 App，浏览器作为 App 由 cua-relay 操作。原版浏览器示例和历史实验保留，不属于语音入口工具集。

工作台保护仍禁止覆盖录音页面；新增唯一受限的原生窗口出口，由模型选择 PRESS_KEY / NEW_DOCUMENT，经新鲜状态校验后派发 super+n。不会提供任意按键绕过保护。下一轮读取新窗口再选择其真实控件。没有修改 cua-relay 仓库。

144 项离线测试、Ruff、前端语法和构建通过；补充验证受限按键实际映射到 press_key(super+n) 而非 CDP。公共服务仍在录音，尚未加载；切换后的真实浏览器语音任务仍待验收。

前一项纯模型重复实验 active-task-repeat-v1：12 次 Jev、1 次缓存共享的文本摘要。冻结状态 seq173 中原请求 3/3 选 8，摘要 3/3 选加；seq422 中摘要仍两次选等于、一次 Raise，未证明恢复能力。此摘要方案仍未部署。

### 纯 CU 浏览器入口：冻结真实界面的模型验证

`benchmarks/addressing/cu_browser_entry.py` 从 `artifacts/voice-audio/20260921-201134-56c122/events.jsonl` 的 MCP 返回 seq51 重新解析 Chrome AX；保留当时 App 目录，移除所有网页专用工具，当前源码生成仅允许开新窗口的工作台出口。输入两种中文、一种英文说法，每种两次。

`artifacts/addressing/cu-browser-entry-v1/events.jsonl`：6 次 Jev 均选择 PRESS_KEY / NEW_DOCUMENT，耗时 1292、382、352、353、351、366 ms。未调用任何界面修改工具。observe 的本机前台只读探针结果在模型请求前清空；其余界面来自冻结 trace。此证据只验证从工作台选择新窗口的决策，不能证明原生快捷键在真实浏览器中的效果或随后网页导航完成。

### 已加载纯 CU；真实回放暴露并修复请求过大

录音结束、服务 idle 且无 session 后重启纯 CU 版本。回放 `open-google.wav` 生成 `artifacts/voice-audio/20260921-212116-495e37/events.jsonl`，成功读取 Chrome 当前网页，未派发界面修改；后续 Jev 返回 HTTP 400。直接复核服务端响应为 `max_tokens_exceeded`（`cu-only-v1-provider-error.json`），不是连接失败。

该页面提供 269 个原生动作，其中 SELECT_TEXT 213 个目标，每个操作均未超过 255，但重复的页面值、目标字典和规则使整体输入超过限制。纯 CU 请求现保留完整 AX 观测及所有目标，目标使用原生 AX 索引、标签、App 和帮助说明引用；共享执行规则只放一次，避免重复大段字段值。没有删去目标或截断页面内容。

同一失败现场用实际新 build_request 构造并调用成功，正文 69,585 字符，Jev 耗时 1,810 ms，选择 Raise；证据 `artifacts/addressing/cu-compact-real-v1/events.jsonl`。此结果仅证明请求可用，不证明网页任务完成。更大页面仍可能超过模型上限，尚无分层目标选择方案。

145 项离线测试、Ruff、JS 语法及构建通过。再次确认 idle/无 session 后加载去重版本，runtime 证据 `artifacts/browser-open/audio/cu-only-deployed.json`。后续整条真实语音执行仍待重测。

### 原生前台激活与导航未通过（cu-only-v2/v3/v4）

- v2 trace `20260921-212602-3507d9`：原生快捷键从录音工作台打开新窗口，但随后模型多次 Raise，停止隔离回放并保留证据，没有宣称完成。
- 检查实时 cua-relay tools/list，确实无 activate_app 工具。桥接层新增 SHOW_APP，目标仅来自观测到的运行 App；用参数数组 `/usr/bin/open -b <observed bundle id>` 激活，记录实际 NSWorkspace 前台 App。没有修改 MCP 仓库，也没有让模型生成命令或 app id。
- v3 trace `20260921-212843-a04b6e`：激活后没有自动观察新目标，模型停止。修复为激活成功后把目标设为下轮观察对象；适配器在观察阶段准备，避免准备失败被记录成激活操作不确定。
- v4 trace `20260921-212946-14ac16`：SHOW_APP 的即时前台观测为 com.google.Chrome。文本模型生成 https://www.google.com，派发 set_value 后观察却出现与任务无关的搜索地址；随后点击网页搜索、WAIT，最后 BLOCKED。未验证 Google 首页成功，存在并发人工/其他任务操作的可能，已向用户询问，暂不继续 GUI 测试。

146 项离线测试通过；新增目标来源验证、派发时序、实际前台与请求目标不一致时不伪报、激活后观察衔接检查。SHOW_APP 改动尚未部署到公共服务。纯 CU 去重版本仍已部署。

确认公共服务 idle 且无 session 后，已加载 SHOW_APP 版本；启动指纹与工作树一致，证据 `artifacts/browser-open/audio/cu-show-app-deployed.json`。未再触碰浏览器。

#### v4 地址异常的边界核对

只读原始 trace，seq94 (20,604 ms) MCP 新鲜状态的地址栏仍为空；seq96 (20,606 ms) 明确发送 `set_value(app=com.google.Chrome, element_index=9, value=https://www.google.com)`；seq97 (23,775 ms) 返回的地址栏已是另一条搜索 URL，焦点位于页面 AXWebArea。不是文本模型生成了这条搜索。

本项目 MCPClient 串行核验 JSON-RPC id；已安装 cua-relay 的 server/session-executor 可见代码将参数传给 session.call，未发现值替换逻辑。这不证明底层运行时正确，也不排除外部并发输入。提取证据 `artifacts/browser-open/audio/cu-v4-argument-boundary.json`。等待用户确认独占条件后再复现，不在当前用户浏览器上盲目重放写入。

### 独占桌面复测：自动补全与桥接观测缺失

用户明确授权独占桌面后，`20260921-213821-77b0f4` 仍复现错误导航，不能继续将问题归因于并发输入。直接 MCP 探针 `artifacts/addressing/cu-exclusive-write-v1/events.jsonl` 与 `cu-insert-exclusive-v1/events.jsonl` 显示：Chrome 会将输入的域名补成历史搜索 URL，并把补出的后缀放在 Selected text 中。

原生 CUA 独立对照：空白窗口 setValue 输入 Google 域名后仍处于新标签页，地址栏中历史搜索后缀被选中；BackSpace 删除选中后缀、Return 后实际呈现 Google 首页。这只是手工工具对照成功，不是语音或 Jev 验收。relay set_value 返回已导航页面与原生对照的差异尚未定位，不能据此宣称参数被篡改或底层已修复。

桥接层现在保留 focused_index / selected_text，传给选择和文本模型，并将选择变化纳入状态新鲜度与执行效果比较。同时补齐协议中的英文 link 和中文组合框角色，使实际暴露的链接、可填写组合框进入工具选项。这里仅解析 AX 协议，不匹配用户意图；CU MCP 未修改。

带选择状态的回放 `20260921-214325-4213b6`、`20260921-214541-ed7fe6`、空白窗口回放 `20260921-214635-ab4d25` 均未通过导航验收；其中第二轮文本助手无有效字段值而停止。试验性的“空字段优先 INSERT_TEXT”提示未改善结果，已撤回，不积累未验证的提示规则。

148 项离线测试、Ruff、两个前端脚本语法检查、离线构建通过。这些检查证明解析及回归约束，不证明真实语音任务成功。

补齐角色后的真实音频回放 `20260921-215121-f814ee`：SHOW_APP、点击地址栏、填写正确域名、ENTER、两次 WAIT 后 BLOCKED。独立原生 AX 读取确认仍为历史搜索结果页，非 Google 首页，验收失败。结果文件 `artifacts/browser-open/audio/result-cu-exclusive-roles-v1.json`。没有在失败后自动重放写入。

公共服务 idle/无活跃会话时已重启加载选择状态和角色解析修复，指纹核验见 `artifacts/browser-open/audio/cu-selection-roles-deployed.json`。运行地址仍为 127.0.0.1:8767。未修改 CU MCP、未提交或推送。

### 原生字段工具契约对照（续）

`artifacts/addressing/cu-field-contract-v1/events.jsonl`：同一 Chrome、各自新建空白窗口、相同输入 `https://example.org`，仅切换工具。`set_value` 返回后，独立 get_app_state 已是 Example Domain 页面，地址包含自动补全的查询参数；`type_text` 返回后仍是新标签页，补全的 `/?CGPOSTTOPID=1` 处于选中状态。全程无 Jev / 文本模型调用。这进一步确认本机这条 MCP 路径下两个工具存在提交行为差异；不泛化为所有 App、所有版本的契约。

修正工具描述：INSERT_TEXT 会替换当前选区，否则在光标处插入，只有未选中的内容被保留。此前“Existing content is preserved”会误导选择和文本模型。修改共享操作描述、文字参数说明及策略中的对应表述，不添加浏览器网址规则，不修改 CU MCP。

只读冻结状态实验 `artifacts/addressing/cu-selection-choice-v1/events.jsonl`：在上述 type_text 后的真实选区状态，任务为打开 example.org 首页，历史记录保留实际输入。Jev 仍选择 ENTER（1394 ms），没有清除补全后缀。未派发该模型动作；不能据此认为选择语义修复解决了 Google 搜索后缀问题。诊断脚本写入 choice 后因漏传遥测 version 抛出 KeyError；原始模型请求、响应与已落盘 choice 可读，未重试付费请求。

148 项测试、Ruff、app.js 语法及离线构建通过。主要未解决项仍是字段提交差异与 Jev 对实际执行偏差的恢复，不是 ASR 未听清。

### CU 策略分离与字段执行反馈

纯 CU 原来继承 NEXT_ACTION 的网页表单假设（输入后必须选自动补全、可见 Search 则提交）。现仅纯 CU 使用通用 NATIVE_NEXT_ACTION；原版 Browser Use 策略保留。固定现场对照 `artifacts/addressing/cu-neutral-policy-v1`：seq130 原提示 LISTEN、新提示 ENTER；seq198 两者均 BLOCKED。共4次 Jev，未执行 UI，不证明去除表单假设单独改善纠错。

LiveSession 增加字段读回反馈：同一 surface 上角色和标签唯一的可填写目标，记录 requested/observed/selected_text 与 equal/different/unavailable。不会凭旧 AX 索引猜字段，重复标签、字段消失或换 surface 则不可比较；不同值只表示字面差异，不判为工具失败或任务失败，不自动重放动作。反馈写入真实观察后的 history 与 action.observed trace，让后续 Jev 和文本助手读取。

原失败 trace 回放验证 field-readback.json：要求 Google 域名，读回历史搜索 URL，正确记录 different。新增离线测试覆盖索引重排、差异/相同值、歧义、其他 surface 和非填写动作。149 项测试、Ruff、两个前端语法检查、离线构建通过。

`artifacts/addressing/cu-field-feedback-v1` 在同一 seq130 冻结请求中增加上述实际读回差异后，Jev 选择 TYPE_TEXT，而非中立提示对照的 ENTER。仅1次模型请求，未在该实验派发界面动作；这是纠正意向变化，不是导航成功。真实音频回放另记。

真实音频 `20260921-220028-9abf5d` 未通过：2次已返回动作后，在写入文字前停止。文本服务 inception/mercury-2.5 返回数组 `["this google address"]`，finish_reason=length，违反已发送的严格 literal_text JSON Schema；max_tokens=1024、reasoning disabled。bridge 拒绝非法参数，没有派发该次文本写入。此轮不是字段读回机制完成导航的证据，也不能归为 CU 写入失败。结果 `artifacts/browser-open/audio/result-cu-field-feedback-v1.json`。

### 文本截断恢复与模型对照

bridge 文本助手现在拒绝 finish_reason/native_finish_reason=length 的响应，即使其中碰巧含合法 JSON。仅此类截断允许一次相同源输入的重新生成，预算1024→2048；第二次截断仍停止，不执行工具、不拼接残缺文本。普通非法格式和显式 null 不重试。返回 attempts/attempt_usage，原始每次模型调用继续独立记 trace。151 项离线测试覆盖两次截断上限及合法但不完整 JSON 不可执行；Ruff、两个前端语法、构建通过。

重放原始失败的 field_text 请求 `artifacts/addressing/text-truncation-recovery-v1`：首次就返回合法 JSON，未触发恢复，但给出了页面既有 example.org 而非请求的 Google。因此不宣称实测证明重试恢复成功，也不把 JSON 合法等同语义正确。

新增显式付费、无 UI 的 `benchmarks/addressing/text_model_comparison.py`。5个探针：原始失败请求、原样输入、局部改口、引用停止语句、内容缺失。OpenRouter /models 返回三者都声明 structured_outputs 支持。
- v1：Mercury 改口多写“写”字；Flash Lite 在缺失内容时回显操作指令。各5次。
- v2：Flash 返回 google.com（地址栏语义可接受，但原始严格字符串评分为 false，不能算导航语义错误）；缺失内容时返回空字符串，违反参数契约。5次。
- v3：试验性明确“区分写入内容与编辑指令”提示，Mercury 改口丢句号，Flash Lite 仍将未完成指令当正文、Google目标变为搜索词 google。各5次。该提示未体现稳定改进，已撤回；默认模型未切换。

v1/v2/v3 原始结果分别保留在 artifacts/addressing/text-provider-comparison-v1/v2/v3，不汇报这5题为通用准确率。未修改 MCP。当前新增生产行为仅为有界截断恢复与相关遥测。

### 键盘替换实验尚未执行：锁屏中断

新增手工 CU 探针 `benchmarks/addressing/cu_keyboard_fill.py`：拟在新测试窗口用公开 MCP 的全选、type_text、逐步读取焦点及值验证替换；仅当实际值严格等于“要求文本+当前选中后缀”时才删除该额外后缀，验证字面值后才由实验明确提交。它不解析用户语音，也不改变生产执行器或 MCP。

`artifacts/addressing/cu-keyboard-fill-v1` 在首个 get_app_state 返回 The Mac is locked，未创建窗口、未输入任何内容。不能把该中断算作键盘方案失败或成功。已请用户解锁；生产 TYPE_TEXT 暂仍沿用现有 MCP 映射，没有上线未经实测的多步填写。

bridge 现在将无 app_state 的明确锁屏响应报告为可理解的中文环境错误，而非通用解析错误；含同样文字的真实文档不会被识别为锁屏。新增测试验证首次只读即停止且不派发操作。

锁屏第二轮复核：新的独立 get_app_state 再次明确返回 Mac is locked；该只读进程已退出，无后台桌面实验运行。等待解锁期间，键盘探针补上每次读取、按键和文字输入前的公共工作台活跃检查，用户开始录音或状态请求失败时停止后续操作；删除选中后缀后再次核验字段角色和标签。Ruff 通过。未运行填写、未修改或重启 MCP，也未将键盘探针接入生产。

### 解锁后：公开 MCP 键盘路径首次通过 Google 首页

新的只读 get_app_state 返回正常界面，锁屏阻断已解除。

- `artifacts/addressing/cu-keyboard-fill-v2`：全选和 type_text 成功返回，自动补全弹窗出现；AX 未提供 focused_index，实验在提交前停止。这不是输入未生效。原始返回含地址栏完整值与选中历史搜索后缀。
- v3 在探针中增加明确记录的诊断性焦点推断：只有 focused_index 缺失、当前 selected_text 只存在于唯一可填写字段，且该字段角色/标签与原目标一致时才继续。不是宣称 AX 明确报告了焦点，也未接入生产。其他情形仍停止。
- `artifacts/addressing/cu-keyboard-fill-v3`：输入域名，观察自动补全后缀被选中；仅在实际值严格等于“要求的字面文本+选中后缀”时派发一次 BackSpace；再次读取，确认值精确等于 https://www.google.com，之后才执行实验明确要求的 Return。最终独立 get_app_state 标题 Google、地址栏 google.com，确认首页，非历史搜索页。

v3 的 literal_verified 位于14351 ms，最终观测18322 ms；含新建窗口、状态检查和所有工具调用。未调用 Jev、文本模型、ASR，不计为语音端到端成功，也不是近即时性能通过。全程使用 cua-relay 公开工具，未改 MCP。探针每步检查公共工作台未启动用户会话。

下一步是将已验证的字面填写语义接入 bridge，并验证取消、字段变化、TextEdit 多行及真实语音导航；当前生产 TYPE_TEXT 映射尚未替换，避免将单个 Chrome 探针泛化为所有字段已通过。

### 键盘填写接入 bridge（尚未部署公共服务）

新增 keyboard_fill.py，将字面替换编排限定为公开 MCP click / press_key(super+a) / type_text，逐步重新观察；仅在字面值严格等于要求文本加当前选中后缀时删除该额外后缀。成功时严格读回完整值；不发送 Return，不重放失败原语。要求同一进程/窗口标题、唯一字段角色及标签；焦点缺失时只允许此前探针验证的唯一选中文字归属推断并写 trace。失败进入不确定操作暂停路径，不假装未修改界面。

AutomaticDesktop 原生 adapter 启用该 bridge 填写目标，操作名仍 TYPE_TEXT、文字仍交文本模型生成；记录的底层调用是真实公共 MCP 原语，绝不向 MCP 发送 keyboard_fill 这种 bridge 内部名称。旧的固定窗口适配器默认行为保留。一次填充值作为一个已派发操作完成，麦克风停止不会撤销其已派发原语。取消边界在开始前核验，后续多步完成延迟仍需改善。MCP 源码和安装包未修改。

第一次真实音频 v1 (`20260921-222444-63ca81`)：成功完成 SHOW_APP 和字面填写，history.field_readback=equal，模型却 LISTEN/SATISFIED，没有 Return。不能把地址栏值正确当导航成功。准备阶段新标签页的地址确实为空，但焦点索引缺失使准备脚本断言失败；本轮是空白地址、非已验证初始焦点的回放。

由 v1 发现 parser 丢弃了窗口/WebArea 的 URL 属性；现在把原始 URL 保留在观测文本及元素元数据，使模型可以区分已加载页面与地址栏待提交值。协议属性保留，不匹配网站或用户意图。新增回归测试；161 项测试、Ruff、前端语法与构建通过。

### 纯 CU 中文音频导航通过一轮

v2 `artifacts/voice-audio/20260921-222645-40aecb/events.jsonl` 从已验证空白地址栏的新标签页开始。PCM 音频经本地 Qwen ASR 持续转写，Jev 自动选择 SHOW_APP、TYPE_TEXT、PRESS_KEY(ENTER)，OpenRouter 生成 https://www.google.com。bridge 在 TYPE_TEXT 内部使用公开键盘原语并清理严格匹配的额外选中后缀，读回 equal，随后由 Jev 单独选择 ENTER。

独立的新 MCP 连接验证保存在 `artifacts/addressing/cu-bridge-fill-verify-v2/result.json`：标题 Google，地址栏 google.com，页面搜索框为空，确认 Google 首页而非历史搜索结果。不能仅以 LISTEN/SATISFIED 为验收依据。

本轮实际调用 Jev 14 次、OpenRouter 1 次（含流式更新、派发核验和结束审查）。相对回放进程启动，前台激活返回12103 ms，填值返回25747 ms，回车返回37421 ms；包含 ASR 启动与工具读取开销。仅证明一个真实音频场景通过，不代表近即时性能、物理麦克风采集、其他场景或稳定成功率。结果文件 `artifacts/browser-open/audio/result-cu-bridge-keyboard-v2.json`。

### 多行中文对照及字段策略收敛

TextEdit 首次只读超时，第二次只读确认处于“打开”面板；从实际可见的“新建文稿”创建独立测试文稿。`artifacts/addressing/cu-keyboard-textedit-v1` 中公开 type_text 后实际正文仅为换行加 MCP，且字段显示标签发生变化；bridge 因目标不能稳定匹配而停止，未重放写入。不能将 Chrome ASCII 地址验证泛化为中文多行支持。

随后只在同一明确测试文稿上，`artifacts/addressing/cu-native-textedit-v1` 用原生 set_value 写入完整中文两行，新的独立读取逐字比较通过。基于该对照，生产 bridge 对 AXTextArea 保留原生 setter，仅单行 AXTextField / AXComboBox 使用键盘替换。它按观测控件角色选择执行机制，不解析用户话语，不修改 MCP。单行中文以及更多 App 的键盘行为尚需单独验证；任何实际值不符都停止，不自动重放。

公共服务确认 idle/无活跃会话后已加载 bridge 字段策略和 URL 观测修复，运行指纹核验见 artifacts/browser-open/audio/cu-keyboard-bridge-deployed.json。162 项测试、Ruff、两份前端语法与构建通过。未提交或推送。当前一轮 Google PCM 音频通过和 TextEdit 原语验证，不代表完整100题、持续改口/取消、物理麦克风或性能目标已经验收。

### 合并完成性检查：再一轮 Google PCM 导航通过

原成功回放中，停止判断经常先 LISTEN，再独立请求完成性分类，再重新请求纠正动作。现在在已结束语音段、已有执行历史时，将 outcome 作为同一 Jev 请求的推测题，与 operation / target 一起预测。仅 operation=LISTEN 才消费 outcome；其他操作忽略该题，包括无效的推测答案。UNSATISFIED 最多再选择一次新动作或 BLOCKED，不重放旧操作；中间 ASR 假设仍不做结束审查。改动仅在 bridge，不修改 MCP。

163 项离线测试、Ruff、两份 JS 语法、构建通过：覆盖单请求停止、两请求纠正、未知/取消不强制动作、中间语音不审核，以及实际动作不消费推测审核答案。

真实音频 `20260921-223704-84815e` 从已验证空白地址栏开始，完成 SHOW_APP、填写、ENTER。独立新 MCP 连接验证 `artifacts/addressing/cu-fused-review-verify-v1/result.json`：标题 Google、地址栏 google.com、搜索框为空。PCM 仍经过真实本地 Qwen ASR，不含物理麦克风采集。

本次 Jev 7 次、OpenRouter 1 次；ASR 就绪7993 ms，首条转写9214 ms，首次派发10998 ms，最终回车返回27950 ms。即首条转写到首次派发1784 ms、首条转写到最后操作返回18736 ms。对照前一成功回放的14次 Jev、37421 ms最终返回，不能归因全部差值给合并请求：前轮还发生一次真实焦点/自动补全弹窗变化导致重新观察，且 CU/ASR 延迟不同。本轮同样不是稳定成功率或平均延迟测量。计时文件 artifacts/browser-open/audio/fused-review-timing-v1.json。

### 中文跨应用 PCM 回放：bridge 兼容修复后通过

v1 `artifacts/voice-audio/20260921-224210-55b03b/events.jsonl` 正确转写并写入 TextEdit，但网页单行中文经公开 type_text 后读回为空，bridge 因字面值不符停止，未搜索。这轮失败保留，不因后续修复改记成功。原生 set_value 对照 `artifacts/addressing/cu-web-native-unicode-v1` 完整写入同一字段，独立读回一致，未触发搜索。

仅修改本仓库 bridge：单行 ASCII 仍用键盘替换；单行非 ASCII 用原生 setter；多行 AXTextArea 保留原生 setter。内部动作名称改为 field_fill，trace 记录 fill.strategy。没有修改、重启外部 MCP，也没有用户意图关键词判断。该修复针对 TYPE_TEXT；INSERT_TEXT 的任意 Unicode 插入仍未验证，不作支持承诺。

重新建立可验证空白实验页和测试文档基线后，v2 `artifacts/voice-audio/20260921-224936-4780b1/events.jsonl` 经真实本地 ASR、Jev 和 OpenRouter 执行完成。实时语音尚未说完时先写入“语音联动”，随后根据更新转写替换成“语音联动成功”，再切换 Chrome、填关键词、点击搜索。共6个已返回操作（含两次正文写入）、11次 Jev、3次文本模型请求；最后点击返回位于进程启动后36965 ms。不能据此宣称低延迟或稳定成功率。

新 MCP 连接独立验证 `artifacts/addressing/cu-crossapp-verify-v2/result.json`：TextEdit 正文、Chrome 关键词均精确为“语音联动成功”；实验页 oracle 只有一次正确搜索，备注为空、归档未选。完整 PCM 经过流式 ASR，但不覆盖物理麦克风采集。结果 `artifacts/voice-smoke/cu-crossapp-current-v2.json`。

164项离线测试、Ruff、两个前端 JS 语法检查及构建通过。公共工作台 idle 后仅重启本仓库 jev-voice；新进程源码指纹核验 `artifacts/voice-smoke/cu-crossapp-v2-deployed.json`。未提交或推送。100题完整真实桌面验收、任意 Unicode 插入、持续改口取消和近即时性能仍未完成。

### 中文追加与局部修改：能力边界、首次停止与未解决的响应校验

本轮为真实 TextEdit 的文字指令回放，不含 ASR/麦克风。所有用例使用本任务创建的“未命名”测试文档，测试输入和代码保存在 artifacts/addressing/cu-unicode-edit-v1 至 v6。

发现 INSERT_TEXT 仍走公开 type_text 的非 ASCII 丢字路径。现将 ASCII 限制记录在观测动作 help，并传入文本助手；所有同操作目标共享的限制也进入操作选项说明。非 ASCII 键盘输入在派发前拒绝，不作转写或丢弃字符的“修复”。可填写字段仍支持原生完整值替换，由文本模型保留未请求修改的正文。外部 MCP 未改。

- v1：选择模型仍选中文 INSERT_TEXT，派发前被拒绝，记失败。
- v2/v3：初始只有应用列表，模型直接 LISTEN，正文未变，均失败。
- v4：首次定稿决策也加入同请求的 outcome 检查，识别未完成却选择 BLOCKED，仍失败。该检查不增加正常动作的往返，仍只消费 LISTEN 对应 outcome。
- 将 SWITCH_APP 的描述明确为“读取应用控件，也可读取已经前台的应用”，纠正此前“another app”含义。没有按用户话语匹配应用，也不强制固定工具顺序。
- v5 追加通过：原文“语音联动成功”变为“语音联动成功，继续中文口述。”；Jev 选择 TYPE_TEXT，OpenRouter 生成保留原文的完整值，独立 MCP 读回一致。指令到稳定停止约13342 ms，非近即时指标通过。
- v5 局部改字失败：Jev 返回 choice=SELECT_TEXT，而概率 SELECT_TEXT=.45、TYPE_TEXT=.46，校验拒绝，不派发这次修改。v6 单独复测仍遇 Invalid TypeSafe response，不计成功。尚未确认其是否与概率精度/服务端选项采样契约有关，未擅自放宽校验。

最终独立读取 artifacts/addressing/cu-unicode-edit-final-verify/result.json 确认正文仍为追加成功后的完整内容，局部改字未完成。每轮原始请求/响应和 trace 保留。离线168项测试、Ruff、两份 JS 语法和构建通过；公共工作台 idle 后加载新版，源码指纹见同目录 deployed.json。未提交、推送或修改 MCP。任意光标 Unicode 插入、模型决策稳定性和完整100题仍未完成。

### Choice 契约核对、有限模型重试、中文音频改口通过

2026-09-21核对官方 https://docs.typesafe.ai/primitives/choice ：choice 定义为概率最高的选项，probabilities 总和为1。v5/v6 的两个不一致响应已提取至 artifacts/addressing/jev-contract-audit/contradictions.json。因此保留严格校验，不将选择悄悄改为概率最大项，也不容忍排名不一致。

bridge 新增 request_decision：只校验所选操作、其目标/参数，以及 LISTEN 消费的 outcome；无用推测题继续忽略。遇 InvalidChoice 在派发前最多重发一次相同请求，仍失败就停止。choice.invalid_response 标记尝试和是否重发，原始 HTTP 请求/响应独立保留。此重试不执行或重放 UI；原有派发前语音和界面有效性检查不变。完成性纠正的新决策各自最多两次请求，最坏四次；HTTP 传输重试另计。pending_valid 暂仍单次解析，没有把所有模型错误统一重试。

离线170项测试通过，包含首次不合法后恢复、两次不合法停止、同请求、以及未使用推测答案不影响操作。Ruff、两份 JS 语法、构建通过。

v7真实 TextEdit 文字指令局部改字通过：独立结果 artifacts/addressing/cu-unicode-edit-v7/results.json，实际正文“语音联动成功，继续实时口述。”，其他内容保持。约13126 ms稳定停止。本轮所有响应直接合法，没有证明线上重试恢复发生。

再用 macOS Tingting 生成音频“把文本编辑测试文档正文中的实时改成快速，不对，改成稳定，其他内容保持不变。”，通过真实本地 Qwen ASR 流式回放，trace artifacts/voice-audio/20260921-230053-629aa6/events.jsonl。最终独立 MCP 读回 artifacts/voice-smoke/unicode-correction/verify/result.json：正文精确为“语音联动成功，继续稳定口述。”。本轮只选择“实时”、然后写入“稳定”后的完整正文，没有写入中间的“快速”。

本轮9次Jev、2次文本模型。ASR就绪7680 ms，首条转写8895 ms，选择文字返回22757 ms，最终填写返回27971 ms；首条转写到最后写入约19076 ms，仍未达到近即时目标。合成PCM覆盖ASR流式改口，未覆盖物理麦克风、旁人噪声或完整100题。工作台idle后加载新版，指纹 artifacts/voice-smoke/unicode-correction/deployed.json。只改本仓库，外部MCP未改或重启，未提交/推送。

### 延迟拆分与未奏效的操作描述实验

上一轮“首条转写到最后写入19076 ms”包含用户仍在说话的8729 ms；语音定稿到最后写入为10347 ms。不可将完整19076 ms当成用户说完后的等待。原始 trace 可见 SELECT_TEXT 产生一次额外文本模型调用、状态读取和 select_text 工具调用，随后 TYPE_TEXT 才整框替换。

实验将 TYPE_TEXT 描述明确为可生成保留其余内容的完整值，从而做局部替换或追加，无需先选字。用新音频“稳定改成灵活，不对，改成可靠”验证，trace artifacts/voice-audio/20260921-230327-4a0556/events.jsonl，最终独立 MCP 读取 artifacts/voice-smoke/direct-edit-correction/verify/result.json 为“语音联动成功，继续可靠口述。”，精确符合改口结果。

但 Jev 仍选择 SHOW_APP→SELECT_TEXT→TYPE_TEXT，没有消除多余的选字。首条转写到最后写入15712 ms、定稿后6937 ms；由于激活/读取时机、模型和MCP延迟均不同，这个单样本差异不能归因于提示词改动，也不代表稳定性能提升。对照指标保存在 timing-comparison.json。

此描述实验已撤回，避免为无已证收益增加提示内容。公共运行指纹与撤回后源码一致，未重启服务或MCP。实验时170项离线测试、Ruff、两份JS语法及构建通过。累计增加一个经真实本地ASR的改口成功样本，但近即时性能、物理麦克风及100题验收仍未完成。下一步优先结构化减少冗余决策/工具调用并扩展冻结用例执行器，而非持续叠加提示词。

### 冻结浏览器25题首次纯CU回归完成

详见 docs/cu100-native-browser-results.md：跨六个接续批次跑完全部B组，20题最终评分通过、5题失败（B10/B11/B14/B15/B24），未重跑覆盖失败。失败集中于缺省搜索目标/地址栏选择；B13虽最终通过，但改口到达后旧值仍被派发，不能宣传及时取消已验证。长背景、完成填写后改口、撤回提交和后续授权提交均有独立状态/事件证据。

测试器去掉Browser Harness/CDP，独立读回同样使用新的MCP连接。新增时序注入、派发时点记录、输入完整性及禁止提前修改的检查。本轮没有生产策略修改、没有MCP修改或重启。最终170项离线测试、Ruff、app.js语法与构建通过。剩余计算器/文本编辑/跨App共75题、真实麦克风和近即时体验仍未完成。

### 改口后的文字单独审核：B13过时写入被阻止

以首次B13的原始pending请求做付费无UI对照，artifacts/addressing/pending-gate-study-v1。6种输入情形×原始/新方案×2次，共24次Jev请求。原始方案在“海浪改山峰”两次都错误放行；同请求添加pending_text后两次均返回REWRITE。无关补充、重复确认、引用他人指令、取消、换目标的对照均符合设定。小型定向诊断，不当作通用准确率。

桥接层在生成过文字且语音版本变化的派发检查中，同时预测动作是否仍有效和文字是否仍适用。主动作DISCARD时不消费推测的文字题；主动作EXECUTE但文字REWRITE时取消待执行操作，让正常循环根据新输入生成文字。不解析关键词、不强制等到语音结束、不重放已执行操作，也不增加单次检查的HTTP往返。pending_valid尚不复用choice重试，非法响应仍停止。

真实冻结B13重跑 artifacts/cu100-native-browser-pending-v1：第二段输入5602 ms到达，旧文字海浪7195 ms生成，9468 ms被REWRITE拒绝，9469 ms取消派发。之后山峰14911 ms派发并16738 ms返回。独立AX和网页事件oracle均验证成功；B13-temporal-check.json确认页面仅收到一次输入山峰，没有短暂写入海浪。此次后验修复验证不覆盖或改写首次25题记录。

调试面板补上文字审核结果，显示“文字已过时，需要重新生成”或“文字仍符合当前要求”，避免只看主动作EXECUTE概率误解最终决定。174项离线测试、Ruff、两份JS语法及构建通过。

修复后的B13、B12、B18、B19四个真实CU时序用例均通过；原始25题成绩不改写。

### 速度优先：取消过时操作后复用刚读取的界面

用户将速度、持续语音的即时执行与反馈明确为顶层要求，后续优先推进延迟关键路径，测试数量本身不作为优化目标。

派发前fresh()刚完成状态读取，随后语音检查可能取消操作；此前取消后的重新决策又读取了一次相同App。现在暂存这次fresh结果供取消后的observe消费，在真正_dispatch前清除。下一次act仍必须重新检查界面，没有把观察缓存当成执行许可。只改bridge，不动MCP。

真实B13对照 artifacts/cu100-native-browser-pending-fast-v1：取消到下一模型请求2062→44 ms，期间get_app_state次数1→0；最后输入到最终写入11136→9092 ms。新版仍只产生一次网页输入“山峰”，没有旧值写入。单轮前后对照，不是平均延迟或完整语音性能承诺；原始计时 timing-comparison.json。175项离线测试、Ruff、两份JS语法及构建通过，其中新测试确认取消后无需重复读、下一次派发仍重新读、执行前清除旧观察。

公共工作台idle后加载新版，指纹同目录deployed.json。未修改或重启MCP，未提交推送。完整目标尚未完成；当前这条改口用例仍需约9秒完成，下一步继续减少串行调用及文本生成等待，并保留正确取消与可观测反馈。

### 速度优先：文字失效时复用已审核的目标

只读检查公开MCP的状态租约实现后，未将租约TTL等同界面新鲜度证明，也未提前并行读取来跳过现有派发检查。外部仓库未修改。

实际新增优化在LiveSession：pending主判断已经EXECUTE、仅pending_text=REWRITE时，取消旧文字后保留该目标和审核过的输入版本。重新观察内容一致、目标仍在当前候选内、输入版本仍相同时，直接重新生成文字，不再重复请求Jev选择操作。任何页面/目标/输入变化都回退正常选择；新的真实操作仍经过既有fresh与派发前校验。trace新增text.rewrite_target_reused。

v1真实B13通过后补上完整观察内容一致性判断，v2最终版再验证通过。与上轮仅复用CU读取的版本对照：取消到新文本请求468→62 ms，中间Jev请求1→0；最新输入到最终返回9092→8466 ms。网页事件oracle仅记录输入“山峰”，独立AX一致。结果 artifacts/cu100-native-browser-direct-rewrite-v2，timing-comparison.json包含指标；仅单轮对照，不能作为稳定平均延迟。

179项离线测试、Ruff和构建通过，JS语法检查通过；新增测试覆盖目标复用、更新输入/目标/页面内容时必须重新选择。工作台idle后已加载最终版，源码指纹deployed.json。未修改或重启MCP，未提交推送。改口后的整体等待仍约8.5秒，速度目标尚未完成。

### 即时反馈：移除网页600ms固定轮询等待

检查发现ASR subprocess在同一工作台连接内已持久复用；转写由事件直接送到LiveSession。网页却每600ms才请求一次状态，反馈延迟与执行延迟是不同问题。现在/api/state支持after游标长轮询，Trace事件通知等待者立即返回；无Trace时500ms空闲等待，有Trace时最多1秒心跳，网络失败保留600ms退避。旧的不带after状态接口行为保持，鉴权不变。

游标在状态快照前捕获，保证快照期间到来的事件会被下一次请求看到；已到达事件不等待。新增两个并发回归覆盖等候期间新事件、订阅之前已到达事件、快照期间更新不丢失。181项离线测试、Ruff、两份JS语法和构建通过。

隔离的真实ThreadingHTTPServer/httpx测试 artifacts/feedback-latency-v1：20次事件发出到HTTP状态响应，中位0.863ms、最大5.461ms；错误token返回403。该测试使用模拟Trace事件，不启动麦克风、不调用付费模型或MCP，不代表浏览器绘制或真实CU执行速度。脚本、原始samples及result.json保留。

公共工作台idle后加载新版，核验state_cursor、下发JS和空闲等待行为，见deployed.json。MCP未改或重启，未提交推送。首次ASR冷启动、录音准备循环、实际浏览器绘制和长程执行延迟仍未整体验收；当前改动只消除反馈链路固定600ms等待，不宣称电脑动作变成毫秒级。

### 速度优先：服务启动预热Qwen，录音不再等冷加载

检查确认桌面连接本已在LiveSession线程异步准备，不能宣称优化了不存在的串行瓶颈。本轮改为服务启动时仅启动ASR子进程、加载模型，不创建LiveSession、不分配recording_id、不发送音频。用户点击录音后，将预热进程接入真实会话和新trace并开始录音；ASR stderr路径在voice.model_attached中保留。无模型环境时跳过预热，预热启动失败记录trace而不阻止文字工作台；服务关闭时释放未绑定的预热进程。VOICE_PRELOAD=0可关闭启动预热以节省常驻内存。MCP未改。

网页录音准备也改用状态长轮询，移除300ms固定就绪检查间隔。预热时界面明确标注未开启录音。

真实本地Qwen worker对照 artifacts/asr-warmup-v1/result.json：冷启动8669.66ms；预加载6971.37ms发生在录音请求前；同一已加载进程开始会话13.09ms。验证预热期间无录音ID、音频字节为0、无桌面会话；启动后PID一致。两个实验进程均关闭，脚本没有访问麦克风或付费模型。

公共服务最终版衔接验证deployed.json：启动后自动预热，/api/state显示无会话、0秒音频、已加载；/api/voice/start至ASR ready为9.52ms，未重新加载权重。测试没有发送PCM或打开物理麦克风，随后暂停并取消测试录音；最终状态idle、model_loaded=true、执行历史为空。后台模型保留给用户下一次录音。

183项离线测试、Ruff、两份JS语法与构建通过，新增测试验证重复预加载仅启动一次、未显式start不发开始录音消息、预热错误无需桌面会话也能报告。当前数字仅证明录音准备速度，不覆盖浏览器麦克风权限、音频采集、ASR首次转写、模型执行或实际动作延迟。未提交推送，完整速度目标仍未完成。

## 2026-09-21：预热后流式 ASR 首条反馈基线

新增可复跑的 `scripts/check_asr_stream_latency.py`，以 200 ms PCM 块按真实时间回放，
每块在对应采集时长经过后才送入；同一预热 Qwen worker 连续处理四个录音。
脚本不调用远程模型、不启动麦克风、不操作桌面，退出时关闭自己创建的 worker。
原始转写版本及模型识别耗时见 `artifacts/asr-stream-latency-v1/trace/events.jsonl`，
输入 PCM 哈希、逐条相对时间见同目录 `result.json`。

| 音频 | 时长 ms | 首条非空转写 ms |
| --- | ---: | ---: |
| command | 3699 | 1352 |
| crossapp | 10619 | 1200 |
| continuous-first | 6330 | 1205 |
| continuous-second | 4389 | 1157 |

这是四段音频各一次的本地识别基线，不能代表真实麦克风或端到端动作延迟。
长句会回改；两段 continuous 的末尾分别识别成“连续语音夹”和“连续语音语”，
因此出字延迟不能替代正确率验收。没有为了结果好看而修改识别文本。

同一音频经实际分段器重放的 `schedule.json` 证明四段均在 400 ms 产生首次快照，
但 worker 的 800 ms 最小长度门槛将其丢弃；第二次快照直到 1000 ms 才到达。
这说明首条反馈包含 1000 ms 的采集/调度等待，而非全部是模型计算。
下一步应对齐分段器与 worker 的最小长度，比较首条时间与回改质量；
本轮只建立基线，没有降低生产识别门槛，也没有修改或重启外部 CU MCP。

验证：183 项 pytest、Ruff、app.js/voice.js 语法检查与离线构建全部通过。

### 对齐 800 ms 首次快照：候选修改，尚未更新公共服务

`StreamingSTTProvider` 现在允许调用方传入首次快照长度，默认行为保持原样。
本项目 worker 指定 0.8 秒，删除调度后的重复长度过滤，避免先生成必然被丢弃的
0.4 秒快照。仍由声学 VAD 判断语音，不增加文字规则或意图匹配。

同四段音频重放记录在 `artifacts/asr-stream-latency-v2/`。依次首条为
1241、990、1006、989 ms，相对基线提前 111、210、199、168 ms。
最终文本四段均与基线相同（包括基线已有的错字）；早期文本更不完整，
例如“先不要搜”变成“先不要算”，随后回改为“先不要搜索”。
因此不能声称中间语义正确率不变。

最终转写时间相对基线依次为 +18、-83、+228、+293 ms。
此次脚本在 WAV 结束后立即 finish，后两段的最终识别需要等待当时正在运行的
partial；还需带尾部静音的自然 endpoint 回放和真实模型执行验收。
暂不重启公共 8767 服务部署此候选，避免把首条出字收益当作整体交互收益。

本地调度验证覆盖 20/100/200 ms 块：首次均在 800 ms；300 ms 短语音在
finish 时仍完整交付，不被最小 partial 长度吞掉。结果见 `schedule-check.json`。
183 项测试、Ruff、两份 JS 语法检查、离线构建通过。外部 MCP 未修改。

### 自然停顿对照及完整链路：已更新公共服务

在相同四段 WAV 后追加 1800 ms 的实时静音，旧调度与 800 ms 对齐调度分别
使用独立源码副本顺序运行；通过 `asr.loaded.source_sha256` 与父进程 manifest
逐项核对，确保实际 worker 是对应版本。首轮隔离有误的 baseline/replay 标为无效，
不进入对照；有效数据仅取 `artifacts/asr-natural-endpoint-v1/{baseline,aligned}/verified-replay`。
复跑脚本现支持 `--tail-silence-ms`，并在回放前自动拒绝 worker 源码指纹不一致。

| 音频 | 旧/新首条 ms | 旧/新最终 ms |
| --- | ---: | ---: |
| command | 1335 / 1004 | 5291 / 5273 |
| crossapp | 1232 / 998 | 12321 / 12301 |
| continuous-first | 1223 / 963 | 7931 / 7935 |
| continuous-second | 1216 / 1018 | 5859 / 5901 |

四段最终文本逐字相同；既有 ASR 错字仍在。自然停顿下没有重现前一轮
立即 finish 的 200–300 ms 最终反馈退化。此结果仍是小样本，不能推导一般正确率。

随后用 command.wav 跑实际 Qwen→JEV→公开 CU MCP，计算器完成清空和 2+3。
独立新 MCP 连接读取，明确观测到表达式 2+3 和显示值 5；证据为
`artifacts/asr-natural-endpoint-v1/calculator-verification.json`。
完整执行记录：`artifacts/voice-audio/20260921-235032-5f4efa/events.jsonl`，
结果：`artifacts/voice-smoke/asr-aligned-calculator-v1.json`。
首条 ASR 在 trace 8452 ms，首次动作派发 10147 ms，最后点击派发 28581 ms；
首条转写到最后派发 20129 ms，仍不满足“几乎即时执行”。此次是 PCM 回放，
不包含实际麦克风采集验收；它证明功能链路，不能证明速度目标已达成。

通过检查公共服务处于 idle、无启用执行会话后，仅重启本项目 jev-voice，
确认新 runtime_id 与源码一致、Qwen 预热完成、未开启麦克风。
部署状态见同目录 `deployed.json`。未改动或重启外部 CU MCP 服务。
183 项测试、Ruff、JS 语法检查及离线构建通过。下一重点是逐步 CU 观测/执行耗时。

## 2026-09-21：CU 执行耗时与选择粒度复查

对 `20260921-235032-5f4efa` 的分解见 `artifacts/cu-choice-efficiency-v1/timing.json`：
5 次 click 合计 11850 ms，单次 2260–2728 ms；6 次 get_app_state 合计 5528 ms，
单次 656–2041 ms；10 次模型请求合计 4748 ms。应用发现后台运行，与上述时段重叠，
不能把各项累计值相加当作端到端总耗时。公开工具 schema 没有关闭截图、跳过返回观测
或批量元素操作参数。bridge 已复用动作返回的树；执行前读取用于重获 app lease 和
验证观察未变化，不能在没有协议依据时删掉。

对相同清空后状态，将 INSERT_TEXT 操作说明改为更短、更明确的“一次键盘调用输入
多个 ASCII 字符”做模型请求对照，每种三次。原版和候选均 3/3 选择 CLICK；
候选只提高 INSERT_TEXT 概率，未改变动作。因此没有向生产加入该提示。
请求、响应摘要和实验脚本保存在 `artifacts/cu-choice-efficiency-v1/`。
该实验不操作桌面，仅发生六次真实选择模型请求。无需因未改动生产代码重启服务。

既有操作定义交接实验（本文件 20:38 左右记录）曾使模型选择一次输入 2+3，
但此次又连续点击，说明该优化尚不能稳定减少轮次。后续需以多场景端到端任务的
操作数和最终结果判断，不继续以某一个提示或一次选择的概率变化宣称加速。

## 2026-09-22：当前纯 CU 自动路由计算器首批 5 题

扩展已有 LiveSession 浏览器测试器，使其也支持冻结 C 组：
`python -m scripts.check_live_browser_cases artifacts/cu100-native-calculator-v1 --ids C08,C09,C10,C12,C15`。
预期值仅用于初始化和独立评分；没有修改生产策略或 MCP。测试仍提供所有应用选项。
本轮是转写事件，不包含麦克风/ASR。183 项测试、Ruff、JS 语法和离线构建通过。

- C08 背景叙述、C09 引用旧指令、C15 同段 ASR 替换通过，最终值分别为 0、0、18。
- C10 未说完请求失败：最终仍为 0，但 SHOW_APP 改变前台，违反冻结 no_actions。
- C12 连续补充失败：实际 `ln(4×7)`，结果 `3.3322045`，不是 28。
  setup 最后一次独立读为 0；模型 SHOW_APP 后首次观察已为 `ln()`，
  在本轮任何计算器输入动作之前出现。来源未确认，不归因于外部干扰或 MCP 缺陷。
  原始失败保留，并标注初态漂移，不能当作干净的选择模型准确率样本。
  模型随后点击 4、乘、7、等于，最后 BLOCKED，没有把错误结果报成满足。

原始证据、独立读回、输入版本、操作序列均在该目录；
`timing-and-caveats.json` 保存操作数与耗时。3/5 仅是这批单 App 检查，
不代表完整 100 题通过率；其它应用内容保持不变尚未全量独立验收。
下一批须特别核对准备结束到首个任务动作之间的初态漂移。

## 2026-09-22：计算器初态核对与评分器修正

`artifacts/calculator-initial-drift-v1` 两次独立探测均为清空→读回0→open激活→读回0，
没有重现 C12 的 ln() 初态漂移，原因仍未确认。测试器现于第一次任务写入前核对
生产 fresh 刚验证的显示值；若与准备结果不同，停止并保存 expected/observed。
不会重置后偷偷继续、也不会把预期值传入策略。此前原始失败保留。

第二批 C01、C05、C07、C18 通过。C06 原始字符串评分失败，但 AX 明确为 `(-8)`，
用户要求只是将当前8改为负数，不能要求额外按等于以满足评分器。
新增数字等价比较：只解析一个有限数字，可带一层括号，不计算任何表达式；
`4×7`、`ln(28)`、`(4+7)` 不会被当成已完成的数值。
C06 的更正单独保存 `artifacts/cu100-native-calculator-v2/C06-adjudication.json`，
原始记录不覆盖。第二批据此为5题通过；前两批原始7/10，更正后8/10，仍含C12初态争议。

显示读取改为定位 AX“输入”滚动区内唯一文本，避免历史记录或表达式区域污染结果。
所有目前12份已保存AX读回结果均与新读取器一致；13项针对评分的检查通过。
新增 `scripts/report_live_cases.py` 汇总每题实际结果、操作数、输入到最后动作返回耗时、
模型请求数，保留重复题目与失败，不以最好一次覆盖历史结果。它输出原始分数，
独立裁定文件需在报告中另列。测试不改变生产运行时，也不改动外部MCP。

### 后续运行到 17 题：再次出现初态漂移，已停止桌面批次

沿用原运行句柄完成第二批后，第三批继续未跑题，C02、C03、C04、C11、C13、C14
通过。C16 在首次计算器写入前，fresh 读回 `ln()`，与夹具准备的 `0` 不同；
测试守卫抛错，未调用原派发 gate，未发送数字8的MCP写入。
LiveSession 将此包装为 action.uncertain 并在history记录尝试，不能据history中有click
推断点击已发送，实际 mcp.start 是执行证据。该题原始失败保留并标注初态异常。

只读检查发现用户此前指定的“Computer Use”任务
`01a0bd0b-a7e2-7fd3-bbf9-fd869ab5fc7a` 的最新turn仍为 inProgress；
这说明可能存在桌面并发，但没有当前工具内容，不能认定它造成ln()。
本项目批次句柄已正常结束，未干预或终止另一任务、未修改MCP。
C17、C19、C20、C21、C22、C23、C24、C25 尚未跑。

`artifacts/cu100-native-calculator-first17.json` 保存17题原始记录（13通过、4失败），
不覆盖历史；C06数字显示裁定后为14通过，剩余C10行为不符、C12/C16初态异常。
这不是14/17的干净模型成功率，也不包括麦克风/ASR。
新增13项评分器测试后，全套196项通过，Ruff、JS语法与离线构建通过。

## 2026-09-22：区分派发前失败与执行结果不确定

C16 的初态检查在原 before_dispatch 之前拒绝，但旧 LiveSession 将所有普通异常
都记为 action.uncertain 并写入history，造成没有调用MCP的click被当成尝试历史。
现在每次动作维护本轮 dispatch 边界：只有 gate 完成且 action.dispatch 已记录后，
后续普通异常才进入 uncertain；之前的失败记 action.not_dispatched，停止会话，
不新增执行历史、不自动重试，显式恢复时重新观察和选择。

新增两项并发actor测试覆盖 freshness准备失败和新语音校验失败，确认没有派发、
没有历史、没有uncertain，恢复必须显式进行。已有派发后断连测试仍确认禁止恢复重放。
这是本仓库执行桥接层修复，不改外部MCP。全套198项测试、Ruff、两份JS语法和构建通过。

只读查询确认另一“Computer Use”任务仍inProgress，所以本轮未继续桌面实验。
确认本项目工作台idle后更新8767，runtime `f802cc45f3a34d42` 与源码一致，
本地ASR预热完成、无录音或桌面会话。部署证据 `artifacts/dispatch-boundary-v1/deployed.json`。
计算器剩余8题、文本编辑25题、跨App25题与真实麦克风/速度目标仍未完成。

## 2026-09-22：工作台显示执行边界

Telemetry 派生最近执行状态，工作台在决策区域显示未派发、等待工具返回、已返回或
结果不确定。“实际操作”计数改名“已返回操作”，不再暗示工具返回等于任务验收。
派发前失败不增加返回次数/执行延迟；完成一次后清空延迟起点，避免跨动作误配。

新增两项Telemetry测试，全套200项、Ruff、JS语法和离线构建通过。
用实际renderDashboard代码加最小DOM替身核对四种状态及空态清除，
`artifacts/execution-feedback-v1/render-check.json`；这不是视觉浏览器验收。
服务新HTML包含执行状态节点，runtime `a86cab72a3f43b00` 已部署且本地ASR预热，
部署记录见同目录deployed.json。另一CU任务仍inProgress，本轮未占用桌面，未修改MCP。

## 2026-09-22：文本编辑25题测试器接入（离线验证，尚未实跑）

单 App runner 支持冻结T组，必须显式传入已打开的自有测试文件路径。
原生初始化、首次任务修改前初态核对、时序转写注入、禁止外部窗口写入、
新连接独立读回与精确正文比较均接入当前LiveSession。没有改题目或给策略预填答案。

检查发现 relay 的可选 document_url 绑定原先采用字符串包含关系，可能接受
Test.txt.backup 作为 Test.txt；现对根窗口解析出的 URL 作全值相等比较。
这是本仓库协议适配修复，外部MCP不变。离线覆盖中文多行、同名不同路径、
URL后缀冒用、正文缺失；focused suite 34项通过。T组真实App回放尚未执行，
不能把测试器就绪记成25题通过。已询问另一任务是否仍操作桌面，等待测试时段信息。

### 用户确认桌面空闲后的计算器续跑

用户明确确认另一任务已停止操作。计算器剩余8题分v4/v5继续首次运行，
25题原始评分18通过、7失败；C06数字显示更正后19通过，分类见
`docs/cu100-native-calculator-results.md` 和 `artifacts/cu100-native-calculator-first25.json`。

C22发现回放顺序错误：中间事件要求during=choice，后一个定时事件却越过它先送入，
实际顺序0、2、1。ready_events现严格保留序列，较早的阶段/动作/时间条件未满足时
阻塞后句，不修改冻结文字或条件。新增两项离线测试；审计已存B/C记录仅发现C22有
顺序倒置，见 `artifacts/replay-order-audit-v1/results.json`。
C22单独复测顺序0、1、2，最终7，通过；C23启动超时单独复测最终11，通过。
首次失败不覆盖。C25生成非ASCII运算字符导致键盘参数被拒绝，仍未修复。
全套203项测试、Ruff、JS语法和离线构建通过。

已创建独立文件 `artifacts/cu100-text-fixture/jev-cu100-test.txt` 并在TextEdit打开，
不复用先前用户/测试文档。T组首次回放从T01、T02、T03、T05、T06开始，
每次写入均绑定精确文件URL，完成后新MCP连接独立验证。

### TextEdit 首批5题完成

`artifacts/cu100-native-textedit-v1`：T01、T02、T03、T06通过；T05失败。
独立读回T05仅剩“已完成”，原“项目记录”丢失。实际执行为聚焦正文→Enter→
TYPE_TEXT；文本模型生成的完整替换参数只有“已完成”，不是“项目记录\n已完成”。
MCP按该参数替换了全文；不能归因于工具丢掉原文。模型随后选择LISTEN。
这暴露选择模型/文本模型对追加任务与全文替换语义衔接仍不可靠，必须修复并回归。
所有修改仅发生于新建的自有测试文件，不复用用户原文档。

原始正文、逐步工具返回和文字模型输出均保留在各题trace。
汇总 `artifacts/cu100-native-textedit-first5.json`：五题首次回放4通过1失败，
其余20题以及跨App25题尚未跑；真实语音端到端即时响应目标仍未达成。

## 2026-09-22：全文替换的文字参数契约

T05失败trace确认：文本助手看到了原文“项目记录”和用户的追加要求，仍只生成了
“已完成”。同一上下文各重放3次，原提示/新提示都3/3正确，不能由此声称提示显著提升
成功率或原问题稳定复现。完整对照见 `artifacts/whole-field-contract-v1`。

将 TEXT_VALUE 的输出含义明确为“执行后字段应包含的完整值”：无论光标/选区如何，
此操作替换整个字段；追加或局部修改需要把未变部分包含在输出中。保持原Jev选择、
OpenRouter生成文本的分工，没有按用户文字匹配追加意图、拼接正文或增加App特例。

新真实回放 `artifacts/cu100-native-textedit-contract-v1`：T05追加、T06局部替换、
T07删除第二行均通过新连接独立AX验证；T05最终“项目记录\n已完成”。
T05仍依次SHOW_APP、CLICK、ENTER、TYPE_TEXT，未消除冗余步骤，也未证明普遍稳定。
T05/T06属于独立复测，T07是首次；不覆盖之前T05失败。

203项测试、Ruff、两份JS语法和离线构建通过。公共工作台空闲时已更新，
runtime `354073e3ada8039b` 与源码一致，Qwen已预热无录音，部署记录见
`artifacts/whole-field-contract-v1/deployed.json`。外部MCP未修改或重启。
