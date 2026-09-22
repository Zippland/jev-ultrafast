# 应用路径桥接验证

2026-09-22：TextEdit T11 首次测试在模型选择观察 TraeWork CN 时失败。公开 list_apps 已提供 /Applications/TRAE SOLO CN.app/，bridge 只保留 bundle ID，MCP 因本机及挂载卷存在同 bundle ID 安装而拒绝调用。

本次仅修改本仓库：保留公开应用路径，用于 get_app_state、原生操作和应用激活；返回 AX 状态仍校验 bundle ID。路径变化时丢弃旧 adapter；运行中目录出现重复 bundle ID 时明确失败，避免静默覆盖。没有修改或重启外部 MCP。

验证：205 项离线测试通过，Ruff、两份 JS 语法检查、离线构建通过。真实只读 get_app_state 使用观察到的 TraeWork CN 路径成功，并验证返回 bundle ID；证据 artifacts/observed-app-path-v1/read-only-check.json。未对 TraeWork CN 写入。

后续 TextEdit T12–T25 批次 v3 / v4 均在前置检查中检测到语音工作台活动，未执行测试写入。没有将其计为题目失败，也没有重启活动工作台；服务仍为此前版本 354073e3ada8039b，本次修复尚未部署到服务。

同时工作台报告单操作超过 255 个目标，属于另一个尚未修复的问题。不能静默截断目标集合，也不能将本次路径修复视作解决该上限问题。
