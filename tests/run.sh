#!/bin/sh
# tests/run.sh — tests for herdr-remote
PASS=0; FAIL=0
DIR="$(cd "$(dirname "$0")/.." && pwd)"

if command -v python3 >/dev/null 2>&1 && python3 -c "pass" >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1 && python -c "pass" >/dev/null 2>&1; then
    PYTHON=python
else
    echo "Python 3 is required"
    exit 1
fi

assert_eq() {
  if [ "$1" = "$2" ]; then PASS=$((PASS+1)); echo "  pass: $3"
  else FAIL=$((FAIL+1)); echo "  FAIL: $3 (expected '$2', got '$1')"; fi
}

echo "herdr-remote tests"
echo ""

# --- Relay ---
echo "=== Relay ==="
echo "1. relay syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_relay.py" 2>/dev/null
assert_eq "$?" "0" "herdr_relay.py parses"

echo "1b. relay behavior"
uv run --with 'python-telegram-bot>=21.0' --with 'websockets>=14.0' \
  python -m unittest discover -s "$DIR/tests" -p "test_*.py"
assert_eq "$?" "0" "relay behavior"

echo "2. PEP 723 metadata"
grep -q "requires-python" "$DIR/relay/herdr_relay.py"
assert_eq "$?" "0" "inline deps present"

echo "3. launch scripts executable"
[ -x "$DIR/relay/start.sh" ] && [ -x "$DIR/relay/install-telegram-only.sh" ] && \
  [ -x "$DIR/relay/install-tailscale-web.sh" ] && bash -n "$DIR/relay/install-tailscale-web.sh" && \
  grep -q '"$DISTRO_ID" == "cachyos"' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q 'DISTRO_ID_LIKE' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q -- '--tailscale-proxy' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q -- '--remote-shell' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q -- '--advertise-routes' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q 'tailscale set --ssh' "$DIR/relay/install-tailscale-web.sh" && \
  grep -q -- '--timeout="$TAILSCALE_LOGIN_TIMEOUT"' "$DIR/relay/install-tailscale-web.sh"
assert_eq "$?" "0" "relay installers are executable and parse"

# --- Telegram ---
echo ""
echo "=== Telegram bot ==="
echo "4. telegram bot syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_telegram.py" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram.py parses"

echo "5. telegram demo bot syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_telegram_demo.py" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram_demo.py parses"

echo "6. telegram bot has all commands"
for cmd in cmd_start cmd_agents cmd_status cmd_read cmd_send cmd_reply cmd_trust cmd_interrupt cmd_digest cmd_help cmd_hosts cmd_browse cmd_cd cmd_cwd cmd_codex; do
  grep -q "async def $cmd" "$DIR/relay/herdr_telegram.py" || { FAIL=$((FAIL+1)); echo "  FAIL: missing $cmd"; continue; }
done
PASS=$((PASS+1)); echo "  pass: all 15 commands present"

echo "7. telegram bot env vars documented"
grep -q "HERDR_TG_TOKEN" "$DIR/relay/herdr_telegram.py" && grep -q "HERDR_TG_CHAT_ID" "$DIR/relay/herdr_telegram.py"
assert_eq "$?" "0" "env vars referenced"

# --- TUI ---
echo ""
echo "=== TUI ==="
echo "8. TUI syntax"
"$PYTHON" -c "import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))" "$DIR/relay/herdr_tui.py" 2>/dev/null
assert_eq "$?" "0" "herdr_tui.py parses"

