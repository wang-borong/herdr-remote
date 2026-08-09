# Tailscale 网页控制台

这一模式让手机或其他电脑通过 Tailscale 私网打开 Herdr 控制台，同时保留 Telegram Bot。网页不需要公网端口、VPS、Cloudflare Tunnel 或浏览器中的长期 Relay token。

```text
手机 / 其他电脑浏览器
        ⇅ Tailscale HTTPS（仅 tailnet）
本机 tailscaled / Tailscale Serve
        ⇅ 身份头 + localhost 反向代理
127.0.0.1:8375 / herdr_relay.py
        ⇅ 结构化 herdr CLI
本机 Herdr Agents
```

## 网页功能

- 响应式 Agent Dashboard，兼容桌面和手机浏览器。
- 按状态筛选和搜索本机 Agent。
- 查看最近 60、120 或 200 行 Pane 输出，并可自动刷新。
- 通过 `herdr agent prompt` 提交 Prompt；Agent 已在工作时显示为安全排队，而不会因等待下一次状态变化误报失败。
- 二次确认后发送规范的 `C-c` Interrupt。
- 在配置的 Workspace 白名单中浏览目录。
- 在白名单内的普通目录、Git 仓库或其子目录中安全创建新的 Codex Agent。
- Agent 阻塞时显示允许、拒绝和 Trust 等上下文操作。
- 可选 Web Push、PWA 安装、深色/浅色主题。

## 安全边界

- Relay 始终只监听 `127.0.0.1`。
- Tailscale Serve 负责 HTTPS 和 tailnet 访问控制；不会启用 Funnel。
- Relay 只信任来自回环连接的 `Tailscale-User-Login`、`Tailscale-User-Name` 身份头。
- `HERDR_TAILSCALE_ALLOWED_USERS` 会再次校验登录名。设备分享给外部用户时，对方也无法越过此白名单。
- WebSocket 必须同源，阻止其他网页借用你的 Tailscale 身份发起控制请求。
- 旧式 `?token=` 网页访问会换成短时 HttpOnly、SameSite 会话 Cookie；token 不写入 `localStorage`。
- Prompt 正文不会写入 Relay 日志或审计日志。
- 工作目录会解析为真实路径，并限制在 `HERDR_WORKSPACE_ROOTS` 中；目录符号链接不会出现在列表中。
- 远程端只提供明确的 Herdr RPC，不提供任意 Shell 命令入口。
- 本机回环网络属于信任边界：不要在存在不可信本地系统用户或不可信本地进程的共享主机上启用此模式。

Tailscale 官方也建议：使用 Serve 身份头鉴权时，后端服务只监听 localhost。本项目会在启动时强制检查这一点。

## 前置要求

1. 先完成 Telegram-only/Relay 安装：

   ```bash
   cd relay
   ./install-telegram-only.sh
   ```

2. Linux 使用 systemd。安装脚本支持：

   - Arch Linux 及兼容衍生发行版（包括 CachyOS）
   - Debian 13 (trixie)

3. 手机安装 Tailscale App，并登录与电脑相同的 tailnet。

## 安装

网页代码完成后，在本机仓库中运行：

```bash
cd relay
./install-tailscale-web.sh
```

脚本会：

1. 在 Arch/CachyOS 等 Arch 兼容系统使用 `pacman`，或在 Debian 13 配置 Tailscale 官方 trixie 软件源并安装依赖。
2. 启用 `tailscaled.service`。
3. 如果机器尚未登录，运行带 3 分钟超时的 `tailscale up` 并显示登录 URL，不会无限等待。
4. 自动读取当前 Tailscale 登录名，写入允许用户白名单。
5. 开启 Relay 的 Tailscale Web 身份认证并重启用户服务。
6. 运行：

   ```bash
   sudo tailscale serve --bg http://127.0.0.1:8375
   ```

7. 输出类似 `https://your-machine.your-tailnet.ts.net` 的访问地址。
8. 在配置前后检查 Funnel；如果无法确认仅为私网访问，脚本会停止。

如果只想配置已有的 Tailscale：

```bash
./install-tailscale-web.sh --configure-only
```

### Clash Meta / Mihomo 共存

