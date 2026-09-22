# Browser / Computer Use 工具清单

核对日期：2026-09-21。来源为本机实际安装的 Browser Harness 0.1.13 `helpers.py`，以及
`cua-relay serve` 的 MCP `initialize` / `tools/list`（服务报告版本 0.1.0）。
这是底层能力清单，不表示全部已适配成 Jev 可选操作，也不是已确定的路由设计。

## Browser Harness：29 个公开辅助函数

浏览器侧是 Python/CDP 接口，并非 29 个 MCP 工具。`cdp` 本身就是其中一个函数；其后仍有完整 CDP 方法空间。

| 函数 | 主要参数 | 能力 |
| --- | --- | --- |
| `goto_url` | url | 打开网址 |
| `page_info` | 无 | URL、标题、视口和滚动信息，或阻塞中的对话框信息 |
| `capture_screenshot` | path, full, max_dim | 截图 |
| `click_at_xy` | x, y, button, clicks | 坐标点击，支持不同鼠标键和点击次数 |
| `type_text` | text | 在当前焦点插入文字 |
| `fill_input` | selector, text, clear_first, timeout | 填写输入框，处理框架事件 |
| `press_key` | key, modifiers | 按键或组合键 |
| `dispatch_key` | selector, key, event | 给 DOM 元素派发键盘事件 |
| `scroll` | x, y, dy, dx | 指定位置横向或纵向滚动 |
| `upload_file` | selector, path | 为文件输入框设置本地文件 |
| `list_tabs` | include_chrome | 列出标签页 |
| `current_tab` | 无 | 当前连接的标签页 |
| `switch_tab` | target, activate | 切换连接的标签页；默认不改变 Chrome 可见页 |
| `activate_tab` | target | 把指定标签页变为 Chrome 可见页 |
| `new_tab` | url | 创建标签页 |
| `close_tab` | target | 关闭指定或当前连接的标签页 |
| `ensure_real_tab` | 无 | 从内部页或失效目标切到真实用户页 |
| `iframe_target` | url_substr | 查找匹配的 iframe target |
| `wait` | seconds | 等待固定时长 |
| `wait_for_load` | timeout | 等待文档加载完成 |
| `wait_for_element` | selector, timeout, visible | 等待元素出现或可见 |
| `wait_for_network_idle` | timeout, idle_ms | 等待网络空闲 |
| `js` | expression, target_id | 执行页面或 iframe JavaScript |
| `cdp` | method, session_id, params | 调用底层 Chrome DevTools Protocol |
| `drain_events` | 无 | 读取积累的浏览器事件 |
| `http_get` | url, headers, timeout | 独立 HTTP 读取，不经过浏览器 |
| `start_recording` | name, title | 开始记录截图和操作 trace |
| `stop_recording` | 无 | 结束记录 |
| `recording_dir` | 无 | 当前记录目录 |

工具安装/诊断、认证、更新等 CLI 管理命令不属于此处的浏览器交互函数。

## Computer Use：10 个 MCP 工具

| 工具 | 主要参数 | 能力 |
| --- | --- | --- |
| `list_apps` | 无 | 列出正在运行及最近 14 天使用过的应用 |
| `get_app_state` | app | 必要时建立该 App 的操作会话，获取其当前主要窗口的截图和 AX 树 |
| `click` | app, element_index 或 x/y, mouse_button, click_count | 按元素编号或截图坐标点击 |
| `perform_secondary_action` | app, element_index, action | 执行元素实际暴露的辅助 AX 动作 |
| `set_value` | app, element_index, value | 设置可写 AX 元素的值 |
| `select_text` | app, element_index, text, selection, prefix, suffix | 选中文字或把光标置于文字前后，可用上下文消歧 |
| `scroll` | app, element_index, direction, pages | 对元素上下左右滚动，可使用小数页数 |
| `drag` | app, from_x/from_y, to_x/to_y | 按截图坐标拖拽 |
| `press_key` | app, key | 按键或组合键，使用工具规定的 xdotool key 语法 |
| `type_text` | app, text | 使用键盘输入字面文字 |

当前服务没有独立 `switch_app`、`launch_app` 或任意 `window_id` 参数。
`get_app_state(app=...)` 可指定要控制的 App，后续操作也带 `app`；这不等于单独验证过的“切前台”能力。

## 当前 Jev 适配范围

| 能力 | 当前适配 |
| --- | --- |
| 网页观察 | 仓库自己的 `snapshot.js`，通过 Browser Harness/CDP 原子读取 DOM 和编号元素 |
| 网页操作 | 左右键／双击、替换／插入文字、局部文字选择、有限快捷键、下拉选项、页面与容器滚动、等待、导航、前进后退、刷新 |
| 浏览器管理 | 发现并切换已有标签页、创建／关闭标签页、显示当前受控标签页、为真实文件输入控件上传本地文件、保存截图 |
| 原生应用观察 | 后台缓存的 `list_apps` + `get_app_state`；复用操作返回的 AX，派发前重新校验 |
| 原生应用操作 | 元素编号点击、`set_value` 替换、`type_text` 插入、`select_text` 选区、有限快捷键、容器滚动、实际暴露的辅助 AX 动作 |
| 等待界面变化 | Jev 选择 `WAIT`，桥接层短暂等待并重新观察；无需新增语音 |
| 切换操作对象 | 语音桥接层选择已发现的 App 或 Chrome 标签页，读取新状态后再选下一步 |
| 文本生成 | 选中需要字面参数的操作后调用文本模型，包括替换／插入／选中文字、URL 和明确的绝对文件路径 |
| 尚未成为通用 Jev 选项 | 无坐标锚点的 CU 拖拽、任意按键组合、iframe/shadow DOM 全覆盖、任意 JS/CDP、独立 HTTP 抓取和录制管理 |

Jev 不生成选择器、坐标或可执行代码。底层 `js` / `cdp` / 坐标接口由桥接代码持有，
如需扩展动作，先将真实观察到的目标和受支持参数编成选项。
CU 的 `drag` 虽存在于 MCP，但当前 AX 文本不提供元素坐标，不能把接口存在当成已有可靠拖拽目标。
工具记录和读取状态由桥接自动进行。上表是语音入口的扩展范围，原固定目标演示与冻结 CU100 实验保持独立。
浏览器标签页、原生 App 和 macOS 前台焦点是不同状态；讨论切换策略时需明确切的是哪一项。