# --- Web app ---
echo ""
echo "=== Web app ==="
echo "9. web app key elements"
WEB="$DIR/web/index.html"
WEB_JS="$DIR/web/app.js"
[ -f "$DIR/web/app.css" ] && [ -f "$DIR/web/manifest.webmanifest" ] && \
  grep -q "WebSocket" "$WEB_JS" && grep -q "agent_prompt" "$WEB_JS" && \
  grep -q "agent_seen" "$WEB_JS" && \
  grep -q 'selectAgent(agent.pane_id, true, true)' "$WEB_JS" && \
  grep -q 'function selectAgent(paneId, updateHistory = true, markSeen = false)' "$WEB_JS" && \
  grep -q "agent_prompt_queue" "$WEB_JS" && grep -q "queue-prompt-button" "$WEB" && \
  grep -q 'message.delivery === "queued"' "$WEB_JS" && \
  grep -q "list_directories" "$WEB_JS" && grep -q "start_agent" "$WEB_JS" && \
  grep -q "list_workspace_files" "$WEB_JS" && grep -q "read_workspace_file" "$WEB_JS" && \
  grep -q "prepare_workspace_download" "$WEB_JS" && \
  grep -q 'case "workspace_listing"' "$WEB_JS" && \
  grep -q 'id="file-workspace"' "$WEB" && grep -q 'data-app-view="files"' "$WEB" && \
  grep -q 'id="file-markdown-content"' "$WEB" && grep -q 'id="file-code-content"' "$WEB" && \
  grep -q "renderMarkdownWorkspaceFile" "$WEB_JS" && \
  grep -q "DOMPurify" "$WEB_JS" && grep -q "hljs.highlight" "$WEB_JS" && \
  grep -q 'elif msg_type == "list_workspace_files"' "$DIR/relay/herdr_relay.py" && \
  grep -q 'elif msg_type == "read_workspace_file"' "$DIR/relay/herdr_relay.py" && \
  grep -q '"workspace_files": True' "$DIR/relay/herdr_relay.py" && \
  grep -q 'path == "/api/workspace-download"' "$DIR/relay/herdr_relay.py" && \
  grep -q "C-c" "$WEB_JS" && grep -q "terminal_open" "$WEB_JS" && \
  grep -q 'id="agent-key-toggle"' "$WEB" && \
  grep -q 'id="agent-keybar"' "$WEB" && \
  grep -q 'aria-busy="false" hidden' "$WEB" && \
  grep -q 'data-agent-key="Enter"' "$WEB" && \
  grep -q 'data-agent-key="Escape"' "$WEB" && \
  grep -q "sendAgentInteractionKey" "$WEB_JS" && \
  grep -q "AGENT_KEY_ACK_TIMEOUT_MS" "$WEB_JS" && \
  ! grep -q 'data-prompt=' "$WEB" && \
  ! grep -q 'data-terminal-command=' "$WEB" && \
  grep -q 'data-terminal-key="escape"' "$WEB" && \
  grep -q 'data-terminal-key="tab"' "$WEB" && \
  grep -q 'case "terminal_resized"' "$WEB_JS" && \
  grep -q "terminalResizePendingId" "$WEB_JS" && \
  grep -q "flushPendingTerminalInput" "$WEB_JS" && \
  grep -q 'resize_id: resizeId' "$WEB_JS" && \
  grep -q '"type": "terminal_resized"' "$DIR/relay/herdr_relay.py" && \
  grep -q 'id="agent-output-terminal"' "$WEB" && grep -q "ansi_content" "$WEB_JS" && \
  grep -q "ensureAgentOutputTerminal" "$WEB_JS" && \
  grep -q "paneIdFromUrl" "$WEB_JS" && \
  grep -q "enableAgentOutputTouchScrolling" "$WEB_JS" && \
  grep -q "AGENT_TOUCH_SELECTION_DELAY" "$WEB_JS" && \
  grep -q "enableNativeAgentOutputSelection" "$WEB_JS" && \
  grep -q "clearBrowserSelectionInside" "$WEB_JS" && \
  grep -q "NATIVE_SELECTION_RELEASE_GRACE_MS" "$WEB_JS" && \
  grep -q "touchStartedWithNativeSelection" "$WEB_JS" && \
  grep -q "cancelExistingSelection" "$WEB_JS" && \
  grep -q "nativeAgentSelectionControllers" "$WEB_JS" && \
  grep -q "restoreNativeSelection" "$WEB_JS" && \
  grep -q "agentOutputAccessibilityTree" "$WEB_JS" && \
  grep -q "selectedAgentOutputText" "$WEB_JS" && \
  grep -q "agentOutputHasSelection" "$WEB_JS" && \
  grep -q "agentOutputSelectionLocked" "$WEB_JS" && \
  grep -q 'selectionLocksSnapshot = agentOutputSelectionLocked' "$WEB_JS" && \
  grep -q 'selectionLayer.addEventListener("contextmenu", keepNativeSelectionEvent' "$WEB_JS" && \
  grep -q 'clonedRow.textContent = lineText' "$WEB_JS" && \
  grep -q "accessibilityObserver.observe" "$WEB_JS" && \
  grep -q "agent-output-selection-layer" "$WEB_JS" && \
  grep -q "agent-output-selection-layer" "$DIR/web/app.css" && \
  grep -q "use-native-touch-selection" "$DIR/web/app.css" && \
  grep -q "selectAgentOutputRange" "$WEB_JS" && \
  grep -q 'source: "agent-selection"' "$WEB_JS" && \
  grep -q "selectionLocksSnapshot" "$WEB_JS" && \
  grep -q 'surface.addEventListener("contextmenu"' "$WEB_JS" && \
  grep -q "enableWebTerminalTouchScrolling" "$WEB_JS" && \
  grep -q "attachTerminalCopyShortcut" "$WEB_JS" && \
  grep -q 'attachTerminalCopyShortcut(terminal, "Agent 输出选区", true)' "$WEB_JS" && \
  grep -q "terminalBufferText" "$WEB_JS" && \
  grep -q 'new WheelEvent("wheel"' "$WEB_JS" && \
  grep -q 'terminal.modes.mouseTrackingMode === "none"' "$WEB_JS" && \
  grep -q 'addEventListener("touchmove"' "$WEB_JS" && \
  grep -q "select option" "$DIR/web/app.css" && grep -q "width: fit-content" "$DIR/web/app.css" && \
  grep -q "ssh_profile_save" "$WEB_JS" && grep -q "Herdr FiraCode Nerd" "$WEB_JS" && \
  grep -q "mobile-keybar" "$WEB" && grep -q "tmux-keybar" "$WEB" && \
  grep -q 'id="terminal-copy-dialog"' "$WEB" && \
  grep -q 'id="copy-web-terminal-button"' "$WEB" && \
  grep -q 'id="terminal-copy-button"' "$WEB" && \
  grep -q 'type: "terminal_capture"' "$WEB_JS" && \
  grep -q 'elif msg_type == "terminal_capture"' "$DIR/relay/herdr_relay.py" && \
  grep -q 'data-terminal-key="left"' "$WEB" && grep -q 'data-terminal-key="right"' "$WEB" && \
  grep -q 'data-terminal-key="page-up"' "$WEB" && grep -q 'data-terminal-key="page-down"' "$WEB" && \
  grep -q "terminal-ctrl-button" "$WEB" && grep -q "TERMINAL_CTRL_KEY_SEQUENCES" "$WEB_JS" && \
  grep -q 'id="terminal-tmux-prefix-button"' "$WEB" && grep -q "tmux · Ctrl+B" "$WEB" && \
  grep -q "TMUX_ACTION_SEQUENCES" "$WEB_JS" && \
  grep -q "sendTerminalShortcutText" "$WEB_JS" && grep -q "Pane缩放" "$WEB" && \
  grep -q "preserveTerminalFocusForKeybar" "$WEB_JS" && \
  grep -q 'terminalKeybar.addEventListener("pointerdown"' "$WEB_JS" && \
  ! grep -A 2 'if (text) sendTerminalText(text);' "$WEB_JS" | grep -q 'terminalInstance.*focus' && \
  grep -q "TERMINAL_FOCUS_SEQUENCES" "$WEB_JS" && grep -q 'TMUX_PREFIX_SEQUENCE = "\\x02"' "$WEB_JS" && \
  grep -q "touch-action: pan-x pinch-zoom" "$DIR/web/app.css" && \
  grep -q "touch-action: manipulation" "$DIR/web/app.css" && \
  grep -q -- '-webkit-user-select: text' "$DIR/web/app.css" && \
  grep -q "interactive-widget=resizes-content" "$WEB" && \
  grep -q "navigator.virtualKeyboard.overlaysContent = false" "$WEB_JS" && \
  grep -q 'viewport?.addEventListener("resize"' "$WEB_JS" && \
  grep -q 'function setShellKeyboardOpen' "$WEB_JS" && \
  grep -q 'classList.toggle("shell-keyboard-open"' "$WEB_JS" && \
  grep -q -- '--app-viewport-height' "$DIR/web/app.css" && \
  grep -q 'body.shell-keyboard-open .interactive-terminal-card' "$DIR/web/app.css" && \
  grep -q 'padding: 0 10px' "$DIR/web/app.css" && \
  [ -f "$DIR/web/vendor/xterm/xterm.js" ] && \
  [ -f "$DIR/web/vendor/xterm/xterm.css" ] && \
  [ -f "$DIR/web/vendor/xterm/addon-fit.js" ] && \
  [ -f "$DIR/web/vendor/fonts/firacode-nerd-mono-v3.3.0.woff2" ] && \
  [ -f "$DIR/web/vendor/preview/marked-18.0.10.js" ] && \
  [ -f "$DIR/web/vendor/preview/dompurify-3.4.14.min.js" ] && \
  [ -f "$DIR/web/vendor/preview/highlight-11.12.0.min.js" ] && \
  [ -f "$DIR/web/vendor/preview/LICENSE-marked.txt" ] && \
  [ -f "$DIR/web/vendor/preview/LICENSE-dompurify-apache.txt" ] && \
  [ -f "$DIR/web/vendor/preview/LICENSE-highlight.txt" ] && \
  grep -q "firacode-nerd-mono-v3.3.0.woff2" "$DIR/web/app.css" && \
  grep -q "firacode-nerd-mono-v3.3.0.woff2" "$DIR/relay/herdr_relay.py" && \
  node --check "$WEB_JS"
