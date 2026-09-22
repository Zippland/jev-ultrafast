<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

### 实时语音试用（macOS）

```bash
uv run jev-voice
```

打开 **http://127.0.0.1:8767/**，点击麦克风说话，再点击关闭。无需选择应用、标签页或执行工具。
Jev 从正在运行的 App 中选择要观察的界面，再选择真实 AX 控件。浏览器同样作为普通 App 通过 Computer Use 操作，不提供 Browser Use 工具或标签页发现。
需要填写、插入、选择文字或提供网址／文件路径时，才调用 OpenRouter 文本模型生成相应的字面参数。
和原版一样，选中填写目标后先生成文字，再由执行器聚焦并输入；没有额外的“是否调用文本模型”分类题。
bridge 对 ASCII 单行文本使用公开 MCP 的键盘原语替换并读回验证，提交由 Jev 另选；
非 ASCII 文本和多行文本区使用原生 setter，避免键盘路径丢失中文。实际工具调用及填写策略记录在 trace。
当前 relay 的 INSERT_TEXT 仅支持 ASCII；该能力限制同时提供给选择模型和文本模型，非 ASCII 键盘插入在派发前拒绝。
对可读取、可填写的文本框，模型可生成保留其余内容的完整值来执行中文追加或修改；任意光标处的中文插入尚未支持。
字段或焦点无法确认时停止，不重放写入；原生 setter 的提交行为取决于控件，执行后重新观察。
转写持续更新，同一语音段的回改替换旧假设；改口后的待执行动作由 Jev 重新判断，不使用关键词或正则识别意图。
关闭麦克风会暂停尚未派发的操作，已派发的操作可能仍会完成。可以持续开启麦克风；静音不会停止尚未完成的执行。

语音入口的全部电脑操作使用独立安装的 `cua-relay serve` MCP，无需 Browser Harness 连接，也无需修改 CU 仓库。原版 `jev` 浏览器示例仍使用 Browser Harness。
ASR 复用 [MiraJelly 的分段及识别实现](docs/voice-source.md)，模型在本机运行。默认寻找当前用户已安装的
NeraJelly ASR 运行环境和 Qwen3-ASR 权重，也可配置 `VOICE_PYTHON`、`VOICE_MODEL_PATH`。
服务启动时默认预热本地模型，不开启麦克风或创建电脑执行会话；点击录音后复用该进程。
模型会占用常驻内存；设置 `VOICE_PRELOAD=0` 可关闭启动预热。同一连接内再次录音仍复用已加载模型。
语音环境缺失时可在折叠的“调试记录”中用文字验证执行链路。

逐条 trace 保存到 `artifacts/voice/<会话>/events.jsonl`，包含转写版本、模型请求和响应、HTTP 尝试、工具调用、
派发及返回时间、后续界面观测。原始音频不落盘，凭据不写入 trace；应用文本及转写会发送给配置的模型服务。
当前仍是实验版，原生 CU 的读写延迟明显高于模型推理延迟；`LISTEN` 只表示模型暂时没有下一步，不代表任务验收成功。
见 [执行逻辑与验证说明](docs/voice-workbench.md)。
第一版已通过计算器及 TextEdit → 浏览器的中文音频回放，见 [独立结果、调用计数和边界](docs/voice-v1-validation.md)。
持续语音、长停顿及后续性能复测见 [体验验证记录（含 ASR 失败）](docs/voice-experience-validation.md)。

语音入口提供有限按键与快捷键、左右键和双击、局部文字选择及容器滚动。网页导航、标签页及上传等操作由模型选择浏览器的原生界面控件完成，不再提供专用网页工具。
录音工作台本身禁止修改；模型可选择通过原生快捷键打开新窗口，再观察和操作新的窗口。
CU 自由坐标拖拽尚未接入：当前公共 AX 返回没有可绑定的元素坐标，不让 Jev 猜坐标。

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

### Native Computer Use through MCP

