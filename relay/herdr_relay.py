#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0", "zeroconf>=0.80.0", "pywebpush>=2.0.0", "py-vapid>=1.9.0"]
# ///
"""herdr-remote relay — polls herdr, accepts push events (HTTP POST + WebSocket + UDP), broadcasts to clients."""
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from email.header import decode_header, make_header
from http.cookies import CookieError, SimpleCookie
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

try:
    from websockets.asyncio.server import serve
except ImportError:
    from websockets.server import serve

from agent_state import complete_agent_update_message

os.umask(0o077)

def _get_log_dir():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/herdr-remote")
    if os.path.isdir("/var/log") and os.access("/var/log", os.W_OK):
        return "/var/log/herdr-remote"
    return os.path.expanduser("~/.local/state/herdr-remote/log")

LOG_DIR = os.environ.get("HERDR_LOG_DIR", _get_log_dir())
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "relay.log")
AUDIT_FILE = os.path.join(LOG_DIR, "audit.log")

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_file_handler.setFormatter(_formatter)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

log = logging.getLogger("herdr-relay")
log.setLevel(logging.INFO)
log.addHandler(_file_handler)
log.addHandler(_console_handler)
logging.getLogger("websockets").setLevel(logging.WARNING)

def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


HERDR = os.environ.get("HERDR_BIN") or shutil.which("herdr") or "/opt/homebrew/bin/herdr"
RELAY_HOST = os.environ.get("HERDR_RELAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
WS_PORT = int(os.environ.get("HERDR_RELAY_PORT", "8375"))
POLL_INTERVAL = 2
AUTH_TOKEN = os.environ.get("HERDR_RELAY_TOKEN", "")
ALLOW_INSECURE_NO_AUTH = env_flag("HERDR_ALLOW_INSECURE_NO_AUTH")
ALLOW_REMOTE_BIND = env_flag("HERDR_ALLOW_REMOTE_BIND")
MDNS_ENABLED = env_flag("HERDR_MDNS_ENABLED")
TAILSCALE_WEB_ENABLED = env_flag("HERDR_TAILSCALE_WEB")
TAILSCALE_ALLOWED_USERS = {
    value.strip().casefold()
    for value in os.environ.get("HERDR_TAILSCALE_ALLOWED_USERS", "").split(",")
    if value.strip()
}
WEB_SESSION_COOKIE = "herdr_session"
WEB_SESSION_TTL_SECONDS = 8 * 60 * 60

# VAPID Web Push
VAPID_PUBLIC_KEY = os.environ.get("HERDR_VAPID_PUBLIC", "")
VAPID_PRIVATE_KEY = os.environ.get("HERDR_VAPID_PRIVATE", "")
VAPID_SUBJECT = os.environ.get("HERDR_VAPID_SUBJECT", "mailto:herdr@localhost")
push_subscriptions = []  # list of PushSubscription dicts
PUSH_SUBS_FILE = os.path.join(LOG_DIR, "push_subs.json")

# Remote hosts: comma-separated SSH targets
REMOTES = [r.strip() for r in os.environ.get("HERDR_REMOTES", "").split(",") if r.strip()]


def configured_workspace_roots() -> list[Path]:
    configured = os.environ.get("HERDR_WORKSPACE_ROOTS", os.path.expanduser("~/Workspace"))
    roots = []
    for value in configured.split(os.pathsep):
        value = value.strip()
        if not value:
            continue
        try:
            root = Path(os.path.expanduser(value)).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if root.is_dir() and root not in roots:
            roots.append(root)
    return roots


WORKSPACE_ROOTS = configured_workspace_roots()
WORKSPACE_ENTRY_LIMIT = 200
AGENT_PROMPT_WAIT_TIMEOUT_MS = 8_000
AGENT_PROMPT_PROCESS_TIMEOUT_SECONDS = 12
AGENT_PROMPT_CONFIRM_ATTEMPTS = 15
AGENT_PROMPT_CONFIRM_INTERVAL_SECONDS = 0.2

TOOL_OPTIONS = ["yes, single permission", "trust, always allow", "no (tab to edit)"]
SUBAGENT_OPTIONS = ["approve all pending", "configure individually", "exit (cancel subagents)"]
CHROME_RE = re.compile(
    r"^[\s─━═_—│|◔◑◕●\s]+$"
    r"|Kiro\s[·•]"
    r"|esc to cancel"
    r"|type to queue"
    r"|^\s*[◔◑◕●]\s+(Shell|Bash)"
)

clients = set()
last_statuses = {}
event_queue = asyncio.Queue()
pane_remote_map = {}
known_panes = set()
agent_cache = {}
agent_start_in_progress = False
client_auth = {}

SAFE_RESPONSES = {"y", "n", "a", "yes", "no", "trust", "yes, single permission", "trust, always allow", "no (tab to edit)", "approve all pending", "configure individually", "exit (cancel subagents)"}
SAFE_KEYS = {"y", "n", "a", "Enter", "Tab", "Escape", "C-c", "Up", "Down", "Left", "Right", "BSpace"} | {
    str(number) for number in range(10)
}

# --- Audit logging ---
_audit_handler = RotatingFileHandler(AUDIT_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
_audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
audit_log = logging.getLogger("herdr-audit")
audit_log.setLevel(logging.INFO)
audit_log.addHandler(_audit_handler)
audit_log.propagate = False


def audit(action: str, ip: str, device: str, pane_id: str, detail: str = ""):
    """Append a write action to the audit log as structured JSONL."""
    import datetime
    entry = {
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "action": action,
        "paneId": pane_id,
        "ip": ip,
        "device": device,
    }
    if detail:
        entry["detail"] = detail[:120]  # truncate like collie
    audit_log.info(json.dumps(entry, separators=(",", ":")))


def sensitive_detail(text: str) -> str:
    """Describe user text without writing its contents to logs."""
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"chars={len(text)} sha256={digest}"


# --- Web Push helpers ---
def _load_push_subs():
    global push_subscriptions
    if os.path.isfile(PUSH_SUBS_FILE):
        try:
            with open(PUSH_SUBS_FILE) as f:
                push_subscriptions = json.load(f)
        except Exception:
            push_subscriptions = []


def _save_push_subs():
    with open(PUSH_SUBS_FILE, "w") as f:
        json.dump(push_subscriptions, f)


async def send_web_push(title: str, body: str, url: str = "/", clear: bool = False):
    """Send push notification to all registered subscriptions.
    
    Uses collapse topic + TTL so offline devices get only the latest.
    If clear=True, sends a clear instruction instead of showing a notification.
    """
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("pywebpush not installed, skipping push")
        return
    if clear:
        payload = json.dumps({"type": "clear", "tag": "herdr-blocked"})
    else:
        payload = json.dumps({"title": title, "body": body, "url": url})
    headers = {"Topic": "herdr-herd", "TTL": "21600"}  # 6h TTL, collapse key
    dead = []
    for i, sub in enumerate(push_subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
                headers=headers,
            )
        except Exception as e:
            log.warning("Push failed for sub %d: %s", i, e)
            if "410" in str(e) or "404" in str(e):
                dead.append(i)
    if dead:
        for i in reversed(dead):
            push_subscriptions.pop(i)
        _save_push_subs()

_load_push_subs()


def run_herdr_result(*args, remote=None, timeout=15):
    if remote:
        # OpenSSH sends its trailing arguments through the remote login shell.
        # Quote the complete command so prompt text cannot become shell syntax.
        remote_command = shlex.join([HERDR, *args])
        cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", remote, remote_command]
    else:
        cmd = [HERDR, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def run_herdr(*args, remote=None):
    try:
        return run_herdr_result(*args, remote=remote).stdout.strip()
    except Exception:
        return ""


def path_within_workspace_roots(path: Path) -> bool:
    return any(path == root or root in path.parents for root in WORKSPACE_ROOTS)


def resolve_workspace_path(value: str) -> Path:
    if not WORKSPACE_ROOTS:
        raise ValueError("No workspace roots are configured")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("A workspace directory is required")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Workspace path contains control characters")

    candidate = Path(os.path.expanduser(value.strip()))
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOTS[0] / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise ValueError("Workspace directory does not exist") from e
    if not resolved.is_dir():
        raise ValueError("Workspace path is not a directory")
    if not path_within_workspace_roots(resolved):
        raise ValueError("Workspace path is outside the configured roots")
    return resolved


def display_workspace_path(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def git_root_for(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if not path_within_workspace_roots(candidate):
            break
        git_marker = candidate / ".git"
        if git_marker.is_dir() or git_marker.is_file():
            return candidate
    return None


def workspace_directory_listing(value: str | None = None) -> dict:
    if not WORKSPACE_ROOTS:
        raise ValueError("No workspace roots are configured")

    if not value:
        entries = [
            {
                "name": root.name or str(root),
                "path": str(root),
                "display_path": display_workspace_path(root),
                "is_repo": git_root_for(root) == root,
            }
            for root in WORKSPACE_ROOTS
        ]
        return {
            "path": "",
            "display_path": "Configured workspace roots",
            "parent": None,
            "entries": entries,
            "can_start_agent": False,
            "truncated": False,
        }

    directory = resolve_workspace_path(value)
    entries = []
    try:
        children = list(os.scandir(directory))
    except OSError as e:
        raise ValueError("Workspace directory cannot be read") from e
    for entry in children:
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
            child = Path(entry.path).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not path_within_workspace_roots(child):
            continue
        entries.append({
            "name": entry.name,
            "path": str(child),
            "display_path": display_workspace_path(child),
            "is_repo": git_root_for(child) == child,
        })

    entries.sort(key=lambda item: (not item["is_repo"], item["name"].lower()))
    truncated = len(entries) > WORKSPACE_ENTRY_LIMIT
    entries = entries[:WORKSPACE_ENTRY_LIMIT]

    parent = directory.parent if path_within_workspace_roots(directory.parent) else None
    git_root = git_root_for(directory)
    return {
        "path": str(directory),
        "display_path": display_workspace_path(directory),
        "parent": str(parent) if parent else None,
        "entries": entries,
        "can_start_agent": True,
        "git_root": str(git_root or ""),
        "truncated": truncated,
    }


def parse_herdr_result(result: subprocess.CompletedProcess) -> dict:
    if result.returncode != 0:
        raise RuntimeError("Herdr command failed")
    try:
        return json.loads(result.stdout).get("result", {})
    except (json.JSONDecodeError, AttributeError) as e:
        raise RuntimeError("Herdr returned an invalid response") from e


def herdr_json_response(result: subprocess.CompletedProcess) -> dict:
    """Decode a Herdr JSON response from stdout or stderr."""
    for stream in (result.stdout, result.stderr):
        if not isinstance(stream, str) or not stream.strip():
            continue
        candidates = [stream.strip(), *reversed(stream.splitlines())]
        for candidate in candidates:
            try:
                response = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(response, dict):
                return response
    return {}


def herdr_error_code(result: subprocess.CompletedProcess) -> str:
    error = herdr_json_response(result).get("error") or {}
    return str(error.get("code", "")) if isinstance(error, dict) else ""


def get_agent_info(pane_id: str, remote=None) -> dict:
    result = run_herdr_result("agent", "get", pane_id, remote=remote, timeout=5)
    if result.returncode != 0:
        raise RuntimeError("Could not inspect agent state")
    response = herdr_json_response(result)
    agent = (response.get("result") or {}).get("agent")
    if not isinstance(agent, dict):
        raise RuntimeError("Herdr returned an invalid agent state")
    return agent


def agent_state_advanced(before: dict, after: dict) -> bool:
    if after.get("agent_status") != before.get("agent_status"):
        return True
    before_seq = before.get("state_change_seq")
    after_seq = after.get("state_change_seq")
    return (
        isinstance(before_seq, int)
        and not isinstance(before_seq, bool)
        and isinstance(after_seq, int)
        and not isinstance(after_seq, bool)
        and after_seq > before_seq
    )


def submit_agent_prompt(pane_id: str, text: str, remote=None) -> bool:
    """Submit a prompt and verify that Herdr observed it starting."""
    result = run_herdr_result(
        "agent", "prompt", pane_id, text,
        "--wait",
        "--until", "working",
        "--until", "blocked",
        "--until", "done",
        "--timeout", str(AGENT_PROMPT_WAIT_TIMEOUT_MS),
        remote=remote,
        timeout=AGENT_PROMPT_PROCESS_TIMEOUT_SECONDS,
    )
    if result.returncode == 0:
        return True
    if herdr_error_code(result) != "agent_prompt_stalled":
        raise RuntimeError("Herdr rejected the agent prompt")

    stalled = get_agent_info(pane_id, remote=remote)
    status = str(stalled.get("agent_status", "unknown")).casefold()
    if status == "working":
        # The state changed just after Herdr's wait timed out. Do not press
        # Enter again or a second empty submission could be queued.
        return True
    if status != "idle" or stalled.get("interactive_ready") is not True:
        raise RuntimeError("Agent prompt did not start")

    entered = run_herdr_result(
        "agent", "send-keys", pane_id, "Enter",
        remote=remote,
        timeout=5,
    )
    if entered.returncode != 0:
        raise RuntimeError("Could not finish submitting the agent prompt")

    for attempt in range(AGENT_PROMPT_CONFIRM_ATTEMPTS):
        current = get_agent_info(pane_id, remote=remote)
        if agent_state_advanced(stalled, current):
            return True
        if attempt + 1 < AGENT_PROMPT_CONFIRM_ATTEMPTS:
            time.sleep(AGENT_PROMPT_CONFIRM_INTERVAL_SECONDS)
    raise RuntimeError("Agent prompt did not start after Enter")


def start_local_codex(cwd: str, prompt: str = "") -> dict:
    directory = resolve_workspace_path(cwd)
    if not isinstance(prompt, str) or len(prompt) > 1000:
        raise ValueError("Prompt must contain at most 1000 characters")

    label = directory.name[:80] or "codex"
    workspace_id = ""
    try:
        created = run_herdr_result(
            "workspace", "create",
            "--cwd", str(directory),
            "--label", label,
            "--no-focus",
            timeout=20,
        )
        created_result = parse_herdr_result(created)
        workspace = created_result.get("workspace") or {}
        root_pane = created_result.get("root_pane") or {}
        workspace_id = workspace.get("workspace_id", "")
        pane_id = root_pane.get("pane_id", "")
        if not workspace_id or not pane_id:
            raise RuntimeError("Herdr did not return the new workspace and pane IDs")

        started = run_herdr_result(
            "agent", "start", "codex",
            "--kind", "codex",
            "--pane", pane_id,
            "--timeout", "60000",
            timeout=75,
        )
        started_result = parse_herdr_result(started)
        agent = started_result.get("agent") or {}
        if agent.get("pane_id") != pane_id:
            raise RuntimeError("Herdr returned an unexpected agent pane")

        prompted = False
        prompt_warning = ""
        if prompt:
            try:
                submit_agent_prompt(pane_id, prompt)
            except Exception:
                prompt_warning = "Codex started, but the initial prompt was not accepted"
            else:
                prompted = True

        return {
            "pane_id": pane_id,
            "workspace_id": workspace_id,
            "cwd": str(directory),
            "display_path": display_workspace_path(directory),
            "project": directory.name,
            "agent": "codex",
            "status": agent.get("agent_status", "idle"),
            "prompted": prompted,
            "warning": prompt_warning,
        }
    except Exception:
        if workspace_id:
            try:
                run_herdr_result("workspace", "close", workspace_id, timeout=15)
            except Exception:
                pass
        raise


def get_agents_from_host(remote=None):
    raw = run_herdr("pane", "list", remote=remote)
    host_label = remote or "local"
    try:
        data = json.loads(raw)
        panes = data.get("result", {}).get("panes", [])
        return [
            {
                "pane_id": p["pane_id"],
                "agent": p.get("agent", ""),
                "label": p.get("label", ""),
                "status": p.get("agent_status", "unknown"),
                "cwd": p.get("cwd", ""),
                "project": os.path.basename(p.get("cwd", "")),
                "host": host_label,
                "remote": remote,
                "workspace_id": p.get("workspace_id", ""),
                "tab_id": p.get("tab_id", ""),
            }
            for p in panes if p.get("agent")
        ]
    except (json.JSONDecodeError, KeyError):
        return []


def get_all_agents():
    agents = get_agents_from_host(remote=None)
    for remote in REMOTES:
        agents.extend(get_agents_from_host(remote=remote))
    return agents


def read_pane(pane_id, remote=None):
    raw = run_herdr("pane", "read", pane_id, "--lines", "50", "--source", "recent", remote=remote)
    lines = [l for l in raw.splitlines() if l.strip() and not CHROME_RE.search(l)]
    return "\n".join(lines[-20:])


def detect_options(text):
    lower = text.lower()
    if "yes, single permission" in lower:
        return TOOL_OPTIONS
    if "approve all pending" in lower:
        return SUBAGENT_OPTIONS
    return None


async def broadcast(msg):
    data = json.dumps(msg)
    dead = set()
    for ws in list(clients):
        try:
            await ws.send(data)
        except (ConnectionClosedError, ConnectionClosedOK):
            dead.add(ws)
        except Exception:
            dead.add(ws)
    if dead:
        log.debug("Removed %d dead client(s)", len(dead))
    clients.difference_update(dead)


async def poll_loop():
    while True:
        try:
            await _poll_once()
        except Exception:
            log.exception("poll cycle failed; retrying")
        await asyncio.sleep(POLL_INTERVAL)


async def _poll_once():
        agents = get_all_agents()
        # Always broadcast (even empty list) so clients stay in sync
        for a in agents:
            pane_remote_map[a["pane_id"]] = a.get("remote")
            known_panes.add(a["pane_id"])
            agent_cache[a["pane_id"]] = a
        await broadcast({"type": "agents", "agents": agents})
        for a in agents:
            pid, status = a["pane_id"], a["status"]
            if status == "blocked" and last_statuses.get(pid) != "blocked":
                content = read_pane(pid, remote=a.get("remote"))
                options = detect_options(content)
                await broadcast({
                    "type": "blocked", "pane_id": pid,
                    "agent": a["agent"], "project": a["project"],
                    "host": a.get("host", "local"),
                    "prompt": content[:500],
                    "options": options or TOOL_OPTIONS
                })
                # Web Push notification
                await send_web_push(
                    title=f"🐑 {a['project']} blocked",
                    body=content[:120],
                    url=f"/?pane={pid}",
                )
            # Send clear push when agent unblocks
            if status != "blocked" and last_statuses.get(pid) == "blocked":
                await send_web_push("", "", clear=True)
            last_statuses[pid] = status
        # Clean up panes that are no longer reported
        current_pane_ids = {a["pane_id"] for a in agents}
        stale = known_panes - current_pane_ids
        if stale:
            known_panes.difference_update(stale)
            for pid in stale:
                pane_remote_map.pop(pid, None)
                last_statuses.pop(pid, None)
                agent_cache.pop(pid, None)


async def event_push():
    while True:
        event = await event_queue.get()
        pane_id = event.get("pane_id", "")
        update = None
        if pane_id and event.get("type") == "agent_event":
            update = complete_agent_update_message(
                event,
                current=agent_cache.get(pane_id),
                local_hostname=socket.gethostname(),
            )
            if update is None:
                continue
        agent_data = update["agent"] if update else event
        status = agent_data.get("status", "")
        host = agent_data.get("host", "local")

        if status == "blocked" and pane_id:
            remote = pane_remote_map.get(pane_id)
            if remote or host == "local":
                content = read_pane(pane_id, remote=remote)
            else:
                content = event.get("prompt", "Agent is blocked")
            options = detect_options(content)
            await broadcast({
                "type": "blocked", "pane_id": pane_id,
                "agent": agent_data.get("agent", ""),
                "project": agent_data.get("project", ""),
                "host": host,
                "prompt": content[:500],
                "options": options or TOOL_OPTIONS
            })

        if update:
            known_panes.add(pane_id)
            pane_remote_map.setdefault(pane_id, None)
            agent_cache[pane_id] = {**agent_cache.get(pane_id, {}), **update["agent"]}
            await broadcast(update)


def request_header(request, name: str) -> str:
    headers = getattr(request, "headers", {})
    try:
        value = headers.get(name, "")
    except (AttributeError, LookupError, TypeError, ValueError):
        value = ""
    if value:
        return str(value)
    try:
        for key, raw_value in headers.raw_items():
            if key.casefold() == name.casefold():
                return str(raw_value)
    except (AttributeError, TypeError):
        pass
    return ""


def request_query(request) -> dict[str, list[str]]:
    path = getattr(request, "path", "") or ""
    query = path.split("?", 1)[1] if "?" in path else ""
    return parse_qs(query, keep_blank_values=True)


def clean_identity_header(value: str) -> str:
    if not value:
        return ""
    try:
        value = str(make_header(decode_header(value)))
    except (LookupError, UnicodeError, ValueError):
        pass
    return "".join(character for character in value.strip() if 31 < ord(character) != 127)[:256]


def connection_is_loopback(connection) -> bool:
    remote = getattr(connection, "remote_address", None)
    if not remote:
        return False
    host = remote[0] if isinstance(remote, (tuple, list)) else str(remote)
    return is_loopback_host(str(host))


def create_web_session(now: int | None = None) -> str:
    if not AUTH_TOKEN:
        return ""
    expires = int(now if now is not None else time.time()) + WEB_SESSION_TTL_SECONDS
    payload = f"v1:{expires}"
    signature = hmac.new(AUTH_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def valid_web_session(value: str, now: int | None = None) -> bool:
    if not AUTH_TOKEN or not value:
        return False
    try:
        version, expires_text, signature = value.split(":", 2)
        expires = int(expires_text)
    except (TypeError, ValueError):
        return False
    if version != "v1" or expires < int(now if now is not None else time.time()):
        return False
    expected = hmac.new(
        AUTH_TOKEN.encode(), f"{version}:{expires}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def request_cookie(request, name: str) -> str:
    raw_cookie = request_header(request, "Cookie")
    if not raw_cookie:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw_cookie)
    except CookieError:
        return ""
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def authenticate_request(connection, request) -> dict:
    authorization = request_header(request, "Authorization")
    if AUTH_TOKEN and authorization[:7].casefold() == "bearer ":
        supplied = authorization[7:]
        if hmac.compare_digest(supplied, AUTH_TOKEN):
            return {"ok": True, "mode": "token", "login": "", "name": ""}

    query_token = request_query(request).get("token", [""])[0]
    if AUTH_TOKEN and query_token and hmac.compare_digest(query_token, AUTH_TOKEN):
        return {
            "ok": True,
            "mode": "token-query",
            "login": "",
            "name": "",
            "set_cookie": True,
        }

    if valid_web_session(request_cookie(request, WEB_SESSION_COOKIE)):
        return {"ok": True, "mode": "web-session", "login": "", "name": ""}

    tailscale_login = clean_identity_header(request_header(request, "Tailscale-User-Login"))
    if TAILSCALE_WEB_ENABLED and tailscale_login:
        if not connection_is_loopback(connection):
            return {
                "ok": False,
                "status": 401,
                "reason": "Unauthorized",
                "message": "Untrusted Tailscale identity proxy",
            }
        if "*" not in TAILSCALE_ALLOWED_USERS and tailscale_login.casefold() not in TAILSCALE_ALLOWED_USERS:
            return {
                "ok": False,
                "status": 403,
                "reason": "Forbidden",
                "message": "This Tailscale user is not allowed",
            }
        return {
            "ok": True,
            "mode": "tailscale",
            "login": tailscale_login,
            "name": clean_identity_header(request_header(request, "Tailscale-User-Name")),
        }

    if ALLOW_INSECURE_NO_AUTH:
        return {"ok": True, "mode": "development", "login": "", "name": ""}

    return {
        "ok": False,
        "status": 401,
        "reason": "Unauthorized",
        "message": "Authentication required",
    }


def websocket_origin_allowed(request) -> bool:
    origin = request_header(request, "Origin")
    if not origin:
        return True
    if origin == "null":
        return False
    host_header = request_header(request, "Host")
    if not host_header:
        return False
    try:
        parsed_origin = urlsplit(origin)
        parsed_host = urlsplit(f"//{host_header}")
        origin_host = (parsed_origin.hostname or "").casefold().rstrip(".")
        request_host = (parsed_host.hostname or "").casefold().rstrip(".")
        if not origin_host or origin_host != request_host:
            return False
        origin_port = parsed_origin.port or (443 if parsed_origin.scheme == "https" else 80)
        request_port = parsed_host.port or (443 if parsed_origin.scheme == "https" else 80)
        if origin_port != request_port:
            return False
    except ValueError:
        return False
    if parsed_origin.scheme not in {"http", "https"}:
        return False
    return parsed_origin.scheme == "https" or is_loopback_host(origin_host)


def web_session_cookie(request) -> str:
    value = create_web_session()
    host_header = request_header(request, "Host")
    try:
        host = urlsplit(f"//{host_header}").hostname or ""
    except ValueError:
        host = ""
    secure = "" if is_loopback_host(host) else "; Secure"
    return (
        f"{WEB_SESSION_COOKIE}={value}; Path=/; Max-Age={WEB_SESSION_TTL_SECONDS}; "
        f"HttpOnly; SameSite=Strict{secure}"
    )


def http_headers(content_type: str, cache_control: str = "no-cache", extra: list | None = None):
    from websockets.datastructures import Headers

    values = [
        ("Content-Type", content_type),
        ("Cache-Control", cache_control),
        ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' ws: wss:; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
        ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
    ]
    if extra:
        values.extend(extra)
    return Headers(values)


async def process_request(connection, request):
    """Authenticate HTTP/WebSocket traffic and serve the browser client."""
    from websockets.http11 import Response

    auth = authenticate_request(connection, request)
    if not auth["ok"]:
        headers = http_headers("text/plain; charset=utf-8", "no-store")
        return Response(
            auth["status"], auth["reason"], headers,
            f"{auth['message']}\n".encode(),
        )

    upgrade = request_header(request, "Upgrade").casefold()
    if upgrade == "websocket":
        if not websocket_origin_allowed(request):
            headers = http_headers("text/plain; charset=utf-8", "no-store")
            return Response(403, "Forbidden", headers, b"Cross-origin WebSocket rejected\n")
        client_auth[id(connection)] = auth
        return None

    # EVENT PUSH MUST BE HANDLED BEFORE STATIC ROUTES. A pushed event may
    # arrive on `/` as `?d=<urlencoded json>`.
    params = request_query(request)
    if "d" in params:
        payload = params["d"][0]
        try:
            event = json.loads(payload)
            if not isinstance(event, dict):
                raise ValueError("event payload must be an object")
            event_queue.put_nowait(event)
            log.debug("push: received event type=%s", event.get("type", "unknown"))
        except (asyncio.QueueFull, json.JSONDecodeError, TypeError, ValueError) as e:
            log.warning("push: unparseable event payload (%d bytes): %s", len(payload), e)
        return Response(200, "OK", http_headers("text/plain; charset=utf-8", "no-store"), b"ok\n")

    path = (getattr(request, "path", "") or "/").split("?", 1)[0]
    web_dir = Path(__file__).resolve().parent.parent / "web"
    static_files = {
        "/": ("index.html", "text/html; charset=utf-8", "no-cache"),
        "/index.html": ("index.html", "text/html; charset=utf-8", "no-cache"),
        "/app.css": ("app.css", "text/css; charset=utf-8", "no-cache"),
        "/app.js": ("app.js", "application/javascript; charset=utf-8", "no-cache"),
        "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json", "no-cache"),
        "/sw.js": ("sw.js", "application/javascript; charset=utf-8", "no-cache"),
        "/logo.svg": ("logo.svg", "image/svg+xml", "public, max-age=86400"),
    }
    if path in static_files:
        filename, content_type, cache_control = static_files[path]
        asset_path = web_dir / filename
        if asset_path.is_file():
            extra_headers = []
            if path == "/sw.js":
                extra_headers.append(("Service-Worker-Allowed", "/"))
            if auth.get("set_cookie"):
                extra_headers.append(("Set-Cookie", web_session_cookie(request)))
            return Response(
                200, "OK", http_headers(content_type, cache_control, extra_headers),
                asset_path.read_bytes(),
            )

    if path == "/api/vapid-public-key":
        body = json.dumps({"publicKey": VAPID_PUBLIC_KEY}).encode()
        return Response(200, "OK", http_headers("application/json", "no-store"), body)

    return Response(
        404, "Not Found", http_headers("text/plain; charset=utf-8", "no-store"),
        b"not found\n",
    )


async def handle_client(ws):
    global agent_start_in_progress
    remote_addr = ws.remote_address
    ip = remote_addr[0] if remote_addr else "unknown"
    ua = ws.request.headers.get("User-Agent", "unknown") if ws.request else "unknown"
    origin = ws.request.headers.get("Origin", "") if ws.request else ""

    device = "unknown"
    ua_lower = ua.lower()
    if "iphone" in ua_lower or "ipad" in ua_lower:
        device = "iOS"
    elif "android" in ua_lower:
        device = "Android"
    elif "macintosh" in ua_lower or "mac os" in ua_lower:
        device = "macOS"
    elif "windows" in ua_lower:
        device = "Windows"
    elif "linux" in ua_lower:
        device = "Linux"
    elif "telegram" in ua_lower or "bot" in ua_lower:
        device = "bot"
    elif "python" in ua_lower:
        device = "script"

    log.info("Client connected: ip=%s device=%s origin=%s", ip, device, origin or "-")
    clients.add(ws)
    auth = client_auth.get(id(ws), {})
    connected_at = time.monotonic()
    try:
        await ws.send(json.dumps({
            "type": "session",
            "auth": auth.get("mode", "token"),
            "user": {
                "login": auth.get("login", ""),
                "name": auth.get("name", ""),
            },
        }))
        await ws.send(json.dumps({"type": "agents", "agents": list(agent_cache.values())}))
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type == "respond":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if text.strip().lower() not in SAFE_RESPONSES:
                    await ws.send(json.dumps({"type": "error", "message": "response not in allowlist"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Response from %s (%s): pane=%s text=%r", ip, device, pane_id, text)
                audit("respond", ip, device, pane_id, f"text={text!r}")
                run_herdr("pane", "send-text", pane_id, text + "\n", remote=remote)
            elif msg_type == "agent_event":
                event_queue.put_nowait(msg)
            elif msg_type == "list_directories":
                try:
                    listing = workspace_directory_listing(msg.get("path"))
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                await ws.send(json.dumps({"type": "directory_listing", **listing}))
            elif msg_type == "start_agent":
                if msg.get("kind", "codex") != "codex":
                    await ws.send(json.dumps({"type": "error", "message": "Only Codex can be started remotely"}))
                    continue
                if agent_start_in_progress:
                    await ws.send(json.dumps({"type": "error", "message": "Another agent is currently starting"}))
                    continue
                cwd = msg.get("cwd", "")
                prompt = msg.get("prompt", "")
                if not isinstance(prompt, str) or len(prompt) > 1000:
                    await ws.send(json.dumps({"type": "error", "message": "Prompt must contain at most 1000 characters"}))
                    continue
                try:
                    directory = resolve_workspace_path(cwd)
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                log.info(
                    "Start Codex from %s (%s): cwd=%s prompt_chars=%d",
                    ip, device, display_workspace_path(directory), len(prompt),
                )
                audit(
                    "start_agent", ip, device, "",
                    f"kind=codex cwd={display_workspace_path(directory)} {sensitive_detail(prompt)}",
                )
                agent_start_in_progress = True
                try:
                    started_agent = await asyncio.to_thread(start_local_codex, str(directory), prompt)
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                except Exception as e:
                    log.warning("Codex start failed (%s)", type(e).__name__)
                    await ws.send(json.dumps({"type": "error", "message": "Could not start Codex"}))
                    continue
                finally:
                    agent_start_in_progress = False

                pane_id = started_agent["pane_id"]
                agent_update = {
                    "pane_id": pane_id,
                    "agent": "codex",
                    "status": started_agent.get("status", "idle"),
                    "cwd": started_agent["cwd"],
                    "project": started_agent["project"],
                    "host": "local",
                    "workspace_id": started_agent["workspace_id"],
                }
                known_panes.add(pane_id)
                pane_remote_map[pane_id] = None
                agent_cache[pane_id] = agent_update
                await ws.send(json.dumps({"type": "agent_started", "ok": True, "agent": started_agent}))
                await broadcast({"type": "agent_update", "agent": agent_update})
            elif msg_type == "read_pane":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                try:
                    lines = max(1, min(int(msg.get("lines", 30)), 200))
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "message": "lines must be an integer"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                content = run_herdr("pane", "read", pane_id, "--lines", str(lines), "--source", "recent", remote=remote)
                await ws.send(json.dumps({"type": "pane_content", "pane_id": pane_id, "content": content}))
            elif msg_type == "send_keys":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                keys = msg.get("keys", [])
                if not isinstance(keys, list) or not keys or len(keys) > 16 or not all(k in SAFE_KEYS for k in keys):
                    await ws.send(json.dumps({"type": "error", "message": "keys contain disallowed values"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Keys from %s (%s): pane=%s keys=%s", ip, device, pane_id, keys)
                audit("send_keys", ip, device, pane_id, f"keys={keys}")
                try:
                    result = run_herdr_result("pane", "send-keys", pane_id, *keys, remote=remote)
                except Exception as e:
                    log.warning("send_keys command failed for pane %s: %s", pane_id, e)
                    await ws.send(json.dumps({"type": "error", "message": "send_keys command failed"}))
                    continue
                if result.returncode != 0:
                    log.warning("send_keys command failed for pane %s with exit %s", pane_id, result.returncode)
                    await ws.send(json.dumps({"type": "error", "message": "send_keys command failed"}))
                    continue
                await ws.send(json.dumps({"type": "command_result", "command": "send_keys", "ok": True}))
            elif msg_type == "agent_prompt":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not isinstance(text, str) or not text or len(text) > 1000:
                    await ws.send(json.dumps({"type": "error", "message": "text empty or too long"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Agent prompt from %s (%s): pane=%s chars=%d", ip, device, pane_id, len(text))
                audit("agent_prompt", ip, device, pane_id, sensitive_detail(text))
                try:
                    await asyncio.to_thread(submit_agent_prompt, pane_id, text, remote)
                except Exception as e:
                    # Exception strings from subprocess may embed the full prompt command.
                    log.warning("agent_prompt command failed for pane %s (%s)", pane_id, type(e).__name__)
                    await ws.send(json.dumps({"type": "error", "message": "agent_prompt command failed"}))
                    continue
                await ws.send(json.dumps({"type": "command_result", "command": "agent_prompt", "ok": True}))
            elif msg_type == "send_text":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not isinstance(text, str) or not text or len(text) > 1000:
                    await ws.send(json.dumps({"type": "error", "message": "text empty or too long"}))
                    continue
                remote = pane_remote_map.get(pane_id)
                log.info("Text from %s (%s): pane=%s chars=%d", ip, device, pane_id, len(text))
                audit("send_text", ip, device, pane_id, sensitive_detail(text))
                run_herdr("pane", "send-text", pane_id, text, remote=remote)
            elif msg_type == "create_tab":
                workspace_id = msg.get("workspace_id", "")
                if workspace_id:
                    log.info("Create tab from %s (%s): workspace=%s", ip, device, workspace_id)
                    audit("create_tab", ip, device, "", f"workspace={workspace_id}")
                    run_herdr("tab", "create", "--workspace", workspace_id, "--focus")
                    await ws.send(json.dumps({"type": "tab_created", "ok": True}))
                else:
                    await ws.send(json.dumps({"type": "error", "message": "workspace_id required"}))
            elif msg_type == "push_subscribe":
                sub = msg.get("subscription")
                if sub and sub not in push_subscriptions:
                    push_subscriptions.append(sub)
                    _save_push_subs()
                    log.info("Push subscription added from %s (%s)", ip, device)
                await ws.send(json.dumps({"type": "push_subscribed", "ok": True}))
            elif msg_type == "push_unsubscribe":
                sub = msg.get("subscription")
                if sub and sub in push_subscriptions:
                    push_subscriptions.remove(sub)
                    _save_push_subs()
                await ws.send(json.dumps({"type": "push_unsubscribed", "ok": True}))
    except (ConnectionClosedError, ConnectionClosedOK):
        pass
    finally:
        duration = int(time.monotonic() - connected_at)
        log.info("Client disconnected: ip=%s device=%s duration=%ds", ip, device, duration)
        clients.discard(ws)
        client_auth.pop(id(ws), None)


class UDPPlugin(asyncio.DatagramProtocol):
    def datagram_received(self, data, addr):
        try:
            event_queue.put_nowait(json.loads(data.decode()))
        except Exception:
            pass


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_runtime_config():
    if TAILSCALE_WEB_ENABLED and not TAILSCALE_ALLOWED_USERS:
        raise RuntimeError(
            "HERDR_TAILSCALE_ALLOWED_USERS is required when HERDR_TAILSCALE_WEB=1. "
            "Use '*' only if every authenticated tailnet user should have control."
        )
    if TAILSCALE_WEB_ENABLED and not is_loopback_host(RELAY_HOST):
        raise RuntimeError("Tailscale web authentication requires HERDR_RELAY_HOST to be loopback.")
    if not AUTH_TOKEN and not TAILSCALE_WEB_ENABLED and not ALLOW_INSECURE_NO_AUTH:
        raise RuntimeError(
            "HERDR_RELAY_TOKEN is required unless Tailscale web authentication is enabled. "
            "Set HERDR_ALLOW_INSECURE_NO_AUTH=1 only for isolated development."
        )
    if AUTH_TOKEN and len(AUTH_TOKEN) < 16:
        raise RuntimeError("HERDR_RELAY_TOKEN must contain at least 16 characters.")
    if not is_loopback_host(RELAY_HOST) and not ALLOW_REMOTE_BIND:
        raise RuntimeError(
            "Remote relay binding is disabled. Set HERDR_ALLOW_REMOTE_BIND=1 only behind a trusted access layer."
        )


def start_mdns():
    if not MDNS_ENABLED:
        return None, None
    try:
        from zeroconf import Zeroconf, ServiceInfo
        import socket as sock_mod
        import threading
        ip = sock_mod.gethostbyname(sock_mod.gethostname())
        info = ServiceInfo(
            "_herdr-remote._tcp.local.", "herdr-remote._herdr-remote._tcp.local.",
            addresses=[sock_mod.inet_aton(ip)], port=WS_PORT,
        )
        zc = Zeroconf()
        threading.Thread(target=zc.register_service, args=(info,), daemon=True).start()
        log.info("mDNS registering at %s", ip)
        return zc, info
    except Exception as e:
        log.warning("mDNS skipped: %s", e)
        return None, None


async def main():
    validate_runtime_config()
    zc, info = start_mdns()
    loop = asyncio.get_running_loop()
    try:
        await loop.create_datagram_endpoint(UDPPlugin, local_addr=("127.0.0.1", 8376))
    except OSError:
        log.warning("UDP 8376 in use, plugin push disabled")
    asyncio.create_task(poll_loop())
    asyncio.create_task(event_push())
    server = await serve(
        handle_client,
        RELAY_HOST,
        WS_PORT,
        process_request=process_request,
        max_size=64 * 1024,
        max_queue=32,
    )
    hosts = ["local"] + REMOTES
    log.info("herdr-remote relay on %s:%d (WebSocket + HTTP POST)", RELAY_HOST, WS_PORT)
    log.info("Polling: %s", ", ".join(hosts))
    stop = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set_result, None)
    await stop
    server.close()
    if zc and info:
        zc.unregister_service(info)
        zc.close()


if __name__ == "__main__":
    asyncio.run(main())