assert_eq "$?" "0" "has responsive Agent and Remote Shell controls"

echo "10. web app no hardcoded secrets"
! grep -q "c4a2385e" "$WEB" && ! grep -q "graffold" "$WEB" && \
  ! grep -q "esm.sh" "$WEB" && ! grep -q 'localStorage.setItem("herdr_relay_token"' "$WEB_JS"
assert_eq "$?" "0" "no secrets, remote scripts, or persisted relay token"

# --- macOS app ---
echo ""
echo "=== macOS app ==="
echo "11. Swift sources parse"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -parse "$DIR/herdi-mac/Sources/"*.swift 2>/dev/null && \
  swiftc -parse "$DIR/herdi-ios/Sources/"*.swift "$DIR/herdi-ios/Sources/Models/"*.swift "$DIR/herdi-ios/Sources/Services/"*.swift "$DIR/herdi-ios/Sources/Views/"*.swift 2>/dev/null
  assert_eq "$?" "0" "Swift clients parse"
else
  PASS=$((PASS+1)); echo "  skip: swiftc not available"
fi

echo "12. build.sh and dmg.sh present"
[ -x "$DIR/herdi-mac/build.sh" ] && [ -f "$DIR/herdi-mac/dmg.sh" ]
assert_eq "$?" "0" "build scripts present"

