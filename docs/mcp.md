# Vesta MCP Client V1

MCP V1 把远端 MCP Tool 适配为现有 `BaseTool`。`AgentRuntime` 不区分本地工具和
MCP 工具，所有调用仍经过 `ToolRegistry → PermissionHook → ToolExecutor → Hook/Trace`。

当前只支持 `stdio`。默认配置路径是 `backend/.vesta/mcp.json`，也可以通过 CLI
参数 `--mcp-config` 指定其他 JSON 文件。最小配置示例：

```json
{
  "servers": [
    {
      "name": "example",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@example/mcp-server"],
      "env": {"EXAMPLE_API_KEY": "${EXAMPLE_API_KEY}"},
      "permission": "human_approval"
    }
  ]
}
```

`${EXAMPLE_API_KEY}` 会从 Vesta 进程环境读取；变量不存在时只将该 Server 标记为
`failed`。不要把真实密钥提交到 MCP JSON。配置文件能够启动本地命令，因此只应使用
自己信任的配置。

发现后的工具名为 `mcp__<server>__<tool>`。工具默认使用 `human_approval`，可信的
只读 Server 可以显式设置为 `allowed`。来自 Server 的只读 annotations 目前只作协议
信息看待，不会自动绕过本地权限策略。

CLI 启动后使用 `/mcp` 查看连接状态、失败原因和已注册工具。一个 Server 失败不会阻止
其他 Server 或 Vesta 主流程启动。退出 CLI 时会关闭 MCP Session 和 stdio 子进程。

Desktop 可以通过“设置 → 扩展能力 → MCP Servers”添加配置。表单中的参数每行一个，
Host 会把它们写成 JSON `args` 数组；环境变量使用 `KEY=${ENV_NAME}`，界面只展示变量名。
新增、停用、启用或删除配置后需要重启 Vesta Host 才会改变 Server 进程和已注册工具；
重启前 Desktop 会持续显示配置尚未应用的提示。

V1 暂不实现 Streamable HTTP、Resources、Prompts、OAuth、自动重连和动态刷新工具。
