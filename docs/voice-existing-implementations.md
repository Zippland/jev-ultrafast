# 现有实时语音执行方案调研

核对日期：2026-09-21。范围是流式语音、Jev、工具执行、改口与取消；不是一般语音聊天框架排行。

## 结论

已有非常接近的开源演示和 macOS 产品，应该优先复用。此次没有找到一个经过公开验证、能直接满足“中文连续输入与改口、无关键词/正则意图逻辑、Jev choice + 文本模型、原生 CU + Browser Use、独立 MCP”全部条件的完整实现。

不需要再研究音频传输、通用会话取消、事件日志这些已有基础设施。剩下的适配重点是：最新语音意图如何使未执行动作失效、已执行动作如何由模型修正，以及把所选动作交给现有 MCP。取消模型请求或停止播音本身不会撤销一次已经发生的点击。

## 最接近的四类实现

| 项目 | 已实现 | 与当前需求的差异 | 建议用途 |
| --- | --- | --- | --- |
| [jev-voice-browser](https://github.com/moritzkremb/jev-voice-browser) | Web Speech partial → Jev → Playwright；概率面板、输入合并、并发请求上限、动作日志 | 英文文字候选用正则；前缀匹配拆命令；部分 ASR 修订被忽略；只控制浏览器 | 最接近的交互对照和代码参考，不整套采用策略 |
| [jev-canvas](https://github.com/gaborishka/jev-canvas) | 语音与指向位置 → Jev → tldraw；逐次 partial 选择动作/对象/位置 | 特定画布；指示词与文字候选依赖正则；固定时长/置信度阈值 | 参考如何同时展示输入、候选和执行反馈 |
| [Yappy + Jev](https://yappy.biz/jev/) | 作者发布的 Mac 产品；AX 控件表 → Jev 操作与目标 → 文本模型填字，困难步骤交回完整 agent | 未找到可复用实现源码；公开 Agent Mode 教程是按住说、松开执行；没有核实持续改口行为 | 可作为产品体验对照，不能当作已验证的流式改口实现 |
| [LiveKit Agents](https://docs.livekit.io/agents/logic/tools/async/) / [Pipecat](https://github.com/pipecat-ai/pipecat/blob/main/examples/README.md) | 语音管线、会话事件、工具调用与取消已有框架和示例 | 不是开箱即用的 Jev 桌面执行器；需保留/接入 choice 与 MCP 适配 | 需要通用语音会话基础设施时复用，避免再造框架 |

## jev-voice-browser：已读源码，不只看演示

审阅固定提交：`054db0f3dbf537af63a8117632d3f941ccd520e1`，提交日期 2026-09-18。MIT 许可；只读克隆保存在 `artifacts/research/jev-voice-browser/`，未启动它控制测试浏览器，也未调用它的付费集成测试。

- [controller.js](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/controller.js#L70)：收 partial，200 ms 合并，最多两个在途请求，旧请求可通过 AbortSignal 取消。执行期间暂缓新决策，动作后重读页面。
- [controller.js 第 80–83 行](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/controller.js#L80)：用已消费文本的前缀拆下一条命令；若 ASR 改写了已执行前缀就直接返回，新增内容少于两个空格分词也返回。因此不能直接照搬到中文持续改口。
- [controller.js 第 194 行起](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/controller.js#L194)：输入改变后，旧响应仍可能执行某些动作，只是不再把它视为 final 或静默结束。这里没有根据最新完整输入再次判断旧动作是否有效。
- [spans.js](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/spans.js#L8)：英文动词、填充词、目标字段短语、站点名、数字口令都参与正则或词表处理。文字不是由另一个文本模型生成，Jev 只从程序提取的片段里选。
- [policy.js](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/policy.js#L85)：搜索、填写和选项文字等待 final 或 600 ms 静默；一般完整性另有 900 ms 回退。它的“边说边做”并非所有操作都在持续讲话时执行。
- [jev.js](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/src/jev.js#L64)：只取当前 transcript 最后 400 字符，没有传入完整的跨句执行历史；不能从该演示推导长背景与跨 App 改口可靠性。

作者报告的 27/27 是模型加策略在页面 fixture 上的测试；另有真实浏览器演示回放。此次没有复测其成功率或延迟，不能把其数字与我们的真实 App 100 题直接比较。[测试源码](https://github.com/moritzkremb/jev-voice-browser/blob/054db0f3dbf537af63a8117632d3f941ccd520e1/test/integration/jev-decisions.test.js)

## 成熟框架解决到哪一层

LiveKit 提供明确的工具生命周期：可取消工具暴露正在执行任务及按 ID 取消；重复调用可以拒绝、替换或要求模型确认；本地 stdio MCP 有现成适配。这些机制可以复用，但要在我们的执行边界上决定哪些任务可取消，不能通过取消 asyncio 等待假装 GUI 动作已撤销。[异步工具](https://docs.livekit.io/agents/logic/tools/async/) · [MCP](https://docs.livekit.io/agents/logic/tools/mcp/)

LiveKit 的 preemptive generation 是在结束一轮尚未确认前预先生成响应；普通 turn-taking 与用户希望的逐步执行不是同一项保证。其文档也说明长段口述更容易导致预生成结果被丢弃。[预生成](https://docs.livekit.io/agents/multimodality/audio/#preemptive-speech-generation)

Pipecat 有函数取消、超时、取消事件和允许模型取消后台函数的实际示例，也有独立 MCP 示例目录。适合已有 Python 管线，但接 Jev 的选择协议与我们的状态验证仍是适配工作。[异步函数示例](https://github.com/pipecat-ai/pipecat/blob/main/examples/function-calling/function-calling-openai-async.py) · [官方示例索引](https://github.com/pipecat-ai/pipecat/blob/main/examples/README.md)

Yappy 的 Jev 发布页日期为 2026-09-19；Agent Mode 教程更新于 2026-08-25，比 Jev 发布早。教程说明松开 Command 后开始执行；据此只能说尚无已核实的持续改口证据，不能断言最新产品一定不支持。[Jev 发布页](https://yappy.biz/jev/) · [Agent Mode 原文](https://yappy.biz/docs/agent-mode.md)

## 对本项目的建议

1. 先把 `jev-voice-browser` 当作现成体验基准，避免继续凭空设计交互。只复用适合的界面、事件与可取消请求结构，保留 MIT 归属；不引入其英文解析策略。
2. 保留已有稳定 ASR 与独立 `cua-relay` / Browser Harness。为一次本地验证重搭 LiveKit 服务器或整套语音框架未必更省时间；需要远程音频、双向对话或多会话时再选一个成熟框架接入。
3. 继续沿用 choice 选择、文本模型写字的分工。是否改口、是否还应执行、如何修复已改变内容交给模型；队列、有界并发、版本号和派发日志用程序处理。这些是协议和调度，不是自然语言匹配规则。
4. 当前冻结的 100 题作为验收资产保留，不扩成无止境的自研实验。后续改实现时先跑已有的背景、未说完、ASR 修订、显式改口、文本生成中改口、执行后修正和跨 App 小组；有增益后再全量回归。

本次交付是资料与源码核对，不代表已经安装或验收上述产品，也没有变更正在运行的基线策略。