echo "13. updater points to correct repo"
grep -q "dcolinmorgan/herdr-remote" "$DIR/herdi-mac/Sources/Updater.swift"
assert_eq "$?" "0" "updater repo correct"

# --- Demo worker ---
echo ""
echo "=== Demo worker ==="
echo "14. demo worker syntax"
if [ -f "$DIR/demo-worker/src/index.js" ]; then
  node --check "$DIR/demo-worker/src/index.js" 2>/dev/null
  assert_eq "$?" "0" "demo worker parses"
else
  PASS=$((PASS+1)); echo "  skip: not present"
fi

# --- Integration ---
echo ""
echo "=== Integration ==="
echo "15. README links to herdr-demo.pages.dev"
grep -q "herdr-demo.pages.dev" "$DIR/README.md"
assert_eq "$?" "0" "demo URL correct"

echo "16. README links to herdr-push"
grep -q "dcolinmorgan/herdr-push" "$DIR/README.md"
assert_eq "$?" "0" "plugin link present"

echo "17. installer service behavior"
"$DIR/tests/install-service.sh"
assert_eq "$?" "0" "installer handles Telegram service lifecycle"

echo "18. LICENSE is AGPL"
grep -q "GNU AFFERO GENERAL PUBLIC LICENSE" "$DIR/LICENSE"
assert_eq "$?" "0" "AGPL license"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
