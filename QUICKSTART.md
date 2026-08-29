# Telegram-only Quick Start

Get private mobile notifications, approval, prompts, reads, and interrupts without a public server or tunnel.

## 1. Install persistent local services

**macOS/Linux:**

```bash
git clone https://github.com/wang-borong/herdr-remote
cd herdr-remote/relay
./install-telegram-only.sh
```

The installer creates restartable user services for the authenticated, loopback-only relay and Telegram bot. Cloudflare is skipped entirely.

**Windows PowerShell:**

```powershell
git clone https://github.com/dcolinmorgan/herdr-remote
Set-Location herdr-remote
herdr plugin link .
./relay/start.ps1
```

The Windows launcher binds to `127.0.0.1` with no tunnel by default. Set
`HERDR_RELAY_TOKEN` before enabling a tunnel or binding beyond loopback.

## 2. Configure Telegram

1. Open `@BotFather` in Telegram and send `/newbot`.
2. Choose Telegram setup in the installer and paste the token when prompted.
3. Open the new bot in a private chat and send the exact one-time `/start <pairing-code>` command displayed by the installer.
4. Accept the test message.

The installer authorizes both your chat ID and user ID. Credentials are stored in `~/.config/herdr-remote/secrets.env` with owner-only permissions. The machine needs outbound internet access to Telegram, but no public IP, webhook, or tunnel.

## 3. Monitor and control

Send `/start` for the clickable dashboard, then use `/agents`, `/read`, `/reply`, `/send`, `/interrupt`, or `/digest`.

Replies are submitted through `herdr agent prompt`, so Codex receives an actual prompt submission instead of a pasted newline. Persistent Trust is hidden by default.

## 4. Check services

```bash
systemctl --user status herdr-relay
systemctl --user status herdr-telegram
```

On macOS, use `launchctl print` commands from the installer summary.

## 5. Security notes

- Use only the authorized private bot chat.
- Never share `secrets.env`, the BotFather token, or a Relay URL containing `?token=`.
- Telegram Bot chats are not end-to-end encrypted; avoid exposing secrets in terminal output.
- Keep `HERDR_TG_ALLOW_PERSISTENT_TRUST=0` unless permanent trust is genuinely required.

See [TELEGRAM_ONLY.md](TELEGRAM_ONLY.md) for detailed operation, optional persistent trust, Linux linger, logs, and uninstall instructions.