Clash Meta 的 Fake-IP 模式可能把 `controlplane.tailscale.com` 解析到
`198.18.0.0/15`。普通程序会由 Clash TUN 接管，但 `tailscaled` 可能无法通过
这个 Fake-IP 建立控制连接，表现为安装器停在登录提示且始终不显示 URL。

安装器检测到 Fake-IP 后，会自动尝试 Clash Verge Rev 常用的 `7897` 和传统
Clash 常用的 `7890` 端口。检测成功时，仍可直接运行：

```bash
./install-tailscale-web.sh
```

如果使用的是其他端口，可以显式指定 Clash 的 HTTP 或 mixed 端口：

```bash
./install-tailscale-web.sh \
  --tailscale-proxy http://127.0.0.1:7897
```

安装器会先验证代理能访问 Tailscale 控制面，然后写入：

```text
/etc/systemd/system/tailscaled.service.d/herdr-remote-proxy.conf
```

并重启 `tailscaled`。使用这一配置时，需要保持 Clash 本地代理可用。如果你的
Clash mixed 端口不同，请替换 `7897`。也可以设置
`HERDR_TAILSCALE_PROXY=http://127.0.0.1:端口`。

指定一个或多个允许登录名：

```bash
./install-tailscale-web.sh \
  --allowed-users you@example.com,second-controller@example.com
```

不要把 `*` 作为日常配置。虽然脚本支持显式的 `--allowed-users '*'`，但这会允许所有能够访问该设备的已认证 tailnet 用户控制 Agents。

## 手机使用

1. 在手机安装并登录 Tailscale。
2. 保持本机在线，且不要让机器休眠。
3. 用手机浏览器打开安装器输出的 `https://...ts.net` 地址。
4. 可通过浏览器菜单“添加到主屏幕”，获得接近原生 App 的全屏体验。

手机页面先显示 Agent 列表；点击 Agent 后进入输出与 Prompt 页面。左上角返回按钮回到 Dashboard。新建 Agent 按钮在顶栏始终可用。

## 配置

配置文件仍位于：

```text
~/.config/herdr-remote/config.env
~/.config/herdr-remote/secrets.env
```

网页相关配置：

```text
HERDR_RELAY_HOST=127.0.0.1
HERDR_TAILSCALE_WEB=1
HERDR_TAILSCALE_ALLOWED_USERS=you@example.com
HERDR_WORKSPACE_ROOTS=/home/user/Workspace:/srv/repos
```

修改后重启 Relay：

```bash
systemctl --user restart herdr-relay
```

如果退出桌面或 SSH 后用户服务会停止，可以启用 linger：

```bash
loginctl enable-linger "$USER"
```

## 状态检查

```bash
systemctl --user status herdr-relay
journalctl --user -u herdr-relay --since today
sudo tailscale serve status
sudo tailscale funnel status
tailscale status
```

Relay 日志和写操作审计日志仍位于安装器输出的日志目录。

## Funnel 与其他 Serve 路由

安装器不会主动执行 `tailscale funnel`。运行 `tailscale serve` 后，Herdr 使用的 HTTPS 端口是 tailnet 私有的。

如果机器上已经有任何 Funnel，脚本会在发布 Herdr 前停止，也不会擅自删除。只有在确认这台机器不需要任何现有 Funnel 路由时，才使用：

```bash
./install-tailscale-web.sh --reset-funnel
```

该选项会执行 `tailscale funnel reset`，可能影响其他应用。

配置完成后脚本还会再次读取 Serve 状态。如果无法确认 Funnel 已关闭，会以失败状态退出，并提示立即检查 `sudo tailscale funnel status`，避免把 Agent 控制台误暴露到公网。

## 停用网页访问

先检查是否有其他应用共用 Tailscale Serve：

```bash
sudo tailscale serve status
```

如果这台机器的 Serve 仅用于 Herdr，可以清除 Serve 配置：

```bash
sudo tailscale serve reset
```

然后将 `HERDR_TAILSCALE_WEB=0` 写入 `config.env` 并重启 Relay。Telegram Bot 不受影响。

## 官方参考

- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Install Tailscale](https://tailscale.com/docs/install)
