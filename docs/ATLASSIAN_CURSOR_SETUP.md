# Cursor Atlassian 插件初始化指南

> 站点示例：`https://jacksonyuanyoyo.atlassian.net`  
> Confluence 目标页：[CAN-RAG API](https://jacksonyuanyoyo.atlassian.net/wiki/spaces/SCRUM/pages/66469/CAN-RAG+API)（`pageId=66469`，`cloudId=308a505a-b882-42ae-b713-2965b58d6c7e`）

## 当前仓库状态

本项目已在 `.cursor/settings.json` 中启用插件：

```json
{
  "plugins": {
    "atlassian": {
      "enabled": true
    }
  }
}
```

插件通过 **HTTP MCP** 连接 Atlassian 官方服务：`https://mcp.atlassian.com/v1/mcp`（OAuth 2.1），**无需**在 `~/.cursor/mcp.json` 里再手写一条 atlassian 配置。

若 Agent 调用 Jira/Confluence 工具时出现 `401 Unauthorized`，说明 **尚未在 Cursor 里完成浏览器授权**，请按下面步骤操作。

---

## 一、前置条件

| 项 | 要求 |
|----|------|
| Cursor 版本 | **≥ 3.0.12**（旧版 OAuth token 交换有 404 bug；你本机若为 3.6.x 即可） |
| Atlassian | **Cloud** 站点，且账号能访问 Jira / Confluence |
| 浏览器 | 可打开 Atlassian 登录与授权页 |
| 首装（组织） | 若提示 *「Your site admin must authorize this app」*，需 **站点管理员** 先完成一次授权 |

---

## 二、在 Cursor 里完成 OAuth（必做）

1. 打开 **Cursor Settings**（`Cmd + ,`）。
2. 进入 **MCP**（或 **Features → MCP**，以你当前 Cursor 菜单为准）。
3. 在 MCP 服务器列表中找到 **Atlassian**（来自已安装的 Atlassian 插件，不是 `~/.cursor/mcp.json` 里的 `taiga`）。
4. 点击 **Connect** / **Login** / **Needs authentication** 旁的登录按钮。
5. 浏览器打开 Atlassian 授权页 → 登录 `jacksonyuanyoyo.atlassian.net` 对应账号 → **Allow access**。
6. 授权成功后回到 Cursor，Atlassian 应显示为 **Connected**，工具列表可用。

### 授权失败时

1. 命令面板（`Cmd + Shift + P`）执行：**`Cursor: Clear All MCP Tokens`**
2. 关闭 Cursor 后重新打开，再重复「Connect」。
3. **View → Output**，下拉选择 **Atlassian MCP** 相关通道，查看 OAuth 日志（token 交换 404 等多为 Cursor 版本或代理问题）。
4. 公司网络：若启用 Atlassian **IP 允许列表**，需把当前出口 IP 加入允许范围。
5. 仍提示管理员授权：让站点 Admin 在 [Connected apps](https://support.atlassian.com/security-and-access-policies/docs/manage-your-users-third-party-apps/) 中批准 Rovo MCP 应用。

---

## 三、验证是否初始化成功

在 Cursor Agent 对话中让助手执行（或你自己在 MCP 面板点测试）：

- `atlassianUserInfo` → 应返回当前用户，而非 401
- `getAccessibleAtlassianResources` → 应列出可访问的 Cloud 站点与 `cloudId`

成功后即可：

- `updateConfluencePage` 同步 `docs/confluence/frontend-api-integration.md` 到 Confluence
- 使用 Jira 创建/查询 Issue 等 Skills

---

## 四、与本项目 Confluence 同步

授权完成后，可让 Agent 更新页面，或本地生成 payload：

```bash
cd /Users/jackson/Documents/project/Fidelity/CAN-RAG-BackEnd
.venv/bin/python scripts/publish_confluence_can_rag_api.py > /tmp/confluence_update.json
```

再由已授权的 MCP 工具 `updateConfluencePage` 提交（`pageId=66469`，`cloudId=308a505a-b882-42ae-b713-2965b58d6c7e`，`contentFormat=markdown`）。

---

## 五、不要与 `taiga` MCP 混淆

`~/.cursor/mcp.json` 中的 **`taiga`** 是自建 Taiga 服务，与 Atlassian 无关。Atlassian 仅通过 **Cursor 插件** 注入的 `plugin-atlassian-atlassian` 使用。

---

## 参考

- [Atlassian Rovo MCP Server（GitHub）](https://github.com/atlassian/atlassian-mcp-server)
- [Cursor 论坛：Atlassian OAuth 404 修复说明（3.0.12+）](https://forum.cursor.com/t/atlassian-mcp-plugin-oauth-authentication-fails-at-token-exchange/155766)
