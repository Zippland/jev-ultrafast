# 语音第一版验证 · 2026-09-21

## 实际链路

Jev Ultrafast 的操作／目标选择 → 按需 OpenRouter 文本生成 → Browser Harness 或独立 cua-relay MCP。
语音输入为本地 Qwen3-ASR-1.7B 8bit，复用 MiraJelly 的流式分段与识别实现。
交互和调试展示参考 Jev Voice Browser；不运行其独立服务，不使用它的英文正则候选提取。

自动发现运行中的 App 与 Chrome 标签页，用户不选择 App、标签页或工具。
切换对象与操作均由 Jev 选择；目标必须来自真实 DOM / AX。选中需要文字的操作后，代码调用文本模型，
没有额外的“是否调用文本模型”分类题。填字段先生成内容，随后检查最新状态并执行。

本机实际文本服务：`https://openrouter.ai/api/v1`，模型 `inception/mercury-2.5`。
CU 使用 `cua-relay serve` 公共接口，未修改或导入外部 CU 仓库。

## 实测结果

真实 Chrome 打开工作台，通过前端麦克风按钮与 AudioWorklet 输入中文 WAV。
仅这个测试标签页的 getUserMedia 被替换为 WAV MediaStream；随后本地 ASR、Jev、OpenRouter、
原生应用和浏览器执行全部真实运行，没有模拟模型结果或工具执行。
这验证了前端采集管线，不代表物理麦克风权限和声学效果已经验收。

| 用例 | 独立核对结果 | Jev 请求 | OpenRouter 请求 | 实际操作 |
| --- | --- | ---: | ---: | ---: |
| TextEdit 写“语音联动成功”，再到浏览器输入同文并搜索 | AX 正文、DOM 输入及搜索事件全部匹配 | 11 | 2 | 3 |
| 计算器清空后计算 2 + 3 | 新 AX 读取显示 5 | 8 | 0 | 5 |

“实际操作”不含只读取其他 App／标签页的路由步骤。两次成功运行各有 13 和 8 次 HTTP 尝试。
跨应用产生 17 个转写版本，计算器产生 7 个转写版本。
从 ASR 就绪到首个操作派发分别为 15.162 秒和 11.860 秒，到最后操作返回分别为 29.031 秒和 56.194 秒；
这些是本次音频回放的端到端测量，包含语音时长、识别和真实工具延迟，不是 Jev 单次推理耗时。

测试文档实际由 CU 暴露为 `artifacts/cu100/JEV-CU100-SANDBOX.txt`；独立验证同时检查了该文件 URL。
CU 当前按 App 的主窗口读取，不能把 macOS `open` 调用当作任意窗口路由成功的证据。

首轮跨应用因模型连接失败在写入前暂停，失败 trace 保留。模型传输现在最多尝试 3 次，
每次 HTTP 尝试及错误类型单独记录；工具操作不会因此重放。后续两次成功运行未发生 HTTP 重试。

证据（项目相对路径）：

- `artifacts/voice-smoke/v1-verification.json`：最终独立检查与调用计数。
- `artifacts/voice-smoke/v1-independent-ax.json`：新读取的原生 AX 结果。
- `artifacts/voice/20260921-162629-df1a6d/events.jsonl`：跨应用成功。
- `artifacts/voice/20260921-162904-9ac3ff/events.jsonl`：计算器成功。
- `artifacts/voice/20260921-162439-df6f58/events.jsonl`：保留的模型连接失败。
- `artifacts/voice-tools/20260921-161839/`：浏览器工具测试、截图和 trace。

浏览器工具测试通过 9 项结果检查，覆盖替换、局部选择、选区插入、全选快捷键、上传、容器滚动、
历史前进后退、标签页创建／关闭；还执行并保存了页面截图。

## 检查与试用

- `uv run ruff check .`：通过。
- `uv run pytest -q`：87 项通过，不调用付费服务。
- JS 语法检查与 AudioWorklet 16 / 44.1 / 48 kHz 三项重采样检查：通过。
- `uv build`：通过，wheel 包含前端、ASR worker、VAD 资产及桥接代码。

`uv run jev-voice` 启动于 `http://127.0.0.1:8767/`，Chrome 中点击麦克风。
首次真实试用需要允许浏览器／系统麦克风权限。当前关闭麦克风会暂停尚未派发的操作，
因此说完后保持开启，等待执行结束再关闭。已派发的操作可能仍会返回。

这是第一版可试用原型，不是 100 题通过声明。CU 的读取和派发仍可能各需数秒。
没有自由坐标拖拽、任意原生窗口 ID、任意代码执行或 iframe/shadow DOM 全覆盖。
完整底层能力和实际接入范围见 `tool-inventory.md`。
