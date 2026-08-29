# Telegram-only 安全部署

这一模式让手机通过私人 Telegram Bot 监控和控制本机 Herdr；配置 SSH Agent Source 后，也可以让本机 Relay 作为跳板控制局域网主机。不需要 VPS、Cloudflare、Tailscale、公网 IP 或路由器端口转发。

```text
手机 Telegram
      ⇅ Telegram Bot API（仅出站 HTTPS）
本机 herdr_telegram.py
      ⇅ 带 token 的 localhost WebSocket
本机 herdr_relay.py
      ⇅ herdr CLI
本机 Herdr Agents
```

## 安全默认值

- Relay 只监听 `127.0.0.1`，不会暴露到局域网或公网。
- Relay token 强制生成，并保存在权限为 `0600` 的 secrets 文件中。
- Telegram 同时校验私人 `chat_id` 和发送者 `user_id`。
- 安装时使用一次性配对码，不会自动信任历史聊天记录。
- `/send` 和直接回复使用 `herdr agent prompt`，不会模拟容易失效的 Enter 按键。
- `/interrupt` 使用 Herdr 的规范 `C-c` 键名并等待 Relay 确认。
- 永久 Trust 默认隐藏；启用后仍需要第二次确认。
- Prompt 正文不会进入 Relay 日志或审计日志，只记录长度和摘要。

## 要求

- Linux 或 macOS。
- Python 3.10+ 和 `uv`。
- Herdr 0.8+，并且 `herdr agent prompt --help` 可用。
- 本机和手机能够连接 Telegram。
- 如需控制局域网主机：本机安装 OpenSSH Client，并已配置免交互 SSH 与远端 `herdr`。

## 安装

```bash
git clone https://github.com/wang-borong/herdr-remote.git
cd herdr-remote/relay
./install-telegram-only.sh
```

安装器会执行以下操作：

1. 生成 Relay token。
2. 引导你通过 `@BotFather` 创建 Bot。
3. 显示一次性 `/start <配对码>` 命令。
4. 只接受发送该配对码的私人 Telegram 用户。
5. 安装自动重启的 Relay 和 Telegram 用户服务。
6. 明确跳过 Cloudflare Tunnel。

配置位置：

```text
~/.config/herdr-remote/config.env
~/.config/herdr-remote/secrets.env
```

不要把 `secrets.env`、BotFather token 或包含 `?token=` 的 Relay URL 发到聊天、Issue 或日志中。

## 使用

| 命令 | 功能 |
|---|---|
| `/start` | 打开 Agent 选择面板 |
| `/agents` | 查看 Agent 状态 |
| `/status` | 查看 Relay 连接状态 |
| `/read` | 读取最近输出 |
| `/reply` | 查看输出并提交新 Prompt |
| `/send` | 直接提交新 Prompt |
| `/interrupt` | 向活动 Agent 发送 Ctrl+C |
| `/digest` | 查看当天活动摘要 |
| `/hosts` | 选择本机或 SSH Agent Source 作为 Codex 运行主机 |
| `/browse [目录]` | 浏览所选主机允许的工作目录 |
| `/cd [目录]` | 在所选主机上选择新 Agent 的工作目录 |
| `/cwd` | 查看当前选择的主机和目录 |
| `/codex [Prompt]` | 在所选主机与目录启动 Codex，可同时提交首条 Prompt |
| `/help` | 查看命令和按钮使用说明 |

首次发送 `/start` 后会显示快捷控制面板，可直接点击 Read、Reply、Send、Interrupt、Refresh 和 Help；下方仍会列出当前 Agent，点击 Agent 即可打开输出并回复。Telegram 输入框旁的 Menu 会显示已注册命令，直接输入 `/` 也会出现命令补全。命令菜单仅注册到已配对的聊天。

Agent 阻塞时，Bot 会提供一次性允许、拒绝和“打开输出并回复”按钮。

## 从 Telegram 创建 Codex Agent

推荐使用按钮流程：

1. 发送 `/start`，点击 **Hosts**，选择本机或在线的 SSH Agent Source。
2. Bot 会打开该主机允许访问的目录；默认是用户家目录，也可以点击 **Workspaces** 再次浏览。
3. 逐层打开目录，点击 **Select here** 选择工作目录。
4. 点击 **Codex here** 或控制面板中的 **New Codex**。
5. Codex 就绪后，直接回复 Bot 的 ForceReply 消息提交第一项任务。

