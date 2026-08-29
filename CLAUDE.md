# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

herdr-remote is a multi-client system for monitoring and approving [herdr](https://herdr.dev) AI agents remotely. It provides a WebSocket relay that bridges the herdr CLI with phone, desktop, Telegram, and terminal clients.

## Architecture

```
Clients (web/mac/ios/telegram/tui)
        │ WebSocket
        ▼
   relay (:8375)  ←── Cloudflare tunnel (public wss://)
        │
        ▼
   herdr CLI (local or SSH to HERDR_REMOTES)
```

The relay (`relay/herdr_relay.py`) is the central hub: it polls herdr for agent state, accepts push events via HTTP POST and UDP, and broadcasts to connected WebSocket clients. Clients send `respond`, `read_pane`, `send_keys`, and `send_text` messages back through the relay to control agents.

The mac and Windows clients can also skip the relay entirely. Their **direct** mode runs the CLI itself — `herdr pane list` locally and `ssh <target> herdr pane list` per configured host — on the same SSH terms as the relay (`ConnectTimeout=5`, `BatchMode=yes`, `HERDR_REMOTE_BIN`). The host list is per client: `herdi_remotes` in `UserDefaults` on macOS, `%LOCALAPPDATA%\herdr-remote\settings.json` on Windows. Nothing in this mode touches the relay, so none of the relay constraints below apply to it.

## Components

| Path | What | Language |
|------|------|----------|
| `relay/herdr_relay.py` | WebSocket+HTTP relay server | Python (websockets, zeroconf) |
| `relay/herdr_telegram.py` | Telegram bot client | Python (python-telegram-bot) |
| `relay/herdr_tui.py` | Terminal TUI client | Python (textual) |
| `web/index.html` | Mobile/desktop web app (single file) | HTML/CSS/JS |
| `demo-worker/` | Cloudflare Worker mock relay for demos | JS |
| `herdi-mac/` | macOS menu bar app | Swift (SPM) |
| `herdi-ios/` | iOS app with widgets + Live Activities | Swift (XcodeGen) |
| `herdi-win/` | Windows tray app + tray flyout panel | C# (.NET 8 / WPF) |

## Running Components

All Python scripts use [PEP 723 inline metadata](https://peps.python.org/pep-0723/) — `uv run` handles dependency installation automatically.

```bash
# Relay (main server)
uv run relay/herdr_relay.py

# Full setup with Cloudflare tunnel
relay/start.sh

# Telegram bot
HERDI_TG_TOKEN="..." HERDI_TG_CHAT_ID="..." uv run relay/herdr_telegram.py

# Terminal TUI
uv run relay/herdr_tui.py

# Demo worker (Cloudflare)
cd demo-worker && npx wrangler dev

# macOS app
cd herdi-mac && ./build.sh

# iOS app (generate Xcode project)
cd herdi-ios && xcodegen generate

# Windows app (needs the .NET 8 SDK; `dotnet build` also works off-Windows
# for compile checking thanks to EnableWindowsTargeting)
# ./build.ps1 -Framework is 25 MB against the default's 166 MB for identical memory;
# ./build.ps1 -Compress halves the download and doubles the memory. See herdi-win/README.md.
cd herdi-win && ./build.ps1
```

## Key Environment Variables

| Variable | Purpose |
|----------|---------|
| `HERDR_RELAY_PORT` | Relay WebSocket port (default: 8375) |
| `HERDR_RELAY_HOST` | Relay bind address (secure default: `127.0.0.1`) |
| `HERDR_RELAY_TOKEN` | Required shared secret for relay auth |
| `HERDR_ALLOW_REMOTE_BIND` | Explicit opt-in for non-loopback relay binding |
| `HERDR_REMOTES` | Comma-separated SSH targets to poll |
| `HERDR_SSH_CONFIG_FILE` | User SSH config used by Remote Shell and Agent Sources |
| `HERDR_BIN` | Path to herdr binary (default: `/opt/homebrew/bin/herdr`) |
| `HERDR_RELAY` | Relay URL used by clients (default: `ws://127.0.0.1:8375`) |
| `HERDR_SESSION` | Boot-time default herdr session; a client can override it per source at runtime via `session_switch` |
| `HERDI_RENDER` | Windows client only: `hardware` restores WPF's GPU path (default is software — see `herdi-win/README.md#memory`) |
| `HERDR_TG_USER_ID` | Telegram controller user allowlist |
| `HERDR_TG_REQUIRE_PRIVATE_CHAT` | Reject non-private Telegram chats (default: true) |
| `HERDR_TG_REQUIRE_LOCAL_RELAY` | Reject non-loopback relay URLs (default: true) |
| `HERDR_TG_ALLOW_PERSISTENT_TRUST` | Show persistent trust controls (default: false) |

Runtime session overrides are persisted per Agent Source to `active_sessions.json` inside `HERDR_LOG_DIR`, so they survive relay restarts.

## Web App

The web app is a build-free static application: `web/index.html` provides markup while `web/app.css` and `web/app.js` provide the responsive UI. It is served by the Relay and can also be deployed to Cloudflare Pages.

## WebSocket Protocol

Messages are JSON with a `type` field:

**Server → Client:** `agents` (complete state snapshot), `agent_update` (single-pane state merge), `blocked` (approval prompt), `pane_content` (terminal read), `sessions` (per-source herdr session lists and the active selection)

**Client → Server:** `respond` and `question_toggle`/`question_submit` (interactive approvals), `agent_prompt`/`agent_prompt_queue` (semantic Prompt submission, including Codex images), `read_pane`/`get_history`, `send_keys`/`send_text`, `session_switch` (select a Herdr session per Agent Source), workspace browse/read/download/upload operations, terminal operations, and push subscription operations.

Interrupt is represented by the allowlisted `C-c` key value. Pane IDs exposed by SSH Agent Sources are source-scoped; clients must return the public Pane ID received from the Relay rather than reconstructing it.

## Deployment

- Web app: Cloudflare Pages (push to main deploys `web/`)
- Demo worker: `npx wrangler deploy` from `demo-worker/`
- macOS app: `herdi-mac/build.sh` produces `dist/Herdi.app`
