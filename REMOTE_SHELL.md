# Tailscale Remote Shell 与局域网服务器

Remote Shell 在现有 Agent Dashboard 之外提供完整的 Linux 用户终端。它适合查看和编辑代码、运行测试、执行 Git 提交、检查 systemd、维护工作机，以及从工作机继续 SSH 到同一局域网中的服务器。

```text
电脑 SSH 客户端 ── Tailscale SSH ──────────────┐
                                               │
手机 / PC 浏览器 ─ Tailscale Serve ─ Herdr PTY ├─ 本机 Shell / Git / tmux
                                               │
                                               └─ ssh ─ 局域网服务器
```

两条访问链路互相独立：即使 Herdr 网页暂时不可用，仍可以使用官方 Tailscale SSH 登录本机维护服务。

## 一键启用

先确保基础 Tailscale Web 已经可用，然后在仓库的 `relay` 目录运行：

```bash
./install-tailscale-web.sh --remote-shell
```

脚本会：

1. 安装 OpenSSH Client 与 tmux。
2. 运行 `sudo tailscale set --ssh`，开启官方 Tailscale SSH Server。
3. 设置 `HERDR_WEB_TERMINAL=1`。
4. 将当前明确授权的 Tailscale 登录名写入独立的 `HERDR_TERMINAL_ALLOWED_USERS` 白名单。
5. 创建权限为 `0600` 的 `~/.config/herdr-remote/ssh-hosts.json`。
6. 重启 `herdr-relay`，保留已有 Telegram 和 Agent Dashboard 配置。

安装器与 Relay 启动检查都会拒绝为 Remote Shell 使用 `*` 用户白名单。完整终端只能授权给明确的 Tailscale 登录名。

如果依赖已经安装：

```bash
./install-tailscale-web.sh --configure-only --remote-shell
```

## 原生 Tailscale SSH

从另一台已经登录同一 tailnet 的电脑执行：

```bash
tailscale ssh 本机用户名@cachyos-wpc
```

也可以使用普通 SSH 客户端和 MagicDNS：

```bash
ssh 本机用户名@cachyos-wpc
```

`tailscale ssh` 会额外使用 Tailscale 控制面提供的节点 SSH Host Key 信息。最终访问权限仍由 tailnet 的 SSH/Grants 策略决定；开启 `--ssh` 并不会绕过管理员策略。

手机可以使用支持 SSH 的 App。先连接 Tailscale，再把 `cachyos-wpc` 或完整的 `*.ts.net` MagicDNS 名称作为主机名。

## Web Terminal

打开原来的 Herdr Tailscale HTTPS 地址，选择顶部或底部的 **Remote Shell / 终端**：

- **本机终端**：当前 Linux 用户的完整登录 Shell。
- **SSH 服务器**：由本机作为网关，连接配置好的局域网 SSH 目标。
- **tmux 持久会话**：浏览器断线、手机切后台或 Relay 重连后，可重新附着到原会话；SSH 服务器会话中新建窗口或分屏时，会继续连接同一台远程服务器，而不会落回 Relay 本机 Shell。
- **移动输入尺寸同步**：软键盘展开只改变可见行数，不改变 xterm 列数；若窗口尺寸确实变化，Relay 会在 tmux 确认新行列数后再按顺序放行输入，避免长命令跨行时覆盖行首或光标错位。
- **移动快捷键**：Esc、Tab、一次性 Ctrl、Ctrl+C、Ctrl+L、四向方向键、PgUp、PgDn、复制和剪贴板粘贴。
- **跨端复制**：复制按钮读取当前 tmux Pane 历史并打开原生纯文本窗口；手机可长按使用系统选择手柄，PC 可拖选、复制全部，或对已有 xterm 选区使用 Ctrl/⌘+C。
- **tmux 鼠标与触控**：Web 专用 tmux Server 默认开启鼠标；手机端可直接触碰快捷栏完成窗口切换、分屏和缩放。
- **tmux 快捷栏**：支持单独启用 Ctrl+B Prefix，以及新建/切换窗口、窗口列表、左右/上下分屏和 Pane 缩放。
- **快捷命令**：`pwd`、`ls -la`、`git status`、`git diff --stat`、`htop`。
- **终端字体**：网页本地提供 FiraCode Nerd Font Mono，Powerline、Starship 与 Nerd Font 图标不依赖手机或电脑预装字体。