也可以使用命令：

```text
/hosts
/browse ~
/cd ~/Projects/herdr-remote
/codex 请解释这个仓库并给出改进建议
```

`/hosts` 的选择会绑定后续目录按钮、`/cd`、`/cwd` 和 `/codex`。切换主机时会清除旧主机的目录选择，防止把远端路径误发给本机。远端主机需要先配置为启用了 Agent discovery 的 SSH Profile，并确保 Relay 主机能够通过免交互 SSH 执行远端 `herdr`；配置方法见 [Remote Shell and LAN access](REMOTE_SHELL.md#同时连接远端-herdr-agents)。

Relay 默认允许访问当前用户的家目录。可在 `~/.config/herdr-remote/config.env` 中配置更小或额外的允许目录，Linux/macOS 使用冒号分隔：

```text
HERDR_WORKSPACE_ROOTS=/home/user:/srv/repos
```

升级时，精确匹配旧默认值 `~/Workspace` 或 `~/Workspace:~/workspace-ai` 的配置会自动迁移为家目录，其他自定义目录不会被覆盖。修改后重启 Relay。目录会解析为真实路径，越过允许范围的 `..` 路径会被拒绝，目录符号链接不会出现在浏览列表中。允许目录内的普通目录和 Git 仓库都可以启动 Codex；Git 标记只用于帮助识别仓库。Relay 始终使用结构化参数调用 Herdr，不提供任意 shell 命令入口。

Pane 输出默认读取最近 60 行，使用 Telegram HTML 的粗体标题和等宽正文显示；内容较长时会自动拆成多条消息，最多保留约 12000 个字符。Codex 完成后的界面页脚会被整理为单独的 `Worked for ...`，后面的下一条 Prompt、模型和路径信息不会发送。

如需调整，在 `~/.config/herdr-remote/config.env` 中设置：

```text
HERDR_TG_READ_LINES=60
HERDR_TG_OUTPUT_MAX_CHARS=12000
HERDR_TG_CONNECT_TIMEOUT=15
```

读取行数会限制在 15–200，输出字符数会限制在 3500–24000。Telegram 连接超时默认 15 秒，可在网络握手较慢时调整为 5–60 秒。修改后重启 Telegram 服务即可。

## 可选：启用永久 Trust

永久 Trust 会让 Agent 后续工具调用不再逐次询问，因此默认关闭。确有需要时，编辑：

```bash
${EDITOR:-vi} ~/.config/herdr-remote/config.env
```

设置：

```text
HERDR_TG_ALLOW_PERSISTENT_TRUST=1
```

然后重启 Telegram 服务。即使开启，Bot 仍会要求第二次确认。

Linux：

```bash
systemctl --user restart herdr-telegram
```

macOS：

```bash
launchctl kickstart -k "gui/$(id -u)/com.herdr-remote.telegram"
```

## Linux 无人值守运行

如果退出 SSH 或桌面登录后用户服务会停止，可启用 linger：

```bash
loginctl enable-linger "$USER"
```

主机必须保持开机且不能进入睡眠。

## 检查状态

Linux：

```bash
systemctl --user status herdr-relay
systemctl --user status herdr-telegram
journalctl --user -u herdr-relay -u herdr-telegram --since today
```

macOS：

```bash
launchctl print "gui/$(id -u)/com.herdr-remote.relay"
launchctl print "gui/$(id -u)/com.herdr-remote.telegram"
```

应用日志位于安装器输出的日志目录；写操作审计保存在 `audit.log`。

## 隐私边界

Telegram Bot 聊天不是端到端加密。`/read`、阻塞提示、分段输出和回复都会经过 Telegram 服务器。不要让 Agent 在终端输出私钥、访问令牌或其他不应离开本机的内容。

## 卸载

```bash
cd herdr-remote/relay
./install-service.sh --uninstall
```

服务会被移除，配置和 secrets 会保留，便于恢复。若要彻底清理，请先撤销 BotFather token，再手动删除 `~/.config/herdr-remote`。
