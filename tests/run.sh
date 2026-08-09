#!/bin/sh
# tests/run.sh — tests for herdr-remote
PASS=0; FAIL=0
DIR="$(cd "$(dirname "$0")/.." && pwd)"

assert_eq() {
  if [ "$1" = "$2" ]; then PASS=$((PASS+1)); echo "  pass: $3"
  else FAIL=$((FAIL+1)); echo "  FAIL: $3 (expected '$2', got '$1')"; fi
}

echo "herdr-remote tests"
echo ""

# --- Relay ---
echo "=== Relay ==="
echo "1. relay syntax"
python3 -c "import ast; ast.parse(open('$DIR/relay/herdr_relay.py').read())" 2>/dev/null
assert_eq "$?" "0" "herdr_relay.py parses"

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
python3 -c "import ast; ast.parse(open('$DIR/relay/herdr_telegram.py').read())" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram.py parses"

echo "5. telegram demo bot syntax"
python3 -c "import ast; ast.parse(open('$DIR/relay/herdr_telegram_demo.py').read())" 2>/dev/null
assert_eq "$?" "0" "herdr_telegram_demo.py parses"

echo "6. telegram bot has all commands"
for cmd in cmd_start cmd_agents cmd_status cmd_read cmd_send cmd_reply cmd_trust cmd_interrupt cmd_digest cmd_help cmd_browse cmd_cd cmd_cwd cmd_codex; do
  grep -q "async def $cmd" "$DIR/relay/herdr_telegram.py" || { FAIL=$((FAIL+1)); echo "  FAIL: missing $cmd"; continue; }
done
PASS=$((PASS+1)); echo "  pass: all 14 commands present"

echo "7. telegram bot env vars documented"
grep -q "HERDR_TG_TOKEN" "$DIR/relay/herdr_telegram.py" && grep -q "HERDR_TG_CHAT_ID" "$DIR/relay/herdr_telegram.py"
assert_eq "$?" "0" "env vars referenced"

echo "8. telegram dashboard behavior"
uv run "$DIR/tests/test_telegram.py"
assert_eq "$?" "0" "telegram dashboard tests"

echo "9. relay security behavior"
uv run "$DIR/tests/test_relay_security.py"
assert_eq "$?" "0" "relay security tests"

echo "10. terminal session behavior"
uv run "$DIR/tests/test_terminal_sessions.py"
assert_eq "$?" "0" "PTY, SSH profile, and terminal session tests"

echo "11. relay agent state behavior"
python3 "$DIR/tests/test_agent_state.py"
assert_eq "$?" "0" "agent state tests"

# --- TUI ---
echo ""
echo "=== TUI ==="
echo "12. TUI syntax"
python3 -c "import ast; ast.parse(open('$DIR/relay/herdr_tui.py').read())" 2>/dev/null
assert_eq "$?" "0" "herdr_tui.py parses"

# --- Web app ---
echo ""
echo "=== Web app ==="
echo "13. web app key elements"
WEB="$DIR/web/index.html"
WEB_JS="$DIR/web/app.js"
[ -f "$DIR/web/app.css" ] && [ -f "$DIR/web/manifest.webmanifest" ] && \
  grep -q "WebSocket" "$WEB_JS" && grep -q "agent_prompt" "$WEB_JS" && \
  grep -q 'message.delivery === "queued"' "$WEB_JS" && \
  grep -q "list_directories" "$WEB_JS" && grep -q "start_agent" "$WEB_JS" && \
  grep -q "C-c" "$WEB_JS" && grep -q "terminal_open" "$WEB_JS" && \
  grep -q "ssh_profile_save" "$WEB_JS" && grep -q "Herdr FiraCode Nerd" "$WEB_JS" && \
  grep -q "mobile-keybar" "$WEB" && \
  [ -f "$DIR/web/vendor/xterm/xterm.js" ] && \
  [ -f "$DIR/web/vendor/xterm/xterm.css" ] && \
  [ -f "$DIR/web/vendor/xterm/addon-fit.js" ] && \
  [ -f "$DIR/web/vendor/fonts/firacode-nerd-mono-v3.3.0.woff2" ] && \
  grep -q "firacode-nerd-mono-v3.3.0.woff2" "$DIR/web/app.css" && \
  grep -q "firacode-nerd-mono-v3.3.0.woff2" "$DIR/relay/herdr_relay.py" && \
  node --check "$WEB_JS"
assert_eq "$?" "0" "has responsive Agent and Remote Shell controls"

echo "14. web app no hardcoded secrets"
! grep -q "c4a2385e" "$WEB" && ! grep -q "graffold" "$WEB" && \
  ! grep -q "esm.sh" "$WEB" && ! grep -q 'localStorage.setItem("herdr_relay_token"' "$WEB_JS"
assert_eq "$?" "0" "no secrets, remote scripts, or persisted relay token"

# --- macOS app ---
echo ""
echo "=== macOS app ==="
echo "15. Swift sources parse"
if command -v swiftc >/dev/null 2>&1; then
  swiftc -parse "$DIR/herdi-mac/Sources/Agent.swift" "$DIR/herdi-mac/Sources/RelayConnection.swift" 2>/dev/null
  assert_eq "$?" "0" "core Swift parses"
else
  PASS=$((PASS+1)); echo "  skip: swiftc not available"
fi

echo "16. build.sh and dmg.sh present"
[ -x "$DIR/herdi-mac/build.sh" ] && [ -f "$DIR/herdi-mac/dmg.sh" ]
assert_eq "$?" "0" "build scripts present"

echo "17. updater points to correct repo"
grep -q "dcolinmorgan/herdr-remote" "$DIR/herdi-mac/Sources/Updater.swift"
assert_eq "$?" "0" "updater repo correct"

# --- Demo worker ---
echo ""
echo "=== Demo worker ==="
echo "18. demo worker syntax"
if [ -f "$DIR/demo-worker/src/index.js" ]; then
  node --check "$DIR/demo-worker/src/index.js" 2>/dev/null
  assert_eq "$?" "0" "demo worker parses"
else
  PASS=$((PASS+1)); echo "  skip: not present"
fi

# --- Integration ---
echo ""
echo "=== Integration ==="
echo "19. README links to herdr-demo.pages.dev"
grep -q "herdr-demo.pages.dev" "$DIR/README.md"
assert_eq "$?" "0" "demo URL correct"

echo "20. README links to herdr-push"
grep -q "dcolinmorgan/herdr-push" "$DIR/README.md"
assert_eq "$?" "0" "plugin link present"

echo "21. installer service behavior"
"$DIR/tests/install-service.sh"
assert_eq "$?" "0" "installer handles Telegram service lifecycle"

echo "22. LICENSE is AGPL"
grep -q "GNU AFFERO GENERAL PUBLIC LICENSE" "$DIR/LICENSE"
assert_eq "$?" "0" "AGPL license"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