The optional CU backend connects to a separately installed and running **Codex-CU** service. This repository does not
import, copy, build, or restart CU. Configure its public stdio entry point in `.env` (a quoted JSON array):

```dotenv
CU_MCP_COMMAND='["node","/absolute/path/to/codex-CU/src/mcp.mjs"]'
```

Keep the CU console running with its required macOS permissions, open the target application, and restart `uv run jev`
after changing configuration. In the inspector, choose **Computer Use · native apps (MCP)**, select an app and window,
enter one goal, then click **Start demo → Run automatically**. **End session** closes only this run's MCP connection;
the CU service and the application remain running. Application text is sent to the configured model providers.
Screenshots are for the inspector only and are not sent to Jev.

```bash
uv run --env-file .env python examples/computer.py --list-apps
uv run --env-file .env python examples/computer.py --app com.apple.calculator --list-windows
uv run --env-file .env python examples/computer.py --app com.apple.calculator \
  --goal '使用计算器计算 1234 × 2345，停在显示计算结果的界面。' \
  --trace artifacts/computer/trace.json
```

```python
from jev_ultrafast import Agent

with Agent.for_app("com.apple.calculator", "Calculate 1234 × 2345.", window_id=12345) as agent:
    for state in agent.run():
        print(state["status"])
```

Use an actual window ID from `--list-windows`; it can be omitted only when the app has exactly one visible window.
Each run owns one persistent MCP session and binds every action to the selected app, window and fresh observation ID.
The current adapter maps indexed AXPress controls, text fields via `set_value`, and exposed menu/vertical page-scroll
actions. CU checks field writability at execution; a read-only field rejection stops the run. Arbitrary coordinates,
keyboard shortcuts, drag, cross-window navigation and password fields are not offered. No model emits code or selectors.

CU may change independently. New runs start the configured MCP entry again and inspect the available tools. Compatible
updates require no code copying; breaking tool/schema or observation-format changes require an adapter update. A restart
or disconnect during an action stops the run without reconnecting or replaying input. Only an explicit pre-dispatch
stale-observation rejection permits re-observation and a new decision. Execution events remain in the exported trace.

There is no silent candidate cutoff in this adapter: a truncated CU tree or more than **255 targets in any one operation
head** stops with an error. Choose a smaller window or a narrower UI state. Hierarchical target selection is not implemented.
CU returns a post-action observation with input results; Jev records the returned execution before consuming that observation.
A successful tool response and Jev's `DONE` still require an independent check of the final application state.

### Streaming intent and mixed CU / Browser Use experiment

The experimental [mixed bridge](jev_ultrafast/mixed.py) reuses Ultrafast's operation and compatible-target questions.
Targets carry their app and execution channel; there is no separate app-classification question.
Native controls come from the independent CU MCP service; the same Chrome test window also
offers DOM controls through Browser Harness. Only the selected operation's target executes. Both channels call
the text model for `TYPE_TEXT`. User intent is not classified with keyword or regex rules.

The benchmark defaults to the public `cua-relay serve` MCP interface backed by the installed official Computer Use
runtime. The [relay adapter](jev_ultrafast/relay.py) validates the app and expected key-window title before dispatch;
the test TextEdit document additionally requires its exact file URL. `--backend codex-cu` selects the independent
implementation for a later comparison. Neither execution repository is changed or imported.

The [CU100 benchmark](benchmarks/cu100/README.md) replays chronological transcript updates against real Calculator,
TextEdit and Chrome windows, including changes during selection/text generation and after earlier actions.
It records raw model responses, actual actions and independent final-state checks. The frozen
[100-case catalog](benchmarks/cu100/catalog.md) and experimental runner are separate from the fixed-goal demo.
The benchmark replays transcripts without a microphone; the voice workbench above supplies local streaming ASR
and automatic discovery of running apps and existing Chrome tabs. Arbitrary native window-ID routing remains unsupported.

### Browser backend

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
