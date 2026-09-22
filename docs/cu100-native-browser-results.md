# 纯 CU 浏览器冻结子集：2026-09-21

25题全部首次运行，20通过、5失败；不同批次仅接着跑未执行题，没有用重跑覆盖失败。只评估文字转写→Jev/OpenRouter→公开cua-relay→Chrome，不含ASR或麦克风，也不代表完整100题。原始评分与运行器快照保留在六个batch目录。

| 用例 | 结果 | 证据目录 |
|---|---|---|
| B01 | 通过 | cu100-native-browser-v1 |
| B02 | 通过 | cu100-native-browser-v1 |
| B03 | 通过 | cu100-native-browser-v1 |
| B04 | 通过 | cu100-native-browser-v1 |
| B05 | 通过 | cu100-native-browser-v1 |
| B06 | 通过 | cu100-native-browser-v1 |
| B07 | 通过 | cu100-native-browser-v1 |
| B08 | 通过 | cu100-native-browser-v1 |
| B09 | 通过 | cu100-native-browser-v1 |
| B10 | 失败 | cu100-native-browser-v1 |
| B11 | 失败 | cu100-native-browser-v3 |
| B12 | 通过 | cu100-native-browser-v4 |
| B13 | 通过 | cu100-native-browser-v4 |
| B14 | 失败 | cu100-native-browser-v4 |
| B15 | 失败 | cu100-native-browser-v5 |
| B16 | 通过 | cu100-native-browser-v6 |
| B17 | 通过 | cu100-native-browser-v2 |
| B18 | 通过 | cu100-native-browser-v6 |
| B19 | 通过 | cu100-native-browser-v6 |
| B20 | 通过 | cu100-native-browser-v2 |
| B21 | 通过 | cu100-native-browser-v2 |
| B22 | 通过 | cu100-native-browser-v2 |
| B23 | 通过 | cu100-native-browser-v2 |
| B24 | 失败 | cu100-native-browser-v2 |
| B25 | 通过 | cu100-native-browser-v3 |

## 不能被最终分数掩盖的问题

- B10 输入“在浏览器搜索”未给搜索内容，模型激活Chrome后点击地址栏，被实验范围保护阻止。冻结要求不操作，所以失败；不能说输入了错误搜索词或已经提交。
- B11、B14、B15、B24 搜索目标选择越出实验WebArea；B14明确选了地址栏。守卫在派发前拒绝，不代表MCP执行失败。对于未明确指定实验搜索框的话语，地址栏搜索存在解释空间，保持原评分并单列歧义，不加网站专用规则迁就分数。
- B13 最终“山峰”正确，但第二条输入到达约4761 ms，旧值“海浪”仍于7822 ms派发。Jev的pending判断为EXECUTE(.69)，随后才修正。最终通过并不证明改口能及时阻止过时写入。
- B12 已确认实际顺序：填写北京→收到改口→填写上海→搜索；B18、B19分别检查撤回提交与后续授权提交。B11、B16同时检查背景期间无提前修改。

## 范围与限制

所有最终字段用新的公开MCP连接独立读取AX，并与实验网页的事件oracle比对；不采信模型的LISTEN/SATISFIED。全部应用仍作为选择候选；实验保护拒绝操作非实验App/网页区域，越界选择算失败。只跑浏览器子集，未独立读取其他App作全局不变性评分。

v1仅单句回放；v2起支持时序输入并显式评分全部输入到达。after_actions统计已返回控件操作，排除观察、App激活、等待；与旧绑定窗口runner的差别写进manifest，不能无说明混成同一历史实验。每轮错误终止批次，确认执行器退出后下一批继续剩余题。

完整逐题数据：artifacts/cu100-native-browser-summary.json。生产代码本轮未改变，未重启MCP或工作台；仅更新测试器及文档。170项离线测试、Ruff、app.js语法及构建通过；此后测试器接管中断处理的小改动单独通过Ruff。