点按单独的 **Ctrl** 后，按钮会保持高亮，并把下一次字母、方向键或 PgUp/PgDn 作为 Ctrl 组合发送；再次点按可取消。焦点切换不会消耗待发送的 Ctrl，因此手机上点按 Ctrl 后再快速输入字母也能组成正确按键。

tmux 的 `mouse on` 会优先接收终端拖动事件，所以网页的 **复制内容** 不依赖浏览器直接选择 xterm 画布。点击后，Relay 使用专用 tmux socket 捕获当前 Pane 的纯文本 scrollback（最多保留最近 1 MiB），再交给浏览器的原生只读文本窗口。没有 tmux 的临时 Shell 会改用浏览器当前保留的 xterm 缓冲区。

每个入口使用独立的 tmux Session，并使用专用 socket：

```bash
tmux -L herdr-web list-sessions
tmux -L herdr-web attach -t herdr-local
```

会话可以跨浏览器断线和 Relay 重启保留，但系统重启后需要重新创建。为避免 PC 与手机使用不同列数时争夺同一个 Pane 尺寸，同一入口只保留最后打开的 Web tmux Client；从另一浏览器打开会分离旧 Client，但不会结束 tmux Session 或其中运行的任务。该专用 Server 会固定使用最新 Client 的窗口尺寸、启用 `mouse on`，并使用 `Ctrl+B` 作为 Prefix；用户普通 tmux 配置不受影响，Herdr 的 `Ctrl+X` 也可以正常透传。

网页 tmux 快捷栏发送以下组合：

```text
Ctrl+B c    新建窗口
Ctrl+B p/n  上一个 / 下一个窗口
Ctrl+B w    窗口列表
Ctrl+B |/_  左右 / 上下分屏
Ctrl+B z    缩放当前 Pane
```

单独点按 **Prefix** 后，按钮会保持高亮，并把下一次按键与 `Ctrl+B` 原子发送；这可以避免浏览器焦点序列打断 Prefix。PC 可直接使用鼠标操作 tmux，手机可触碰快捷栏按钮操作。

## 添加局域网服务器

在 Remote Shell 页面点击 **添加服务器**，填写：

- 显示名称，例如“Debian 编译机”。
- SSH 目标，例如 `builder@192.168.1.50`。
- SSH 端口，默认 `22`。
- 可选备注与颜色。
- Files 允许访问的远端目录，默认 `~`（远端用户家目录）；每行一个，最多 8 个。

也可以填写 `~/.ssh/config` 中的 Host 别名：

```sshconfig
Host build-server
    HostName 192.168.1.50
    User builder
    Port 22
    IdentityFile ~/.ssh/id_ed25519
```

然后在网页中把 SSH 目标填写为 `build-server`。

Herdr 只保存目标、端口和显示信息，不保存密码或私钥。第一次连接时，Host Key 确认和密码提示会直接出现在终端中。推荐在本机提前配置 SSH Key：

```bash
ssh-keygen -t ed25519
ssh-copy-id builder@192.168.1.50
```

示例配置见 `relay/ssh-hosts.example.json`。

## 从 Files 上传到本机或局域网服务器

拥有 Remote Shell 白名单权限的用户可以在 **Files** 页面把当前浏览设备上的文件上传到 Relay 本机或任一 SSH Profile：

1. 在 Files 顶部选择本机或 SSH 主机。
2. 进入一个具体的允许目录；配置根目录的虚拟列表本身不能作为上传目标。
3. 点击 **上传** 多选文件，或直接把文件拖到 Files 面板。
4. 默认保留已有同名文件，并把新文件命名为 `name (1).ext`、`name (2).ext`；如需原子替换，可勾选 **覆盖同名文件**。

上传以 512 KiB 分块通过 WebSocket 传输，并显示单文件及总进度。Relay 先写入隐藏临时文件，校验声明大小后再原子提交；取消、断线或校验失败会清理未完成的临时文件。Files 原有的允许目录、隐藏路径和符号链接限制同样适用于上传，因此隐藏文件名和包含路径分隔符的文件名会被拒绝。

默认单文件上限为 2 GiB，可通过 `HERDR_WORKSPACE_UPLOAD_MAX_BYTES` 调整，Relay 会把配置限制在 1 MiB–64 GiB。SSH 上传需要目标机安装 Python 3，并使用 Batch Mode；请先配置 SSH Key 或 SSH Agent，交互式密码提示只适用于 Web Terminal，不能用于 Files 后台传输。

普通 Relay Token 客户端仍可使用原有只读 Files，但不能上传。SSH Profile 即使没有启用 Agent discovery，也会出现在已授权用户的 Files 主机列表中；其允许目录始终用于 Files，启用 Agent discovery 后再同时用于远端 Codex 启动。

### 同时连接远端 Herdr Agents

编辑或添加 SSH 服务器时，可以启用 **发现并控制这台主机上的 Herdr Agents**。同一个 SSH Profile 随即同时提供：

- Remote Shell 终端入口。
- 远端 Herdr Agent 发现、状态与健康检查。
- Pane 输出、Prompt、审批、Tab 排队和 Interrupt。
- 受限目录浏览。
- 在所选远端目录中创建 Herdr workspace 并启动 Codex。

需要填写远端 `herdr` 命令名或绝对路径。SSH Profile 默认使用远端用户家目录 `~`；配置的 1–8 个允许目录会同时作为 Files 和 Agent 启动范围。旧版 Profile 中精确匹配 `~/Workspace`，或同一用户目录下 `Workspace` 与 `workspace-ai` 组合的默认值，会自动迁移为该用户家目录；包含其他自定义路径的 Profile 保持不变。所有路径都会在远端解析后重新检查边界，目录列表不会展示隐藏目录或符号链接。

Relay 为远端 Pane 使用 `<profile-id>::<raw-pane-id>` 全局 ID。例如 `build-server::w0:p1`，因此不同机器都存在 `w0:p1` 时不会发生状态覆盖或命令串台。本机 Pane ID 保持不变。

远端 SSH 查询使用 Batch Mode，推荐先验证密钥和 Herdr 路径：

```bash
ssh build-server '/home/builder/.local/bin/herdr pane list'
```

Telegram Bot 使用同一组 Agent Sources。发送 `/hosts` 可以选择本机或某个在线 SSH 主机；选择后 Bot 会打开该主机的允许目录，后续 `/browse`、`/cd`、`/cwd`、目录按钮和 `/codex` 都会保持在该主机范围内。这样可以让运行 Relay 的本机充当跳板，从 Telegram 在局域网服务器上选择目录并启动 Codex，而无需把远端 Relay 暴露到网络。

不可达的主机会在新建 Agent 对话框中显示为离线；各主机并行检查，不会按主机数量串行累积超时。

## 从其他电脑直接跳到局域网服务器

不必先手动登录本机再执行第二次 SSH。PC 上可以使用 ProxyJump：

```bash
ssh -J 本机用户@cachyos-wpc builder@192.168.1.50
```

Remote Shell 页面会为每个 SSH Profile 生成并复制对应命令。也可以写入客户端的 `~/.ssh/config`：

```sshconfig
Host work-gateway
    HostName cachyos-wpc
    User 本机用户

Host lan-build
    HostName 192.168.1.50
    User builder
    ProxyJump work-gateway
```

不少手机 SSH App 也支持 Jump Host/Bastion Host，可把 `cachyos-wpc` 配置为跳板机。

## 可选：把本机作为 Tailscale 子网路由器

如果希望其他 tailnet 设备直接访问整个局域网，而不只通过 SSH 跳板，可以显式配置路由：

```bash
./install-tailscale-web.sh \
  --configure-only \
  --advertise-routes 192.168.1.0/24
```

脚本会开启必要的内核转发并调用：

```bash
sudo tailscale set --advertise-routes=192.168.1.0/24
```

之后还必须在 Tailscale Admin Console 中批准该 Route，并通过 tailnet Access Controls 允许目标用户访问。完成后，其他设备可以直接执行：

```bash
ssh builder@192.168.1.50
```

子网路由会扩大 tailnet 可访问的网络范围。如果只有少量 SSH 服务器，Web Terminal 或 ProxyJump 通常更简单、更容易控制。

## 安全边界

- Relay 仍只监听 `127.0.0.1`，由 Tailscale Serve 提供 HTTPS 和身份头。
- Agent 控制白名单与完整终端白名单分离。
- Web Terminal 只接受经过 Tailscale 身份认证的浏览器 WebSocket；Relay Token 客户端不能打开 Shell。
- Files 上传沿用同一份 Remote Shell 明确用户白名单；普通 Relay Token 只有 Files 浏览、预览和下载权限。
- 上传只写入当前允许目录，拒绝隐藏路径、符号链接和路径型文件名，并通过临时文件原子提交。
- SSH Profile 进行结构化校验，不能注入 `ssh -o` 或 Shell 参数。
- Profile 文件以 `0600` 原子写入。
- Relay 审计日志只记录打开、关闭和 Profile 变更，不记录按键、命令或终端输出。
- PTY 不继承 Relay 的 Telegram Token、Relay Token、VAPID Private Key 或 uv 临时 Python 环境。
- xterm.js 固定版本并从本机提供，不加载 CDN 脚本。
- 每个浏览器连接最多附着一个 PTY，并有全局会话数量上限。

Remote Shell 拥有运行 `herdr-relay` 的 Linux 用户的完整权限。不要以 root 用户运行 Relay，也不要把终端白名单授权给不完全信任的用户。

## 配置项

```text
HERDR_TAILSCALE_SSH=1
HERDR_WEB_TERMINAL=1
HERDR_TERMINAL_ALLOWED_USERS=you@example.com
HERDR_SSH_HOSTS_FILE=/home/user/.config/herdr-remote/ssh-hosts.json
HERDR_SSH_CONFIG_FILE=/home/user/.ssh/config
HERDR_TERMINAL_MAX_SESSIONS=6
HERDR_TERMINAL_CWD=/home/user
HERDR_TERMINAL_SHELL=/bin/zsh
HERDR_WORKSPACE_UPLOAD_MAX_BYTES=2147483648
```

修改后重启：

```bash
systemctl --user restart herdr-relay
```

## 状态与排错

```bash
systemctl --user status herdr-relay
journalctl --user -u herdr-relay -n 100 --no-pager
tailscale status
tailscale debug prefs
tmux -L herdr-web list-sessions
ssh -vvv build-server
```

常见问题：

- **Tailscale SSH 拒绝访问**：检查 tailnet SSH/Grants 策略、目标 Linux 用户名和设备 Tags。
- **网页没有 Remote Shell 权限**：检查 `HERDR_WEB_TERMINAL=1` 和 `HERDR_TERMINAL_ALLOWED_USERS` 是否包含当前 Tailscale 登录名。
- **局域网服务器连接超时**：先在本机终端执行相同的 `ssh` 命令，确认路由、防火墙和 sshd。
- **SSH Key 未生效**：检查本机 `~/.ssh/config`、文件权限和 `SSH_AUTH_SOCK`。
- **SSH Files 可以浏览但无法上传**：确认目标机有 `python3`，并从 Relay 本机执行 `ssh -o BatchMode=yes build-server true` 验证无需交互的密钥认证。
- **上传按钮不可用**：先进入一个具体允许目录，并确认当前 Tailscale 登录名位于 `HERDR_TERMINAL_ALLOWED_USERS`。
- **重新打开网页后看不到原现场**：运行 `tmux -L herdr-web list-sessions`，确认 tmux Server 仍在运行。
- **仍显示方框或缺失图标**：先强制刷新网页；字体由 Relay 本地提供，不需要在手机上单独安装 Nerd Font。

## 停用

关闭官方 Tailscale SSH：

```bash
sudo tailscale set --ssh=false
```

关闭 Web Terminal，在 `~/.config/herdr-remote/config.env` 中设置：

```text
HERDR_TAILSCALE_SSH=0
HERDR_WEB_TERMINAL=0
```

然后重启 Relay。Agent Dashboard 和 Telegram Bot 不受影响。

## 官方参考

- [Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)
- [Tailscale Subnet Routers](https://tailscale.com/docs/features/subnet-routers)
- [Tailscale Access Controls](https://tailscale.com/docs/features/access-control)
