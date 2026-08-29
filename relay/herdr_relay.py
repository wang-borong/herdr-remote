#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0", "zeroconf>=0.80.0", "pywebpush>=2.0.0", "py-vapid>=1.9.0"]
# ///
"""herdr-remote relay — polls herdr, accepts push events (HTTP POST + WebSocket + UDP), broadcasts to clients."""
import asyncio
import base64
import binascii
import contextlib
import getpass
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue as thread_queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from email.header import decode_header, make_header
from http.cookies import CookieError, SimpleCookie
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

try:
    from websockets.asyncio.server import serve
except ImportError:
    from websockets.server import serve

from agent_state import complete_agent_update_message
from terminal_sessions import (
    TerminalConfigError,
    TerminalSession,
    delete_ssh_profile,
    load_ssh_profiles,
    normalize_ssh_profile,
    save_ssh_profile,
    terminal_profiles,
    terminate_persistent_session,
)

os.umask(0o077)

def _get_log_dir():
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/herdr-remote")
    if sys.platform == "win32":
        base = os.environ.get(
            "LOCALAPPDATA",
            os.path.expanduser("~/AppData/Local"),
        )
        return os.path.join(base, "herdr-remote", "logs")
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


def normalized_http_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(str(value or "").strip())
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        return None
    return scheme, host, port


TRUSTED_ORIGIN_IDENTITIES = frozenset(filter(None, (
    normalized_http_origin(value)
    for configured in (
        os.environ.get("HERDR_RELAY_TRUSTED_ORIGINS", ""),
        os.environ.get("HERDR_TRUSTED_ORIGINS", ""),
    )
    for value in configured.split(",")
    if value.strip()
)))


ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:\][^\x07]*(?:\x07|\x1B\\)|[@-_][0-?]*[ -/]*[@-~])"
)
ANSI_BACKGROUND_RE = re.compile(
    r"\x1B\[(?:[0-9]+;)*(?:4[0-8]|10[0-7])(?:;[0-9]+)*m"
)
ANSI_TRAILING_BACKGROUND_PADDING_RE = re.compile(
    r"(?P<background>\x1B\[(?:[0-9]+;)*(?:4[0-8]|10[0-7])(?:;[0-9]+)*m)"
    r"[ \t]+"
    r"(?P<suffix>(?:\x1B\[[0-?]*[ -/]*[@-~])*)"
    r"(?=\r?$)",
    re.MULTILINE,
)
ANSI_SGR_RE = re.compile(r"\x1B\[(?P<parameters>[0-9;]*)m")
CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
MODEL_PATH_RE = re.compile(r"^[^·]+?\s*·\s*(?:~|/).+$")
WORKED_FOR_RE = re.compile(
    r"Worked for\s+(?P<duration>(?:<?\d+(?:\.\d+)?[dhms]\s*)+)",
    re.IGNORECASE,
)
WORKING_RE = re.compile(r"(?:^|\s)Working\s*(?:\((?P<details>[^)]*)\))?", re.IGNORECASE)
BACKGROUND_TERMINAL_RE = re.compile(
    r"(?P<count>\d+)\s+background terminals? running",
    re.IGNORECASE,
)
DIVIDER_LINE_RE = re.compile(r"^[─━═—–_\-=]+$")
CODEX_LIST_ITEM_RE = re.compile(r"^(?P<indent> *)(?:[-+*]|\d+[.)])\s+")
CODEX_STRUCTURAL_TEXT_RE = re.compile(
    r"^(?:#{1,6}\s|>|```|~~~|\||[┌┐└┘├┤┬┴┼│┃╭╮╰╯])"
)
CODEX_REFLOW_MIN_WIDTH = 40
CODEX_REFLOW_MIN_FILL_RATIO = 0.88


HERDR = (
    os.environ.get("HERDR_BIN")
    or shutil.which("herdr")
    or ("herdr" if sys.platform == "win32" else "/opt/homebrew/bin/herdr")
)
CODEX = (
    os.environ.get("HERDR_CODEX_BIN")
    or shutil.which("codex")
    or "codex"
)
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
WEB_TERMINAL_ENABLED = env_flag("HERDR_WEB_TERMINAL")
TERMINAL_ALLOWED_USERS = {
    value.strip().casefold()
    for value in os.environ.get("HERDR_TERMINAL_ALLOWED_USERS", "").split(",")
    if value.strip()
}
TERMINAL_ALLOW_DEVELOPMENT = env_flag("HERDR_TERMINAL_ALLOW_DEVELOPMENT")
TAILSCALE_SSH_ENABLED = env_flag("HERDR_TAILSCALE_SSH")
SSH_HOSTS_FILE = Path(os.path.expanduser(os.environ.get(
    "HERDR_SSH_HOSTS_FILE",
    "~/.config/herdr-remote/ssh-hosts.json",
)))
SSH_CONFIG_FILE = Path(os.path.expanduser(os.environ.get(
    "HERDR_SSH_CONFIG_FILE",
    "~/.ssh/config",
)))
TERMINAL_SHELL = os.environ.get("HERDR_TERMINAL_SHELL") or os.environ.get("SHELL") or "/bin/sh"
TERMINAL_SHELL = shutil.which(TERMINAL_SHELL) or TERMINAL_SHELL
SSH_BINARY = shutil.which("ssh") or ("ssh" if sys.platform == "win32" else "/usr/bin/ssh")
TMUX_BINARY = shutil.which("tmux")
try:
    TERMINAL_MAX_SESSIONS = max(1, min(int(os.environ.get("HERDR_TERMINAL_MAX_SESSIONS", "6")), 16))
except ValueError:
    TERMINAL_MAX_SESSIONS = 6
WEB_SESSION_COOKIE = "herdr_session"
WEB_SESSION_TTL_SECONDS = 8 * 60 * 60

# Herdr session selection is scoped to each Agent Source. Local keeps the
# historical ``None`` key; configured SSH sources use their stable profile ID,
# while HERDR_REMOTES compatibility sources continue to use ``user@host``.
DEFAULT_LOCAL_SESSION = os.environ.get("HERDR_SESSION") or None
ACTIVE_SESSIONS = {}


def _session_source_key(remote=None):
    if remote is None:
        return None
    if not isinstance(remote, dict):
        return remote
    source_id = str(remote.get("id") or "")
    target = str(remote.get("target") or "")
    if source_id.startswith("legacy-") and target:
        return target
    return source_id or target


def active_session_for(remote=None):
    """Return the selected Herdr session for a local or SSH Agent Source."""
    source_key = _session_source_key(remote)
    if source_key in ACTIVE_SESSIONS:
        return ACTIVE_SESSIONS[source_key]
    return DEFAULT_LOCAL_SESSION if source_key is None else None


def _herdr_env(session):
    """Build a child environment targeting one Herdr session.

    Following Herdr's default must remove both inherited selectors: managed
    services can set HERDR_SESSION, while a relay launched inside a pane can
    inherit HERDR_SOCKET_PATH.
    """
    environment = os.environ.copy()
    environment.pop("HERDR_SOCKET_PATH", None)
    if session:
        environment["HERDR_SESSION"] = session
    else:
        environment.pop("HERDR_SESSION", None)
    return environment

# VAPID Web Push
VAPID_PUBLIC_KEY = os.environ.get("HERDR_VAPID_PUBLIC", "")
VAPID_PRIVATE_KEY = os.environ.get("HERDR_VAPID_PRIVATE", "")
VAPID_SUBJECT = os.environ.get("HERDR_VAPID_SUBJECT", "mailto:herdr@localhost")
push_subscriptions = []  # list of PushSubscription dicts
PUSH_SUBS_FILE = os.path.join(LOG_DIR, "push_subs.json")
ACTIVE_SESSIONS_FILE = os.path.join(LOG_DIR, "active_sessions.json")

# Remote hosts: comma-separated SSH targets
REMOTES = [r.strip() for r in os.environ.get("HERDR_REMOTES", "").split(",") if r.strip()]
REMOTE_HERDR_BIN = os.environ.get("HERDR_REMOTE_BIN", "herdr").strip() or "herdr"
# Compatibility alias retained for older clients/tests and documentation.
REMOTE_HERDR = REMOTE_HERDR_BIN
REMOTE_CODEX_BIN = os.environ.get("HERDR_REMOTE_CODEX_BIN", "codex").strip() or "codex"


def configured_workspace_roots() -> list[Path]:
    configured = os.environ.get("HERDR_WORKSPACE_ROOTS")
    values = (
        [os.path.expanduser("~")]
        if configured is None
        else [value.strip() for value in configured.split(os.pathsep) if value.strip()]
    )
    home = Path(os.path.expanduser("~"))
    expanded_values = [Path(os.path.expanduser(value)) for value in values]
    legacy_workspace = home / "Workspace"
    legacy_workspace_ai = home / "workspace-ai"
    if expanded_values == [legacy_workspace] or (
        len(expanded_values) == 2
        and set(expanded_values) == {legacy_workspace, legacy_workspace_ai}
    ):
        values = [str(home)]

    roots = []
    for value in values:
        try:
            root = Path(os.path.expanduser(value)).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if root.is_dir() and root not in roots:
            roots.append(root)
    return roots


WORKSPACE_ROOTS = configured_workspace_roots()
WORKSPACE_ENTRY_LIMIT = 200
WORKSPACE_FILE_ENTRY_LIMIT = 400
WORKSPACE_FILE_PREVIEW_MAX_BYTES = 1024 * 1024
WORKSPACE_FILE_DOWNLOAD_MAX_BYTES = 25 * 1024 * 1024
WORKSPACE_UPLOAD_CHUNK_BYTES = 512 * 1024
try:
    WORKSPACE_UPLOAD_MAX_BYTES = max(
        1024 * 1024,
        min(
            int(os.environ.get("HERDR_WORKSPACE_UPLOAD_MAX_BYTES", str(2 * 1024**3))),
            64 * 1024**3,
        ),
    )
except ValueError:
    WORKSPACE_UPLOAD_MAX_BYTES = 2 * 1024**3
WORKSPACE_DOWNLOAD_TOKEN_TTL_SECONDS = 90
WORKSPACE_DOWNLOAD_TOKEN_LIMIT = 256
AGENT_PROMPT_WAIT_TIMEOUT_MS = 8_000
AGENT_PROMPT_PROCESS_TIMEOUT_SECONDS = 12
AGENT_PROMPT_CONFIRM_ATTEMPTS = 15
AGENT_PROMPT_CONFIRM_INTERVAL_SECONDS = 0.2
AGENT_START_PANE_READY_TIMEOUT_SECONDS = 10
AGENT_START_PANE_READY_INTERVAL_SECONDS = 0.1
PROMPT_IMAGE_MAX_COUNT = 4
PROMPT_IMAGE_MAX_BYTES = 8 * 1024 * 1024
PROMPT_IMAGE_TOTAL_MAX_BYTES = 20 * 1024 * 1024
CODEX_APP_SERVER_INITIALIZE_TIMEOUT_SECONDS = 10
CODEX_APP_SERVER_QUEUE_TIMEOUT_SECONDS = 30
CODEX_APP_SERVER_STOP_TIMEOUT_SECONDS = 2
PROMPT_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
WEBSOCKET_MAX_MESSAGE_BYTES = 32 * 1024 * 1024
CONVERSATION_HISTORY_MAX_MESSAGES = 200
CONVERSATION_HISTORY_CONTENT_MAX_CHARS = 20_000
PANE_READ_MAX_LINES = 1000

TOOL_OPTIONS = ["yes, single permission", "trust, always allow", "no (tab to edit)"]
SUBAGENT_OPTIONS = ["approve all pending", "configure individually", "exit (cancel subagents)"]
CHROME_RE = re.compile(
    r"^[\s─━═_—│|◔◑◕●\s]+$"
    r"|Kiro\s[·•]"
    r"|esc to cancel"
    r"|type to queue"
    r"|^\s*[◔◑◕●]\s+(Shell|Bash)"
)
QUESTION_OPTION_RE = re.compile(
    r"^(?P<cursor>[\uf054>›❯▸→])?\s*"
    r"(?P<marker>[\uf046\uf10c\uf192\uf096\uf14a○◉☐☑]|\([ o]\)|\[[ xX]\])\s+"
    r"(?P<label>.+?)\s*$"
)
QUESTION_OTHER = "Other (type your own)"

clients = set()
last_statuses = {}
last_blocked_prompts = {}
event_queue = asyncio.Queue()
pane_remote_map = {}
pane_raw_map = {}
known_panes = set()
agent_cache = {}
agent_source_cache = {}
agent_start_in_progress = False
client_auth = {}
active_terminal_sessions = set()
machine_access_cache = None
workspace_downloads = {}
workspace_download_lock = threading.Lock()
_remote_locks: dict[tuple[str, int], threading.Lock] = {}
_remote_locks_guard = threading.Lock()
_session_list_cache = {}
POLL_GENERATION = 0
SESSION_REFRESH_EVERY = 15
SESSION_LIST_CACHE_TTL = SESSION_REFRESH_EVERY * POLL_INTERVAL

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


def _load_active_sessions():
    """Restore persisted per-source selections without trusting file types."""
    if not os.path.isfile(ACTIVE_SESSIONS_FILE):
        return
    try:
        with open(ACTIVE_SESSIONS_FILE) as f:
            stored = json.load(f)
    except Exception:
        return
    if not isinstance(stored, dict):
        return
    for key, value in stored.items():
        if value is not None and not isinstance(value, str):
            continue
        ACTIVE_SESSIONS[None if key == "local" else key] = value


def _save_active_sessions():
    payload = {
        ("local" if key is None else key): value
        for key, value in ACTIVE_SESSIONS.items()
    }
    with open(ACTIVE_SESSIONS_FILE, "w") as f:
        json.dump(payload, f)


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
_load_active_sessions()


def local_agent_source() -> dict:
    return {
        "id": "local",
        "kind": "local",
        "label": "本机",
        "target": "",
        "port": 0,
        "agent_enabled": True,
        "herdr_bin": HERDR,
        "workspace_root": "",
        "workspace_roots": [],
    }


def legacy_agent_source(remote: str) -> dict:
    digest = hashlib.sha256(remote.encode()).hexdigest()[:12]
    return normalize_ssh_profile({
        "id": f"legacy-{digest}",
        "label": remote,
        "target": remote,
        "port": 22,
        "agent_enabled": True,
        "herdr_bin": REMOTE_HERDR_BIN,
        "workspace_root": "~",
    })


def configured_agent_sources() -> list[dict]:
    sources = [local_agent_source()]
    remote_endpoints = set()
    try:
        profiles = load_ssh_profiles(SSH_HOSTS_FILE)
    except TerminalConfigError:
        profiles = []
    for profile in profiles:
        if not profile.get("agent_enabled"):
            continue
        sources.append(profile)
        remote_endpoints.add((profile["target"], profile.get("port", 22)))
    for remote in REMOTES:
        try:
            source = legacy_agent_source(remote)
        except TerminalConfigError:
            log.warning("Ignoring invalid HERDR_REMOTES target")
            continue
        endpoint = (source["target"], source.get("port", 22))
        if endpoint in remote_endpoints:
            continue
        sources.append(source)
        remote_endpoints.add(endpoint)
    return sources


def agent_source(source_id: str | None) -> dict:
    requested = str(source_id or "local")
    for source in configured_agent_sources():
        if source["id"] == requested:
            return source
    raise ValueError("Agent source is not configured")


def _session_source(host=None) -> dict:
    requested = str(host or "local")
    for source in configured_agent_sources():
        aliases = {
            str(source.get("id") or ""),
            str(source.get("target") or ""),
            str(source.get("label") or ""),
        }
        if requested in aliases or (requested == "local" and source["kind"] == "local"):
            return source
    raise KeyError(host)


def _source_key(host=None):
    """Map a client source identifier to the persisted session key."""
    source = _session_source(host)
    return _session_source_key(None if source["kind"] == "local" else source)


def _session_remote(source: dict):
    if source["kind"] == "local":
        return None
    if str(source.get("id") or "").startswith("legacy-"):
        return str(source.get("target") or "")
    return source


def workspace_source(
    source_id: str | None,
    *,
    include_terminal_profiles: bool = False,
) -> dict:
    """Resolve a Files source, optionally including SSH-only terminal profiles."""
    try:
        return agent_source(source_id)
    except ValueError:
        if not include_terminal_profiles:
            raise
    requested = str(source_id or "local")
    try:
        profile = terminal_profile(requested)
    except TerminalConfigError as error:
        raise ValueError("Workspace source is not configured") from error
    if profile.get("kind") == "local":
        return local_agent_source()
    return profile


def public_pane_id(source_id: str, raw_pane_id: str) -> str:
    return raw_pane_id if source_id == "local" else f"{source_id}::{raw_pane_id}"


def pane_route(pane_id: str) -> tuple[str, dict | str | None]:
    return pane_raw_map.get(pane_id, pane_id), pane_remote_map.get(pane_id)


def ssh_command_prefix(*, connect_timeout: int = 5, batch_mode: bool = True) -> list[str]:
    command = [SSH_BINARY]
    if SSH_CONFIG_FILE.is_file():
        command.extend(["-F", str(SSH_CONFIG_FILE)])
    command.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if batch_mode:
        command.extend(["-o", "BatchMode=yes"])
    return command


def remote_command_arguments(source: dict | str, args: list[str]) -> list[str]:
    if isinstance(source, str):
        source = {
            "target": source,
            "port": 22,
        }
    command = ssh_command_prefix()
    if source.get("port", 22) != 22:
        command.extend(["-p", str(source["port"])])
    command.extend([source["target"], shlex.join(args)])
    return command


def run_remote_result(
    source: dict | str,
    args: list[str],
    *,
    timeout: int = 15,
    input_data: bytes | None = None,
):
    if isinstance(source, str):
        source = {
            "target": source,
            "port": 22,
        }
    command = remote_command_arguments(source, args)
    lock_key = (str(source["target"]), int(source.get("port", 22)))
    with _remote_locks_guard:
        remote_lock = _remote_locks.get(lock_key)
        if remote_lock is None:
            remote_lock = threading.Lock()
            _remote_locks[lock_key] = remote_lock
    with remote_lock:
        options = {
            "capture_output": True,
            "timeout": timeout,
            "check": False,
        }
        if input_data is None:
            options.update({
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            })
        else:
            options["input"] = input_data
        return subprocess.run(command, **options)


def _invoke_herdr(*args, remote=None, timeout=15):
    """Run Herdr against the selected session for one Agent Source."""
    session = active_session_for(remote)
    if remote:
        herdr_bin = (
            remote.get("herdr_bin", REMOTE_HERDR_BIN)
            if isinstance(remote, dict)
            else REMOTE_HERDR_BIN
        )
        remote_args = [herdr_bin, *args]
        if session:
            # ``env`` keeps the assignment and every argument safely quoted by
            # remote_command_arguments/shlex.join.
            remote_args = ["env", f"HERDR_SESSION={session}", *remote_args]
        return run_remote_result(remote, remote_args, timeout=timeout)

    return subprocess.run(
        [HERDR, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_herdr_env(session),
    )


def run_herdr_result(*args, remote=None, timeout=15):
    return _invoke_herdr(*args, remote=remote, timeout=timeout)


def run_herdr(*args, remote=None):
    try:
        return run_herdr_result(*args, remote=remote).stdout.strip()
    except Exception:
        return ""


def _mutate_herdr(*args, remote=None) -> bool:
    try:
        return run_herdr_result(*args, remote=remote).returncode == 0
    except Exception:
        return False


def get_sessions(remote=None):
    """List Herdr sessions for one source, caching the short CLI response."""
    cache_key = _session_source_key(remote)
    cached = _session_list_cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < SESSION_LIST_CACHE_TTL:
        return cached[1]
    raw = run_herdr("session", "list", remote=remote)
    sessions = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "name":
            continue
        sessions.append({"name": parts[0], "running": parts[1] == "running"})
    _session_list_cache[cache_key] = (time.monotonic(), sessions)
    return sessions


def reset_pane_state():
    """Invalidate all pane/session-local state after a session switch."""
    global POLL_GENERATION
    known_panes.clear()
    agent_cache.clear()
    agent_source_cache.clear()
    pane_remote_map.clear()
    pane_raw_map.clear()
    last_statuses.clear()
    last_blocked_prompts.clear()
    _session_list_cache.clear()
    while True:
        try:
            event_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    POLL_GENERATION += 1


def apply_session_switch(host, session, ip="", device=""):
    """Select a Herdr session for one local/SSH Agent Source."""
    try:
        source = _session_source(host)
    except KeyError:
        return False, f"unknown host: {host}", False
    source_key = _session_source_key(None if source["kind"] == "local" else source)

    if source_key in ACTIVE_SESSIONS and ACTIVE_SESSIONS[source_key] == session:
        return True, "", False
    if session is not None:
        if not isinstance(session, str):
            return False, f"unknown session: {session}", False
        remote = _session_remote(source)
        names = {item["name"] for item in get_sessions(remote=remote)}
        if session not in names:
            return False, f"unknown session: {session}", False

    ACTIVE_SESSIONS[source_key] = session
    try:
        _save_active_sessions()
    except Exception:
        log.exception("failed to persist active sessions: host=%s session=%s", host, session)
    reset_pane_state()
    audit("session_switch", ip, device, "", f"host={host} session={session}")
    log.info("session switch: host=%s session=%s", host, session)
    return True, "", True


def sessions_message():
    """Return selectable sessions for every configured Agent Source."""
    sources = []
    for source in configured_agent_sources():
        remote = _session_remote(source)
        legacy_host = (
            str(source.get("target") or "")
            if str(source.get("id") or "").startswith("legacy-")
            else str(source.get("id") or "local")
        )
        sources.append({
            "host": "local" if source["kind"] == "local" else legacy_host,
            "source_id": source["id"],
            "label": source["label"],
            "active": active_session_for(remote),
            "sessions": get_sessions(remote=remote),
        })
    return {"type": "sessions", "sources": sources}


async def broadcast_sessions():
    generation = POLL_GENERATION
    message = sessions_message()
    if generation != POLL_GENERATION:
        return
    await broadcast(message)


def terminal_text_without_controls(content: str) -> str:
    cleaned = ANSI_ESCAPE_RE.sub("", str(content or ""))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return CONTROL_CHARACTER_RE.sub("", cleaned)


def plain_terminal_output(content: str) -> str:
    return terminal_text_without_controls(content).strip()


def terminal_display_width(content: str) -> int:
    """Return the terminal-cell width of text after ANSI/control removal."""
    width = 0
    for character in terminal_text_without_controls(content):
        if character in "\r\n":
            continue
        if character == "\t":
            width += 8 - (width % 8)
            continue
        category = unicodedata.category(character)
        if category in {"Mn", "Me", "Cf"}:
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def infer_codex_snapshot_width(content: str) -> int:
    """Infer the source pane width from Codex's full-width painted rows."""
    candidates = []
    for line in re.split(r"\r\n|\r|\n", str(content or "")):
        visible = terminal_text_without_controls(line)
        width = terminal_display_width(visible)
        if width < CODEX_REFLOW_MIN_WIDTH:
            continue
        stripped = visible.strip()
        background_blank = ANSI_BACKGROUND_RE.search(line) and not stripped
        divider_cells = sum(character in "─━═—–_-=" for character in stripped)
        divider = divider_cells >= 20 and divider_cells >= len(stripped) * 0.6
        if background_blank or divider:
            candidates.append(width)
    if not candidates:
        return 0
    counts = {width: candidates.count(width) for width in set(candidates)}
    return max(candidates, key=lambda width: (counts[width], width))


def _sgr_codes(sequence: str):
    parameters = ANSI_SGR_RE.fullmatch(sequence)
    if not parameters:
        return
    values = [int(value or 0) for value in parameters.group("parameters").split(";")]
    index = 0
    while index < len(values):
        code = values[index]
        yield code
        if code in {38, 48, 58} and index + 1 < len(values):
            mode = values[index + 1]
            if mode == 2:
                index += 5
                continue
            if mode == 5:
                index += 3
                continue
        index += 1


def _codex_assistant_message_start(line: str) -> bool:
    position = 0
    dim = False
    while True:
        match = ANSI_ESCAPE_RE.match(line, position)
        if not match:
            break
        sequence = match.group(0)
        for code in _sgr_codes(sequence) or ():
            if code == 0:
                dim = False
            elif code == 2:
                dim = True
            elif code == 22:
                dim = False
        position = match.end()
    return dim and line[position:].startswith("• ")


def _top_level_bullet(line: str) -> bool:
    position = 0
    while True:
        match = ANSI_ESCAPE_RE.match(line, position)
        if not match:
            break
        position = match.end()
    return line[position:].startswith("• ")


def _remove_leading_spaces_preserving_ansi(line: str, count: int) -> str:
    if count <= 0:
        return line
    position = 0
    removed = 0
    prefix = []
    while position < len(line) and removed < count:
        match = ANSI_ESCAPE_RE.match(line, position)
        if match:
            prefix.append(match.group(0))
            position = match.end()
            continue
        if line[position] != " ":
            return line
        removed += 1
        position += 1
    return "".join(prefix) + line[position:] if removed == count else line


def _codex_reflow_separator(left: str, right: str) -> str:
    left = terminal_text_without_controls(left).rstrip()
    right = terminal_text_without_controls(right).lstrip()
    if not left or not right:
        return ""
    left_character = left[-1]
    right_character = right[0]
    if right_character in ",.;:!?%)]}，。；：！？、）】》」』”’":
        return ""
    if left_character in "([{（【《「『“‘/\\@#_-":
        return ""
    left_wide = unicodedata.east_asian_width(left_character) in {"W", "F"}
    right_wide = unicodedata.east_asian_width(right_character) in {"W", "F"}
    return "" if left_wide and right_wide else " "


def _codex_reflow_paragraph(
    lines: list[str],
    source_width: int,
    assistant_start: bool,
) -> list[str]:
    if len(lines) < 2 or any(ANSI_BACKGROUND_RE.search(line) for line in lines):
        return lines

    visible = [terminal_text_without_controls(line) for line in lines]
    first_text = (
        visible[0][2:]
        if assistant_start and visible[0].startswith("• ")
        else visible[0].lstrip()
    )
    first_indent = (
        0
        if assistant_start
        else len(visible[0]) - len(visible[0].lstrip(" "))
    )
    list_match = CODEX_LIST_ITEM_RE.match(visible[0])
    if CODEX_STRUCTURAL_TEXT_RE.match(first_text) or (first_indent >= 4 and not list_match):
        return lines

    threshold = max(
        CODEX_REFLOW_MIN_WIDTH - 1,
        int(source_width * CODEX_REFLOW_MIN_FILL_RATIO),
    )
    output = [lines[0]]
    previous_physical = lines[0]
    item_indent = list_match.end() if list_match else None

    for index in range(1, len(lines)):
        current = lines[index]
        current_visible = visible[index]
        current_indent = len(current_visible) - len(current_visible.lstrip(" "))
        current_list = CODEX_LIST_ITEM_RE.match(current_visible)
        if current_list:
            output.append(current)
            previous_physical = current
            item_indent = current_list.end()
            continue

        expected_indent = item_indent if item_indent is not None else (
            2 if assistant_start else first_indent
        )
        structural = CODEX_STRUCTURAL_TEXT_RE.match(current_visible.lstrip())
        should_join = (
            not structural
            and expected_indent > 0
            and current_indent == expected_indent
            and terminal_display_width(previous_physical) >= threshold
        )
        if should_join:
            output[-1] += _codex_reflow_separator(previous_physical, current)
            output[-1] += _remove_leading_spaces_preserving_ansi(
                current,
                expected_indent,
            )
        else:
            output.append(current)
        previous_physical = current
    return output


def reflow_codex_terminal_snapshot(content: str, source_width: int = 0) -> str:
    """Turn Codex TUI prose rows back into logical lines for browser reflow.

    Codex renders Markdown to physical rows at the source pane width, so tmux's
    wrapped-line metadata cannot recover these boundaries. Only nearly-full
    prose rows inside a dim Codex assistant message are joined. Structural
    Markdown, code, tables, terminal events, and ANSI background rows remain
    physical lines.
    """
    raw = str(content or "")
    if not raw:
        return ""
    width = source_width or infer_codex_snapshot_width(raw)
    if width < CODEX_REFLOW_MIN_WIDTH:
        return raw

    lines = re.split(r"\r\n|\r|\n", raw)
    output = []
    index = 0
    inside_assistant_message = False
    while index < len(lines):
        line = lines[index]
        assistant_start = _codex_assistant_message_start(line)
        visible = terminal_text_without_controls(line)
        if assistant_start:
            inside_assistant_message = True
        elif inside_assistant_message and (
            (_top_level_bullet(line) and visible.strip())
            or WORKED_FOR_RE.search(visible)
            or WORKING_RE.search(visible)
            or MODEL_PATH_RE.match(visible.strip())
        ):
            inside_assistant_message = False

        if not inside_assistant_message or not visible.strip():
            output.append(line)
            index += 1
            continue

        end = index + 1
        while end < len(lines) and terminal_text_without_controls(lines[end]).strip():
            if _top_level_bullet(lines[end]):
                break
            end += 1
        output.extend(
            _codex_reflow_paragraph(lines[index:end], width, assistant_start)
        )
        index = end
    return "\r\n".join(output)


def compact_ansi_background_padding(content: str) -> str:
    """Replace source-width color padding with erase-to-end terminal commands.

    Codex paints diff rows by appending background-colored spaces up to the
    source terminal width. A narrower browser terminal wraps those invisible
    spaces onto another row, which makes every diff line look double-spaced.
    CSI K preserves the full-row background without adding wrap-width cells.
    """
    return ANSI_TRAILING_BACKGROUND_PADDING_RE.sub(
        lambda match: f"{match.group('background')}\x1b[K{match.group('suffix')}",
        str(content or ""),
    )


def simplify_codex_terminal_snapshot(content: str, agent_status: str = "") -> str:
    raw = str(content or "")
    if not raw:
        return ""

    lines = re.split(r"\r\n|\r|\n", raw)
    plain_lines = [plain_terminal_output(line) for line in lines]
    model_index = next(
        (index for index in range(len(lines) - 1, -1, -1) if plain_lines[index]),
        None,
    )
    if model_index is None or not MODEL_PATH_RE.match(plain_lines[model_index]):
        return raw

    prompt_window_start = max(0, model_index - 8)
    if not any(
        ANSI_BACKGROUND_RE.search(lines[index])
        for index in range(prompt_window_start, model_index)
    ):
        return raw

    input_start = model_index
    while input_start > 0:
        previous = input_start - 1
        # The submitted prompt and the live editor use the same background.
        # An unstyled blank line is their boundary, even on spinner frames
        # where Codex temporarily omits the Working status line.
        if ANSI_BACKGROUND_RE.search(lines[previous]):
            input_start -= 1
            continue
        break

    worked_duration = ""
    is_working = False
    background_count = ""
    summary_indexes = []
    index = input_start - 1
    while index >= 0:
        plain = plain_lines[index]
        if not plain or DIVIDER_LINE_RE.match(plain):
            index -= 1
            continue

        worked = WORKED_FOR_RE.search(plain)
        working = WORKING_RE.search(plain)
        background = BACKGROUND_TERMINAL_RE.search(plain)
        if not any((worked, working, background)):
            break
        summary_indexes.append(index)
        if worked:
            worked_duration = worked.group("duration").strip()
        if working:
            is_working = True
        if background:
            background_count = background.group("count")
        index -= 1

    tail_start = min(summary_indexes) if summary_indexes else input_start
    body = lines[:tail_start]
    while body and not plain_terminal_output(body[-1]):
        body.pop()

    working_state = not worked_duration and (
        is_working or str(agent_status or "").casefold() == "working"
    )
    summary = []
    if worked_duration:
        summary.append(f"\x1b[0m\x1b[2mWorked for {worked_duration}\x1b[0m")
    elif working_state:
        summary.append("\x1b[0m\x1b[2mWorking\x1b[0m")
    if background_count and not working_state:
        suffix = "" if background_count == "1" else "s"
        summary.append(
            f"\x1b[0m\x1b[2m{background_count} background terminal{suffix} running\x1b[0m"
        )
    summary.append(lines[model_index].lstrip())

    if body:
        body.append("")
    return "\r\n".join([*body, *summary])


def read_pane_snapshot(
    pane_id: str,
    lines: int,
    remote=None,
    agent_status: str = "",
) -> tuple[str, str]:
    args = (
        "pane",
        "read",
        pane_id,
        "--lines",
        str(lines),
        "--source",
    )
    result = None
    for source in ("recent-unwrapped", "recent"):
        try:
            candidate = run_herdr_result(
                *args,
                source,
                "--format",
                "ansi",
                remote=remote,
            )
        except Exception:
            candidate = None
        if candidate is not None and candidate.returncode == 0:
            result = candidate
            break

    if result is not None and result.returncode == 0:
        raw_content = str(result.stdout or "").strip()
        source_width = infer_codex_snapshot_width(raw_content)
        ansi_content = compact_ansi_background_padding(
            reflow_codex_terminal_snapshot(
                simplify_codex_terminal_snapshot(
                    raw_content,
                    agent_status=agent_status,
                ),
                source_width=source_width,
            )
        )
        return plain_terminal_output(ansi_content), ansi_content

    # Herdr versions predating ANSI pane snapshots still receive the original
    # plain-text request instead of leaving the Agent output window empty.
    return run_herdr(*args, "recent", remote=remote), ""


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
    try:
        home = Path.home().resolve(strict=True)
    except (OSError, RuntimeError):
        home = Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


WORKSPACE_MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown", ".mkd"}
WORKSPACE_HTML_SUFFIXES = {".htm", ".html"}
WORKSPACE_CODE_LANGUAGES = {
    ".bash": "bash",
    ".bat": "dos",
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "ini",
    ".cmake": "cmake",
    ".cmd": "dos",
    ".conf": "ini",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".cxx": "cpp",
    ".dart": "dart",
    ".diff": "diff",
    ".dockerfile": "dockerfile",
    ".fish": "bash",
    ".go": "go",
    ".gql": "graphql",
    ".gradle": "gradle",
    ".graphql": "graphql",
    ".h": "c",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".htm": "xml",
    ".html": "xml",
    ".ini": "ini",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsonc": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".less": "less",
    ".lua": "lua",
    ".mjs": "javascript",
    ".patch": "diff",
    ".php": "php",
    ".proto": "protobuf",
    ".ps1": "powershell",
    ".py": "python",
    ".pyi": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scss": "scss",
    ".sh": "bash",
    ".sql": "sql",
    ".svelte": "xml",
    ".swift": "swift",
    ".toml": "ini",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "plaintext",
    ".vue": "xml",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".zsh": "bash",
}
WORKSPACE_CODE_FILENAMES = {
    "cmakelists.txt": "cmake",
    "containerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "gemfile": "ruby",
    "justfile": "makefile",
    "license": "plaintext",
    "makefile": "makefile",
    "procfile": "plaintext",
    "rakefile": "ruby",
    "readme": "plaintext",
    "vagrantfile": "ruby",
}


def safe_workspace_entry_name(name: str) -> bool:
    return (
        bool(name)
        and not name.startswith(".")
        and not any(ord(character) < 32 or ord(character) == 127 for character in name)
    )


def workspace_file_preview_info(name: str) -> dict | None:
    normalized = str(name or "").casefold()
    suffix = Path(normalized).suffix
    if suffix in WORKSPACE_HTML_SUFFIXES:
        return {"kind": "html", "language": "xml"}
    if suffix in WORKSPACE_MARKDOWN_SUFFIXES:
        return {"kind": "markdown", "language": "markdown"}
    language = WORKSPACE_CODE_FILENAMES.get(normalized) or WORKSPACE_CODE_LANGUAGES.get(suffix)
    if not language:
        return None
    return {"kind": "code", "language": language}


def workspace_root_for_lexical_path(path: Path) -> Path | None:
    for root in WORKSPACE_ROOTS:
        if path == root or root in path.parents:
            return root
    return None


def resolve_workspace_file_path(value: str, *, expected: str = "any") -> Path:
    if not WORKSPACE_ROOTS:
        raise ValueError("No workspace roots are configured")
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("A workspace path is required")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Workspace path contains control characters")

    candidate = Path(os.path.expanduser(value.strip()))
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOTS[0] / candidate
    lexical = Path(os.path.abspath(candidate))
    root = workspace_root_for_lexical_path(lexical)
    if root is None:
        raise ValueError("Workspace path is outside the configured roots")

    relative = lexical.relative_to(root)
    current = root
    for component in relative.parts:
        if component.startswith("."):
            raise ValueError("Hidden workspace paths cannot be accessed")
        current = current / component
        try:
            if current.is_symlink():
                raise ValueError("Workspace symbolic links cannot be accessed")
            current.lstat()
        except FileNotFoundError as error:
            raise ValueError("Workspace path does not exist") from error
        except OSError as error:
            raise ValueError("Workspace path cannot be accessed") from error

    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Workspace path does not exist") from error
    if not path_within_workspace_roots(resolved):
        raise ValueError("Workspace path is outside the configured roots")
    if expected == "directory" and not resolved.is_dir():
        raise ValueError("Workspace path is not a directory")
    if expected == "file" and not resolved.is_file():
        raise ValueError("Workspace path is not a regular file")
    return resolved


def workspace_file_entry(path: Path, *, kind: str, size: int = 0) -> dict:
    entry = {
        "name": path.name or str(path),
        "path": str(path),
        "display_path": display_workspace_path(path),
        "kind": kind,
        "size": max(0, int(size)),
    }
    if kind == "directory":
        entry["is_repo"] = git_root_for(path) == path
        return entry

    preview = workspace_file_preview_info(path.name)
    entry.update({
        "is_repo": False,
        "previewable": bool(preview) and size <= WORKSPACE_FILE_PREVIEW_MAX_BYTES,
        "preview_kind": preview["kind"] if preview else "",
        "language": preview["language"] if preview else "",
        "downloadable": size <= WORKSPACE_FILE_DOWNLOAD_MAX_BYTES,
    })
    if preview and size > WORKSPACE_FILE_PREVIEW_MAX_BYTES:
        entry["preview_reason"] = "File is too large to preview"
    elif not preview:
        entry["preview_reason"] = "Preview is limited to code, Markdown, and HTML files"
    if size > WORKSPACE_FILE_DOWNLOAD_MAX_BYTES:
        entry["download_reason"] = "File is too large to download"
    return entry


def workspace_file_listing(value: str | None = None) -> dict:
    if not WORKSPACE_ROOTS:
        raise ValueError("No workspace roots are configured")
    if not value:
        return {
            "path": "",
            "display_path": "Allowed directories",
            "parent": None,
            "entries": [workspace_file_entry(root, kind="directory") for root in WORKSPACE_ROOTS],
            "truncated": False,
        }

    directory = resolve_workspace_file_path(value, expected="directory")
    entries = []
    try:
        children = list(os.scandir(directory))
    except OSError as error:
        raise ValueError("Workspace directory cannot be read") from error
    for child in children:
        if not safe_workspace_entry_name(child.name) or child.is_symlink():
            continue
        path = Path(child.path)
        try:
            if child.is_dir(follow_symlinks=False):
                entries.append(workspace_file_entry(path.resolve(strict=True), kind="directory"))
            elif child.is_file(follow_symlinks=False):
                size = child.stat(follow_symlinks=False).st_size
                entries.append(workspace_file_entry(path.resolve(strict=True), kind="file", size=size))
        except (OSError, RuntimeError):
            continue

    entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
    truncated = len(entries) > WORKSPACE_FILE_ENTRY_LIMIT
    entries = entries[:WORKSPACE_FILE_ENTRY_LIMIT]
    parent = "" if directory in WORKSPACE_ROOTS else str(directory.parent)
    return {
        "path": str(directory),
        "display_path": display_workspace_path(directory),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
    }


def workspace_file_metadata(value: str) -> dict:
    path = resolve_workspace_file_path(value, expected="file")
    try:
        file_stat = path.lstat()
    except OSError as error:
        raise ValueError("Workspace file cannot be read") from error
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("Workspace path is not a regular file")
    return {
        "path": str(path),
        "display_path": display_workspace_path(path),
        "name": path.name,
        "size": file_stat.st_size,
    }


def read_workspace_file_bytes(path: str, maximum: int, expected_size: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("Workspace file cannot be read") from error
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("Workspace path is not a regular file")
        if file_stat.st_size != expected_size:
            raise ValueError("Workspace file changed while it was being read")
        if file_stat.st_size > maximum:
            raise ValueError("Workspace file exceeds the allowed size")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(maximum + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > maximum:
        raise ValueError("Workspace file exceeds the allowed size")
    if len(raw) != expected_size:
        raise ValueError("Workspace file changed while it was being read")
    return raw


def workspace_file_read(value: str) -> dict:
    metadata = workspace_file_metadata(value)
    preview = workspace_file_preview_info(metadata["name"])
    if not preview:
        raise ValueError("Preview is limited to code, Markdown, and HTML files")
    if metadata["size"] > WORKSPACE_FILE_PREVIEW_MAX_BYTES:
        raise ValueError("Workspace file is too large to preview")
    raw = read_workspace_file_bytes(
        metadata["path"],
        WORKSPACE_FILE_PREVIEW_MAX_BYTES,
        metadata["size"],
    )
    if b"\x00" in raw:
        raise ValueError("Binary files cannot be previewed")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Workspace file is not valid UTF-8 text") from error
    return {
        **metadata,
        **preview,
        "content": content,
        "line_count": content.count("\n") + 1,
    }


def workspace_file_download(value: str) -> tuple[dict, bytes]:
    metadata = workspace_file_metadata(value)
    if metadata["size"] > WORKSPACE_FILE_DOWNLOAD_MAX_BYTES:
        raise ValueError("Workspace file is too large to download")
    data = read_workspace_file_bytes(
        metadata["path"],
        WORKSPACE_FILE_DOWNLOAD_MAX_BYTES,
        metadata["size"],
    )
    return metadata, data


def terminal_access_allowed(auth: dict) -> bool:
    if not WEB_TERMINAL_ENABLED:
        return False
    mode = auth.get("mode", "")
    if mode == "tailscale":
        login = str(auth.get("login", "")).casefold()
        return bool(login) and login in TERMINAL_ALLOWED_USERS
    return mode == "development" and TERMINAL_ALLOW_DEVELOPMENT


def configured_terminal_profiles() -> list[dict]:
    return terminal_profiles(SSH_HOSTS_FILE, socket.gethostname())


def terminal_profile(profile_id: str) -> dict:
    for profile in configured_terminal_profiles():
        if profile["id"] == profile_id:
            return profile
    raise TerminalConfigError("Terminal profile was not found")


def terminal_working_directory() -> Path:
    configured = os.environ.get("HERDR_TERMINAL_CWD", str(Path.home()))
    try:
        directory = Path(os.path.expanduser(configured)).resolve(strict=True)
    except (OSError, RuntimeError):
        directory = Path.home()
    return directory if directory.is_dir() else Path.home()


def machine_access_info() -> dict:
    global machine_access_cache
    if machine_access_cache is not None:
        return machine_access_cache

    info = {
        "hostname": socket.gethostname(),
        "username": getpass.getuser(),
        "tailscale_dns": "",
        "tailscale_ips": [],
        "native_ssh": TAILSCALE_SSH_ENABLED,
        "tmux": bool(TMUX_BINARY),
    }
    tailscale = shutil.which("tailscale")
    if tailscale and (TAILSCALE_WEB_ENABLED or TAILSCALE_SSH_ENABLED):
        try:
            result = subprocess.run(
                [tailscale, "status", "--json"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            status = json.loads(result.stdout) if result.returncode == 0 else {}
            self_node = status.get("Self") or {}
            info["tailscale_dns"] = str(self_node.get("DNSName", "")).rstrip(".")
            addresses = self_node.get("TailscaleIPs") or []
            info["tailscale_ips"] = [str(address) for address in addresses[:4]]
        except (json.JSONDecodeError, OSError, subprocess.SubprocessError):
            pass
    machine_access_cache = info
    return info


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
            "display_path": "Allowed directories",
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


REMOTE_WORKSPACE_SCRIPT = r'''
import json
import os
import sys

LIMIT = 200

def display(path):
    home = os.path.realpath(os.path.expanduser("~"))
    if path == home:
        return "~"
    prefix = home + os.sep
    return "~/" + path[len(prefix):] if path.startswith(prefix) else path

def within(path, roots):
    for root in roots:
        try:
            if os.path.commonpath((path, root)) == root:
                return True
        except ValueError:
            pass
    return False

def git_root(path, roots):
    candidate = path
    while within(candidate, roots):
        marker = os.path.join(candidate, ".git")
        if os.path.isdir(marker) or os.path.isfile(marker):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return ""

def fail(message):
    print(json.dumps({"error": message}))
    raise SystemExit(2)

try:
    configured = json.loads(sys.argv[1])
    requested = sys.argv[2]
except (IndexError, json.JSONDecodeError):
    fail("Remote workspace request is invalid")

roots = []
for value in configured:
    root = os.path.realpath(os.path.expanduser(value))
    if os.path.isdir(root) and root not in roots:
        roots.append(root)
if not roots:
    fail("No remote workspace roots are available")

if not requested:
    entries = [{
        "name": os.path.basename(root) or root,
        "path": root,
        "display_path": display(root),
        "is_repo": git_root(root, roots) == root,
    } for root in roots]
    print(json.dumps({
        "path": "",
        "display_path": "Allowed directories",
        "parent": None,
        "entries": entries,
        "can_start_agent": False,
        "truncated": False,
    }))
    raise SystemExit(0)

candidate = os.path.expanduser(requested)
if not os.path.isabs(candidate):
    candidate = os.path.join(roots[0], candidate)
directory = os.path.realpath(candidate)
if not os.path.isdir(directory):
    fail("Remote workspace directory does not exist")
if not within(directory, roots):
    fail("Remote workspace directory is outside the configured root")

entries = []
try:
    children = list(os.scandir(directory))
except OSError:
    fail("Remote workspace directory cannot be read")
for entry in children:
    if entry.name.startswith(".") or entry.is_symlink():
        continue
    try:
        if not entry.is_dir(follow_symlinks=False):
            continue
        child = os.path.realpath(entry.path)
    except OSError:
        continue
    if not within(child, roots):
        continue
    entries.append({
        "name": entry.name,
        "path": child,
        "display_path": display(child),
        "is_repo": git_root(child, roots) == child,
    })
entries.sort(key=lambda item: (not item["is_repo"], item["name"].casefold()))
truncated = len(entries) > LIMIT
entries = entries[:LIMIT]
parent = os.path.dirname(directory)
if not within(parent, roots):
    parent = None
print(json.dumps({
    "path": directory,
    "display_path": display(directory),
    "parent": parent,
    "entries": entries,
    "can_start_agent": True,
    "git_root": git_root(directory, roots),
    "truncated": truncated,
}))
'''


REMOTE_WORKSPACE_FILE_SCRIPT = r'''
import base64
import json
import os
import stat
import sys

LIMIT = 400

def display(path):
    home = os.path.realpath(os.path.expanduser("~"))
    if path == home:
        return "~"
    prefix = home + os.sep
    return "~/" + path[len(prefix):] if path.startswith(prefix) else path

def within(path, roots):
    for root in roots:
        try:
            if os.path.commonpath((path, root)) == root:
                return True
        except ValueError:
            pass
    return False

def git_root(path, roots):
    candidate = path
    while within(candidate, roots):
        marker = os.path.join(candidate, ".git")
        if os.path.isdir(marker) or os.path.isfile(marker):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return ""

def safe_name(name):
    return bool(name) and not name.startswith(".") and not any(
        ord(character) < 32 or ord(character) == 127 for character in name
    )

def fail(message):
    print(json.dumps({"error": message}))
    raise SystemExit(2)

try:
    configured = json.loads(sys.argv[1])
    operation = sys.argv[2]
    requested = sys.argv[3]
    maximum = int(sys.argv[4])
except (IndexError, json.JSONDecodeError, TypeError, ValueError):
    fail("Remote workspace file request is invalid")

roots = []
for value in configured:
    root = os.path.realpath(os.path.expanduser(value))
    if os.path.isdir(root) and root not in roots:
        roots.append(root)
if not roots:
    fail("No remote workspace roots are available")

def resolve(requested_path, expected):
    candidate = os.path.expanduser(requested_path)
    if not os.path.isabs(candidate):
        candidate = os.path.join(roots[0], candidate)
    lexical = os.path.abspath(candidate)
    root = next((item for item in roots if within(lexical, [item])), None)
    if root is None:
        fail("Remote workspace path is outside the configured roots")
    relative = os.path.relpath(lexical, root)
    current = root
    if relative != ".":
        for component in relative.split(os.sep):
            if component.startswith("."):
                fail("Hidden remote workspace paths cannot be accessed")
            current = os.path.join(current, component)
            try:
                mode = os.lstat(current).st_mode
            except OSError:
                fail("Remote workspace path does not exist")
            if stat.S_ISLNK(mode):
                fail("Remote workspace symbolic links cannot be accessed")
    resolved = os.path.realpath(lexical)
    if not within(resolved, roots):
        fail("Remote workspace path is outside the configured roots")
    if expected == "directory" and not os.path.isdir(resolved):
        fail("Remote workspace path is not a directory")
    if expected == "file":
        try:
            mode = os.stat(resolved, follow_symlinks=False).st_mode
        except OSError:
            fail("Remote workspace file cannot be read")
        if not stat.S_ISREG(mode):
            fail("Remote workspace path is not a regular file")
    return resolved

if operation == "list":
    if not requested:
        entries = [{
            "name": os.path.basename(root) or root,
            "path": root,
            "display_path": display(root),
            "kind": "directory",
            "size": 0,
            "is_repo": git_root(root, roots) == root,
        } for root in roots]
        print(json.dumps({
            "path": "",
            "display_path": "Allowed directories",
            "parent": None,
            "entries": entries,
            "truncated": False,
        }))
        raise SystemExit(0)

    directory = resolve(requested, "directory")
    entries = []
    try:
        children = list(os.scandir(directory))
    except OSError:
        fail("Remote workspace directory cannot be read")
    for child in children:
        if not safe_name(child.name) or child.is_symlink():
            continue
        try:
            if child.is_dir(follow_symlinks=False):
                child_path = os.path.realpath(child.path)
                entries.append({
                    "name": child.name,
                    "path": child_path,
                    "display_path": display(child_path),
                    "kind": "directory",
                    "size": 0,
                    "is_repo": git_root(child_path, roots) == child_path,
                })
            elif child.is_file(follow_symlinks=False):
                child_path = os.path.realpath(child.path)
                entries.append({
                    "name": child.name,
                    "path": child_path,
                    "display_path": display(child_path),
                    "kind": "file",
                    "size": child.stat(follow_symlinks=False).st_size,
                    "is_repo": False,
                })
        except OSError:
            continue
    entries.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold()))
    truncated = len(entries) > LIMIT
    entries = entries[:LIMIT]
    parent = "" if directory in roots else os.path.dirname(directory)
    print(json.dumps({
        "path": directory,
        "display_path": display(directory),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
    }))
    raise SystemExit(0)

if operation not in {"info", "read", "download"}:
    fail("Remote workspace file operation is invalid")

path = resolve(requested, "file")
try:
    size = os.stat(path, follow_symlinks=False).st_size
except OSError:
    fail("Remote workspace file cannot be read")
if maximum > 0 and size > maximum:
    fail("Remote workspace file exceeds the allowed size")

response = {
    "path": path,
    "display_path": display(path),
    "name": os.path.basename(path),
    "size": size,
}
if operation == "info":
    print(json.dumps(response))
    raise SystemExit(0)

descriptor = -1
try:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_size != size:
        fail("Remote workspace file changed while it was being read")
    with os.fdopen(descriptor, "rb") as handle:
        descriptor = -1
        raw = handle.read(maximum + 1 if maximum > 0 else -1)
except OSError:
    fail("Remote workspace file cannot be read")
finally:
    if descriptor >= 0:
        os.close(descriptor)
if maximum > 0 and len(raw) > maximum:
    fail("Remote workspace file exceeds the allowed size")
if len(raw) != size:
    fail("Remote workspace file changed while it was being read")

if operation == "download":
    response["data"] = base64.b64encode(raw).decode("ascii")
    print(json.dumps(response))
    raise SystemExit(0)

if b"\x00" in raw:
    fail("Binary files cannot be previewed")
try:
    response["content"] = raw.decode("utf-8-sig")
except UnicodeDecodeError:
    fail("Remote workspace file is not valid UTF-8 text")
print(json.dumps(response))
'''


def remote_workspace_directory_listing(source: dict, value: str | None = None) -> dict:
    if value is not None:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("A remote workspace directory is required")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Remote workspace path contains control characters")
        value = value.strip()
    try:
        result = run_remote_result(
            source,
            [
                "python3",
                "-c",
                REMOTE_WORKSPACE_SCRIPT,
                json.dumps(
                    source.get("workspace_roots")
                    or [source.get("workspace_root", "~")]
                ),
                value or "",
            ],
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Remote Agent source is offline") from error
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        if result.returncode == 255:
            raise ValueError("Remote Agent source is offline") from error
        raise ValueError("Remote workspace returned an invalid response") from error
    if not isinstance(response, dict):
        raise ValueError("Remote workspace returned an invalid response")
    if result.returncode != 0 or response.get("error"):
        raise ValueError(str(response.get("error") or "Remote workspace cannot be read"))
    return response


def remote_workspace_file_request(
    source: dict,
    operation: str,
    value: str | None = None,
    *,
    maximum: int = 0,
    timeout: int = 15,
) -> dict:
    if value is not None:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("A remote workspace path is required")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Remote workspace path contains control characters")
        value = value.strip()
    try:
        result = run_remote_result(
            source,
            [
                "python3",
                "-c",
                REMOTE_WORKSPACE_FILE_SCRIPT,
                json.dumps(
                    source.get("workspace_roots")
                    or [source.get("workspace_root", "~")]
                ),
                operation,
                value or "",
                str(max(0, int(maximum))),
            ],
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Remote Agent source is offline") from error
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        if result.returncode == 255:
            raise ValueError("Remote Agent source is offline") from error
        raise ValueError("Remote workspace returned an invalid file response") from error
    if not isinstance(response, dict):
        raise ValueError("Remote workspace returned an invalid file response")
    if result.returncode != 0 or response.get("error"):
        raise ValueError(str(response.get("error") or "Remote workspace file cannot be read"))
    return response


def remote_workspace_file_listing(source: dict, value: str | None = None) -> dict:
    listing = remote_workspace_file_request(source, "list", value, timeout=15)
    entries = []
    for entry in listing.get("entries", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "directory":
            entries.append(entry)
            continue
        preview = workspace_file_preview_info(str(entry.get("name", "")))
        size = max(0, int(entry.get("size", 0)))
        entry.update({
            "previewable": bool(preview) and size <= WORKSPACE_FILE_PREVIEW_MAX_BYTES,
            "preview_kind": preview["kind"] if preview else "",
            "language": preview["language"] if preview else "",
            "downloadable": size <= WORKSPACE_FILE_DOWNLOAD_MAX_BYTES,
        })
        if preview and size > WORKSPACE_FILE_PREVIEW_MAX_BYTES:
            entry["preview_reason"] = "File is too large to preview"
        elif not preview:
            entry["preview_reason"] = "Preview is limited to code, Markdown, and HTML files"
        if size > WORKSPACE_FILE_DOWNLOAD_MAX_BYTES:
            entry["download_reason"] = "File is too large to download"
        entries.append(entry)
    listing["entries"] = entries
    return listing


def remote_workspace_file_metadata(source: dict, value: str) -> dict:
    return remote_workspace_file_request(
        source,
        "info",
        value,
        maximum=WORKSPACE_FILE_DOWNLOAD_MAX_BYTES,
        timeout=15,
    )


def remote_workspace_file_read(source: dict, value: str) -> dict:
    preview = workspace_file_preview_info(Path(value).name)
    if not preview:
        raise ValueError("Preview is limited to code, Markdown, and HTML files")
    response = remote_workspace_file_request(
        source,
        "read",
        value,
        maximum=WORKSPACE_FILE_PREVIEW_MAX_BYTES,
        timeout=20,
    )
    content = str(response.get("content", ""))
    return {
        **response,
        **preview,
        "content": content,
        "line_count": content.count("\n") + 1,
    }


def remote_workspace_file_download(source: dict, value: str) -> tuple[dict, bytes]:
    response = remote_workspace_file_request(
        source,
        "download",
        value,
        maximum=WORKSPACE_FILE_DOWNLOAD_MAX_BYTES,
        timeout=45,
    )
    encoded = response.pop("data", "")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, TypeError) as error:
        raise ValueError("Remote workspace returned invalid file data") from error
    if len(data) != int(response.get("size", -1)):
        raise ValueError("Remote workspace returned incomplete file data")
    return response, data


def workspace_directory_listing_for_source(
    source_id: str | None,
    value: str | None = None,
) -> dict:
    source = agent_source(source_id)
    if source["kind"] == "local":
        listing = workspace_directory_listing(value)
    else:
        listing = remote_workspace_directory_listing(source, value)
    return {
        "type": "directory_listing",
        **listing,
        "source_id": source["id"],
        "source_label": source["label"],
    }


def workspace_file_listing_for_source(
    source_id: str | None,
    value: str | None = None,
    include_terminal_profiles: bool = False,
) -> dict:
    source = workspace_source(
        source_id,
        include_terminal_profiles=include_terminal_profiles,
    )
    if source["kind"] == "local":
        listing = workspace_file_listing(value)
    else:
        listing = remote_workspace_file_listing(source, value)
    return {
        "type": "workspace_listing",
        **listing,
        "source_id": source["id"],
        "source_label": source["label"],
    }


def workspace_file_read_for_source(
    source_id: str | None,
    value: str,
    include_terminal_profiles: bool = False,
) -> dict:
    source = workspace_source(
        source_id,
        include_terminal_profiles=include_terminal_profiles,
    )
    if source["kind"] == "local":
        result = workspace_file_read(value)
    else:
        result = remote_workspace_file_read(source, value)
    return {
        "type": "workspace_file",
        **result,
        "source_id": source["id"],
        "source_label": source["label"],
    }


def workspace_file_metadata_for_source(
    source_id: str | None,
    value: str,
    include_terminal_profiles: bool = False,
) -> dict:
    source = workspace_source(
        source_id,
        include_terminal_profiles=include_terminal_profiles,
    )
    if source["kind"] == "local":
        result = workspace_file_metadata(value)
        if result["size"] > WORKSPACE_FILE_DOWNLOAD_MAX_BYTES:
            raise ValueError("Workspace file is too large to download")
    else:
        result = remote_workspace_file_metadata(source, value)
    return {**result, "source_id": source["id"], "source_label": source["label"]}


def workspace_file_download_for_source(
    source_id: str | None,
    value: str,
    include_terminal_profiles: bool = False,
) -> tuple[dict, bytes]:
    source = workspace_source(
        source_id,
        include_terminal_profiles=include_terminal_profiles,
    )
    if source["kind"] == "local":
        metadata, data = workspace_file_download(value)
    else:
        metadata, data = remote_workspace_file_download(source, value)
    return {
        **metadata,
        "source_id": source["id"],
        "source_label": source["label"],
    }, data


def validated_workspace_upload_name(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Upload file name must be text")
    name = value
    if (
        not name.strip()
        or name in {".", ".."}
        or name.startswith(".")
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("Upload file name is not allowed")
    if len(name.encode("utf-8")) > 255:
        raise ValueError("Upload file name is too long")
    return name


def workspace_upload_candidate_name(name: str, index: int) -> str:
    if index <= 0:
        return name
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    return f"{stem} ({index}){suffix}"


def commit_local_workspace_upload(
    staging_path: Path,
    directory: Path,
    name: str,
    size: int,
    overwrite: bool,
) -> dict:
    if overwrite:
        target = directory / name
        try:
            existing = target.lstat()
        except FileNotFoundError:
            existing = None
        except OSError as error:
            raise ValueError("Workspace upload target cannot be accessed") from error
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            raise ValueError("A directory already uses the upload file name")
        try:
            os.replace(staging_path, target)
        except OSError as error:
            raise ValueError("Workspace upload could not replace the target file") from error
    else:
        target = None
        for index in range(10_000):
            candidate = directory / workspace_upload_candidate_name(name, index)
            try:
                os.link(staging_path, candidate)
            except FileExistsError:
                continue
            except OSError as error:
                raise ValueError("Workspace upload could not create the target file") from error
            staging_path.unlink()
            target = candidate
            break
        if target is None:
            raise ValueError("Too many files use the same upload name")

    return {
        "path": str(target),
        "display_path": display_workspace_path(target),
        "name": target.name,
        "size": size,
    }


REMOTE_WORKSPACE_UPLOAD_SCRIPT = r'''
import json
import os
import stat
import sys
import tempfile

def display(path):
    home = os.path.realpath(os.path.expanduser("~"))
    if path == home:
        return "~"
    prefix = home + os.sep
    return "~/" + path[len(prefix):] if path.startswith(prefix) else path

def within(path, roots):
    for root in roots:
        try:
            if os.path.commonpath((path, root)) == root:
                return True
        except ValueError:
            pass
    return False

def fail(message):
    print(json.dumps({"error": message}))
    raise SystemExit(2)

try:
    configured = json.loads(sys.argv[1])
    requested = sys.argv[2]
    name = sys.argv[3]
    overwrite = sys.argv[4] == "1"
    expected = int(sys.argv[5])
except (IndexError, json.JSONDecodeError, TypeError, ValueError):
    fail("Remote workspace upload request is invalid")

if (
    not name.strip()
    or name in {".", ".."}
    or name.startswith(".")
    or "/" in name
    or "\\" in name
    or any(ord(character) < 32 or ord(character) == 127 for character in name)
    or len(name.encode("utf-8")) > 255
    or expected < 0
):
    fail("Remote workspace upload file name is not allowed")

roots = []
for value in configured:
    root = os.path.realpath(os.path.expanduser(value))
    if os.path.isdir(root) and root not in roots:
        roots.append(root)
if not roots:
    fail("No remote workspace roots are available")

candidate = os.path.expanduser(requested)
if not os.path.isabs(candidate):
    candidate = os.path.join(roots[0], candidate)
lexical = os.path.abspath(candidate)
root = next((item for item in roots if within(lexical, [item])), None)
if root is None:
    fail("Remote workspace upload directory is outside the configured roots")
relative = os.path.relpath(lexical, root)
current = root
if relative != ".":
    for component in relative.split(os.sep):
        if component.startswith("."):
            fail("Hidden remote workspace paths cannot be accessed")
        current = os.path.join(current, component)
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            fail("Remote workspace upload directory does not exist")
        if stat.S_ISLNK(mode):
            fail("Remote workspace symbolic links cannot be accessed")
directory = os.path.realpath(lexical)
if not os.path.isdir(directory) or not within(directory, roots):
    fail("Remote workspace upload directory is unavailable")

descriptor = -1
temporary = ""
try:
    descriptor, temporary = tempfile.mkstemp(prefix=".herdr-upload-", dir=directory)
    received = 0
    with os.fdopen(descriptor, "wb") as handle:
        descriptor = -1
        while True:
            chunk = sys.stdin.buffer.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > expected:
                fail("Remote workspace upload exceeded its declared size")
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    if received != expected or os.stat(temporary).st_size != expected:
        fail("Remote workspace upload is incomplete")

    suffix = os.path.splitext(name)[1]
    stem = name[:-len(suffix)] if suffix else name
    target = ""
    if overwrite:
        target = os.path.join(directory, name)
        try:
            existing = os.lstat(target)
        except FileNotFoundError:
            existing = None
        except OSError:
            fail("Remote workspace upload target cannot be accessed")
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            fail("A remote directory already uses the upload file name")
        os.replace(temporary, target)
        temporary = ""
    else:
        for index in range(10_000):
            candidate_name = name if index == 0 else f"{stem} ({index}){suffix}"
            candidate_path = os.path.join(directory, candidate_name)
            try:
                os.link(temporary, candidate_path)
            except FileExistsError:
                continue
            target = candidate_path
            os.unlink(temporary)
            temporary = ""
            break
        if not target:
            fail("Too many remote files use the same upload name")

    print(json.dumps({
        "path": target,
        "display_path": display(target),
        "name": os.path.basename(target),
        "size": received,
    }))
except SystemExit:
    raise
except OSError:
    fail("Remote workspace upload could not write the target file")
finally:
    if descriptor >= 0:
        os.close(descriptor)
    if temporary:
        try:
            os.unlink(temporary)
        except OSError:
            pass
'''


def remote_workspace_upload_file(
    source: dict,
    staging_path: Path,
    directory: str,
    name: str,
    size: int,
    overwrite: bool,
) -> dict:
    command = ssh_command_prefix(connect_timeout=10)
    if source.get("port", 22) != 22:
        command.extend(["-p", str(source["port"])])
    command.extend([
        source["target"],
        shlex.join([
            "python3",
            "-c",
            REMOTE_WORKSPACE_UPLOAD_SCRIPT,
            json.dumps(
                source.get("workspace_roots")
                or [source.get("workspace_root", "~")]
            ),
            directory,
            name,
            "1" if overwrite else "0",
            str(size),
        ]),
    ])
    timeout = max(60, min(7200, 60 + size // (512 * 1024)))
    try:
        with staging_path.open("rb") as stream:
            result = subprocess.run(
                command,
                stdin=stream,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("Remote workspace upload could not be transferred") from error
    raw_stdout = result.stdout or b""
    if isinstance(raw_stdout, bytes):
        raw_stdout = raw_stdout.decode("utf-8", errors="replace")
    try:
        response = json.loads(str(raw_stdout))
    except (json.JSONDecodeError, TypeError) as error:
        if result.returncode == 255:
            raise ValueError(
                "Remote Files source is offline or requires SSH key authentication"
            ) from error
        if result.returncode in {126, 127}:
            raise ValueError("Remote Files source requires Python 3") from error
        raise ValueError("Remote workspace upload returned an invalid response") from error
    if not isinstance(response, dict):
        raise ValueError("Remote workspace upload returned an invalid response")
    if result.returncode != 0 or response.get("error"):
        raise ValueError(str(response.get("error") or "Remote workspace upload failed"))
    try:
        response_size = int(response.get("size", -1))
        response_name = validated_workspace_upload_name(response.get("name"))
    except (TypeError, ValueError) as error:
        raise ValueError("Remote workspace upload returned invalid metadata") from error
    response_path = response.get("path")
    response_display_path = response.get("display_path") or response_path
    if (
        response_size != size
        or not isinstance(response_path, str)
        or not response_path
        or not isinstance(response_display_path, str)
        or not response_display_path
    ):
        raise ValueError("Remote workspace upload returned invalid metadata")
    return {
        "path": response_path,
        "display_path": response_display_path,
        "name": response_name,
        "size": response_size,
    }


class WorkspaceUpload:
    """One bounded, sequential browser upload into a workspace directory."""

    def __init__(
        self,
        source: dict,
        directory: str,
        name: str,
        size: int,
        overwrite: bool,
    ):
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= WORKSPACE_UPLOAD_MAX_BYTES
        ):
            raise ValueError("Workspace upload file size is invalid or exceeds the limit")
        self.source = source
        self.name = validated_workspace_upload_name(name)
        self.size = size
        self.overwrite = overwrite is True
        self.received = 0
        self.finished = False
        self.directory: Path | str
        if source.get("kind") == "local":
            self.directory = resolve_workspace_file_path(directory, expected="directory")
            descriptor, temporary = tempfile.mkstemp(
                prefix=".herdr-upload-",
                dir=self.directory,
            )
        else:
            if not isinstance(directory, str) or not directory.strip():
                raise ValueError("A remote workspace upload directory is required")
            listing = remote_workspace_file_request(
                source,
                "list",
                directory,
                timeout=20,
            )
            resolved_directory = str(listing.get("path") or "")
            if not resolved_directory:
                raise ValueError("A concrete remote workspace directory is required")
            self.directory = resolved_directory
            descriptor, temporary = tempfile.mkstemp(prefix="herdr-workspace-upload-")
        self.staging_path = Path(temporary)
        self.stream = os.fdopen(descriptor, "wb")

    def write(self, offset: int, data: bytes) -> int:
        if self.finished or self.stream.closed:
            raise ValueError("Workspace upload is no longer active")
        if offset != self.received:
            raise ValueError("Workspace upload chunk offset does not match")
        if not data or len(data) > WORKSPACE_UPLOAD_CHUNK_BYTES:
            raise ValueError("Workspace upload chunk is invalid")
        if self.received + len(data) > self.size:
            raise ValueError("Workspace upload exceeds its declared size")
        written = self.stream.write(data)
        if written != len(data):
            raise ValueError("Workspace upload chunk could not be written completely")
        self.received += len(data)
        return self.received

    def finish(self) -> dict:
        if self.finished:
            raise ValueError("Workspace upload is no longer active")
        self.finished = True
        try:
            if self.received != self.size:
                raise ValueError("Workspace upload is incomplete")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            try:
                staged_size = self.staging_path.stat().st_size
            except OSError as error:
                raise ValueError("Workspace upload staging file cannot be verified") from error
            if staged_size != self.size:
                raise ValueError("Workspace upload staging file size does not match")
            if self.source.get("kind") == "local":
                result = commit_local_workspace_upload(
                    self.staging_path,
                    self.directory,
                    self.name,
                    self.size,
                    self.overwrite,
                )
            else:
                result = remote_workspace_upload_file(
                    self.source,
                    self.staging_path,
                    self.directory,
                    self.name,
                    self.size,
                    self.overwrite,
                )
        finally:
            if not self.stream.closed:
                with contextlib.suppress(OSError):
                    self.stream.close()
            with contextlib.suppress(OSError):
                self.staging_path.unlink()
        return {
            **result,
            "source_id": self.source["id"],
            "source_label": self.source["label"],
        }

    def cancel(self) -> None:
        self.finished = True
        if not self.stream.closed:
            with contextlib.suppress(OSError):
                self.stream.close()
        with contextlib.suppress(OSError):
            self.staging_path.unlink()


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


def codex_agent_name(workspace_id: str) -> str:
    """Return a stable agent name that is unique within the Herdr session."""
    suffix = re.sub(r"[^a-z0-9_-]+", "-", workspace_id.casefold()).strip("-_")
    if not suffix:
        raise RuntimeError("Herdr returned an invalid workspace ID")
    return f"codex-{suffix}"


def start_codex_in_pane(pane_id: str, workspace_id: str, remote=None) -> dict:
    """Start Codex once a newly created pane reaches its shell prompt."""
    agent_name = codex_agent_name(workspace_id)
    deadline = time.monotonic() + AGENT_START_PANE_READY_TIMEOUT_SECONDS
    while True:
        arguments = [
            "agent", "start", agent_name,
            "--kind", "codex",
            "--pane", pane_id,
            "--timeout", "60000",
        ]
        if remote:
            started = run_herdr_result(*arguments, remote=remote, timeout=75)
        else:
            started = run_herdr_result(*arguments, timeout=75)
        if started.returncode == 0:
            return parse_herdr_result(started)
        if herdr_error_code(started) != "agent_pane_busy":
            return parse_herdr_result(started)
        if time.monotonic() >= deadline:
            raise RuntimeError("Herdr pane did not become ready for Codex")
        time.sleep(AGENT_START_PANE_READY_INTERVAL_SECONDS)


def get_agent_info(pane_id: str, remote=None) -> dict:
    result = run_herdr_result("agent", "get", pane_id, remote=remote, timeout=5)
    if result.returncode != 0:
        raise RuntimeError("Could not inspect agent state")
    response = herdr_json_response(result)
    agent = (response.get("result") or {}).get("agent")
    if not isinstance(agent, dict):
        raise RuntimeError("Herdr returned an invalid agent state")
    return agent


def mark_agent_seen(pane_id: str, remote=None) -> tuple[dict, bool]:
    """Mark a completed Agent as seen without focusing other Agent states.

    Herdr's agent.view.clear API clears an Agent View definition; agent focus
    is the operation that acknowledges a completed Agent.
    """
    before = get_agent_info(pane_id, remote=remote)
    if str(before.get("agent_status", "unknown")).casefold() != "done":
        return before, False

    focused = run_herdr_result(
        "agent", "focus", pane_id,
        remote=remote,
        timeout=5,
    )
    if focused.returncode != 0:
        raise RuntimeError("Could not mark agent as seen")
    return get_agent_info(pane_id, remote=remote), True


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


def submit_agent_prompt(pane_id: str, text: str, remote=None) -> str:
    """Submit a prompt and return confirmed or queued delivery semantics."""
    before = get_agent_info(pane_id, remote=remote)
    if str(before.get("agent_status", "unknown")).casefold() == "working":
        # Herdr cannot attribute the next state transition to a prompt queued
        # while an existing turn is working. Waiting here can falsely time out
        # even though the semantic prompt API accepted the new task.
        queued = run_herdr_result(
            "agent", "prompt", pane_id, text,
            remote=remote,
            timeout=5,
        )
        if queued.returncode != 0:
            raise RuntimeError("Herdr rejected the queued agent prompt")
        return "queued"

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
        return "confirmed"

    current = get_agent_info(pane_id, remote=remote)
    if agent_state_advanced(before, current):
        # Herdr may time out waiting for one of the requested settled states
        # after already observing that submission changed the agent state.
        return "confirmed"
    if herdr_error_code(result) != "agent_prompt_stalled":
        raise RuntimeError("Herdr rejected the agent prompt")

    status = str(current.get("agent_status", "unknown")).casefold()
    if status != "idle" or current.get("interactive_ready") is not True:
        raise RuntimeError("Agent prompt did not start")

    entered = run_herdr_result(
        "agent", "send-keys", pane_id, "Enter",
        remote=remote,
        timeout=5,
    )
    if entered.returncode != 0:
        raise RuntimeError("Could not finish submitting the agent prompt")

    for attempt in range(AGENT_PROMPT_CONFIRM_ATTEMPTS):
        observed = get_agent_info(pane_id, remote=remote)
        if agent_state_advanced(before, observed):
            return "confirmed"
        if attempt + 1 < AGENT_PROMPT_CONFIRM_ATTEMPTS:
            time.sleep(AGENT_PROMPT_CONFIRM_INTERVAL_SECONDS)
    raise RuntimeError("Agent prompt did not start after Enter")


def cache_agent_prompt_with_tab(pane_id: str, text: str, remote=None) -> str:
    """Type a prompt and press Tab so Codex queues it after the active turn."""
    agent = get_agent_info(pane_id, remote=remote)
    if str(agent.get("agent", "")).casefold() != "codex":
        raise ValueError("Tab cache is only available for Codex agents")
    if str(agent.get("agent_status", "unknown")).casefold() != "working":
        raise ValueError("Agent is no longer working; use Send Prompt instead")

    # Keep the text and Tab in one terminal write so another controller cannot
    # interleave input between them. Herdr send-text preserves the Tab byte.
    result = run_herdr_result(
        "pane", "send-text", pane_id, text + "\t",
        remote=remote,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not cache the agent prompt")
    return "cached"


def prompt_image_matches_media_type(data: bytes, media_type: str) -> bool:
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def decode_prompt_images(value) -> list[dict]:
    if value in (None, []):
        return []
    if not isinstance(value, list) or len(value) > PROMPT_IMAGE_MAX_COUNT:
        raise ValueError(f"Prompt images must contain at most {PROMPT_IMAGE_MAX_COUNT} files")

    decoded = []
    total_bytes = 0
    maximum_encoded_bytes = ((PROMPT_IMAGE_MAX_BYTES + 2) // 3) * 4 + 4
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Prompt image is invalid")
        media_type = str(item.get("media_type") or "").casefold()
        encoded = item.get("data")
        if media_type not in PROMPT_IMAGE_EXTENSIONS or not isinstance(encoded, str):
            raise ValueError("Prompt image type is not supported")
        if not encoded or len(encoded) > maximum_encoded_bytes:
            raise ValueError("Prompt image is too large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Prompt image data is invalid") from error
        if not data or len(data) > PROMPT_IMAGE_MAX_BYTES:
            raise ValueError("Prompt image is too large")
        if not prompt_image_matches_media_type(data, media_type):
            raise ValueError("Prompt image content does not match its type")
        total_bytes += len(data)
        if total_bytes > PROMPT_IMAGE_TOTAL_MAX_BYTES:
            raise ValueError("Prompt images are too large")
        decoded.append({"media_type": media_type, "data": data})
    return decoded


def codex_session_id(agent: dict) -> str:
    if str(agent.get("agent") or "").casefold() != "codex":
        raise ValueError("Image prompts are only available for Codex agents")
    session = agent.get("agent_session") or {}
    value = session.get("value") if isinstance(session, dict) else ""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value)
    ):
        raise ValueError("Codex session is not available yet; refresh and try again")
    return value


class CodexAppServerUnavailable(RuntimeError):
    """Raised when an older Codex host cannot accept app-server requests."""


def codex_binary(remote=None) -> str:
    return (
        remote.get("codex_bin", REMOTE_CODEX_BIN)
        if isinstance(remote, dict)
        else (REMOTE_CODEX_BIN if remote else CODEX)
    )


def codex_queue_arguments(session_id: str, text: str, image_paths: list[str], remote=None) -> list[str]:
    binary = codex_binary(remote)
    arguments = [binary, "queue", "--thread", session_id, "--message", text]
    for path in image_paths:
        arguments.extend(["--image", path])
    return arguments


def codex_prompt_input(text: str, images: list[dict]) -> list[dict]:
    items = [{"type": "text", "text": text}]
    for image in images:
        encoded = base64.b64encode(image["data"]).decode("ascii")
        items.append({
            "type": "image",
            "url": f"data:{image['media_type']};base64,{encoded}",
        })
    return items


def codex_app_server_output_reader(stream, messages: thread_queue.Queue) -> None:
    try:
        for line in stream:
            messages.put(line)
    except (OSError, ValueError):
        pass
    finally:
        messages.put(None)


def codex_app_server_method_unavailable(error: dict) -> bool:
    code = error.get("code")
    message = str(error.get("message") or "").casefold()
    return code == -32601 or any(marker in message for marker in (
        "method not found",
        "unknown method",
        "does not support thread/queue/add",
    ))


def read_codex_app_server_response(
    messages: thread_queue.Queue,
    request_id: str,
    timeout: float,
    *,
    unavailable_on_failure: bool = False,
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if unavailable_on_failure:
                raise CodexAppServerUnavailable("Codex app-server did not respond")
            raise RuntimeError("Codex app-server timed out while queuing the image prompt")
        try:
            line = messages.get(timeout=remaining)
        except thread_queue.Empty as error:
            if unavailable_on_failure:
                raise CodexAppServerUnavailable("Codex app-server did not respond") from error
            raise RuntimeError("Codex app-server timed out while queuing the image prompt") from error
        if line is None:
            if unavailable_on_failure:
                raise CodexAppServerUnavailable("Codex app-server exited before initialization")
            raise RuntimeError("Codex app-server exited before queuing the image prompt")
        try:
            response = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(response, dict) or response.get("id") != request_id:
            continue
        error = response.get("error")
        if isinstance(error, dict):
            if unavailable_on_failure or codex_app_server_method_unavailable(error):
                raise CodexAppServerUnavailable("Codex app-server image queue is unavailable")
            raise RuntimeError("Codex app-server rejected the image prompt")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Codex app-server returned an invalid image prompt response")
        return result


def write_codex_app_server_message(process, payload: dict, *, unavailable=False) -> None:
    if process.stdin is None:
        if unavailable:
            raise CodexAppServerUnavailable("Codex app-server input is unavailable")
        raise RuntimeError("Codex app-server input is unavailable")
    try:
        process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError, ValueError) as error:
        if unavailable:
            raise CodexAppServerUnavailable("Codex app-server input closed unexpectedly") from error
        raise RuntimeError("Codex app-server input closed unexpectedly") from error


def stop_codex_app_server(process) -> None:
    if process.poll() is None:
        with contextlib.suppress(OSError):
            process.terminate()
    if process.stdin is not None:
        with contextlib.suppress(BrokenPipeError, OSError, ValueError):
            process.stdin.close()
    try:
        process.wait(timeout=CODEX_APP_SERVER_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=CODEX_APP_SERVER_STOP_TIMEOUT_SECONDS)
    if process.stdout is not None:
        with contextlib.suppress(OSError, ValueError):
            process.stdout.close()


def submit_codex_app_server_prompt(
    session_id: str,
    text: str,
    images: list[dict],
    remote=None,
) -> None:
    arguments = [codex_binary(remote), "app-server", "--stdio"]
    command = remote_command_arguments(remote, arguments) if remote else arguments
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        raise CodexAppServerUnavailable("Could not start Codex app-server") from error
    if process.stdout is None:
        stop_codex_app_server(process)
        raise CodexAppServerUnavailable("Codex app-server output is unavailable")

    messages = thread_queue.Queue()
    reader = threading.Thread(
        target=codex_app_server_output_reader,
        args=(process.stdout, messages),
        name="herdr-codex-app-server",
        daemon=True,
    )
    reader.start()
    initialize_id = "herdr-initialize"
    queue_id = f"herdr-queue-{secrets.token_hex(8)}"
    try:
        write_codex_app_server_message(process, {
            "id": initialize_id,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "herdr-remote",
                    "version": "1",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "optOutNotificationMethods": [
                        "item/agentMessage/delta",
                        "item/reasoning/summaryTextDelta",
                        "item/reasoning/textDelta",
                    ],
                },
            },
        }, unavailable=True)
        read_codex_app_server_response(
            messages,
            initialize_id,
            CODEX_APP_SERVER_INITIALIZE_TIMEOUT_SECONDS,
            unavailable_on_failure=True,
        )
        write_codex_app_server_message(process, {"method": "initialized"}, unavailable=True)
        write_codex_app_server_message(process, {
            "id": queue_id,
            "method": "thread/queue/add",
            "params": {
                "threadId": session_id,
                "clientUserMessageId": f"herdr-{secrets.token_hex(16)}",
                "input": codex_prompt_input(text, images),
            },
        })
        result = read_codex_app_server_response(
            messages,
            queue_id,
            CODEX_APP_SERVER_QUEUE_TIMEOUT_SECONDS,
        )
    finally:
        # The queue response means the image data is durably embedded. Stop the
        # short-lived client immediately so the already-running Codex TUI owns
        # execution of the queued turn.
        stop_codex_app_server(process)

    submission = result.get("queuedSubmission")
    queued_input = submission.get("input") if isinstance(submission, dict) else None
    if not isinstance(queued_input, list):
        raise RuntimeError("Codex app-server did not confirm the image prompt")
    queued_images = sum(
        item.get("type") in {"image", "localImage"}
        for item in queued_input
        if isinstance(item, dict)
    )
    if queued_images != len(images):
        raise RuntimeError("Codex app-server did not retain every prompt image")


def write_remote_prompt_image(remote, path: str, data: bytes):
    result = run_remote_result(
        remote,
        ["sh", "-c", 'umask 077; cat > "$1"', "herdr-image-upload", path],
        timeout=20,
        input_data=data,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not upload prompt image to the Agent host")


def submit_codex_image_prompt_legacy(
    session_id: str,
    text: str,
    images: list[dict],
    remote=None,
) -> None:
    if not remote:
        with tempfile.TemporaryDirectory(prefix="herdr-prompt-images-") as directory:
            paths = []
            for index, image in enumerate(images, start=1):
                path = Path(directory) / f"image-{index}{PROMPT_IMAGE_EXTENSIONS[image['media_type']]}"
                path.write_bytes(image["data"])
                paths.append(str(path))
            result = subprocess.run(
                codex_queue_arguments(session_id, text, paths),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("Codex rejected the image prompt")
        return

    remote_directory = f"/tmp/herdr-prompt-images-{secrets.token_hex(12)}"
    remote_paths = [
        f"{remote_directory}/image-{index}{PROMPT_IMAGE_EXTENSIONS[image['media_type']]}"
        for index, image in enumerate(images, start=1)
    ]
    created = run_remote_result(remote, ["mkdir", "-m", "700", remote_directory], timeout=10)
    if created.returncode != 0:
        raise RuntimeError("Could not prepare prompt images on the Agent host")
    try:
        for path, image in zip(remote_paths, images):
            write_remote_prompt_image(remote, path, image["data"])
        result = run_remote_result(
            remote,
            codex_queue_arguments(session_id, text, remote_paths, remote=remote),
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError("Codex rejected the image prompt")
    finally:
        run_remote_result(remote, ["rm", "-f", *remote_paths], timeout=10)
        run_remote_result(remote, ["rmdir", remote_directory], timeout=10)


def submit_codex_image_prompt(pane_id: str, text: str, images: list[dict], remote=None) -> str:
    if not images:
        raise ValueError("At least one prompt image is required")
    agent = get_agent_info(pane_id, remote=remote)
    session_id = codex_session_id(agent)
    message = text.strip() or "请查看并分析附加图片。"
    status = str(agent.get("agent_status", "unknown")).casefold()
    if status == "blocked":
        raise ValueError("Agent is blocked; answer the current prompt before sending images")
    delivery = (
        "queued"
        if status == "working"
        else "confirmed"
    )
    try:
        submit_codex_app_server_prompt(session_id, message, images, remote=remote)
    except CodexAppServerUnavailable:
        # Codex versions predating thread/queue/add may still support the CLI
        # --image path used by the original implementation.
        submit_codex_image_prompt_legacy(session_id, message, images, remote=remote)
    return delivery


def start_codex_on_source(cwd: str, prompt: str, source: dict) -> dict:
    if not isinstance(prompt, str):
        raise ValueError("Prompt must be text")

    remote = source if source["kind"] != "local" else None
    if remote:
        listing = remote_workspace_directory_listing(source, cwd)
        if not listing.get("can_start_agent") or not listing.get("path"):
            raise ValueError("Remote workspace directory cannot start an Agent")
        directory_path = str(listing["path"])
        display_path = str(listing.get("display_path") or directory_path)
    else:
        directory = resolve_workspace_path(cwd)
        directory_path = str(directory)
        display_path = display_workspace_path(directory)

    label = os.path.basename(directory_path.rstrip("/"))[:80] or "codex"
    workspace_id = ""
    try:
        create_arguments = [
            "workspace", "create",
            "--cwd", directory_path,
            "--label", label,
            "--no-focus",
        ]
        if remote:
            created = run_herdr_result(*create_arguments, remote=remote, timeout=20)
        else:
            created = run_herdr_result(*create_arguments, timeout=20)
        created_result = parse_herdr_result(created)
        workspace = created_result.get("workspace") or {}
        root_pane = created_result.get("root_pane") or {}
        workspace_id = workspace.get("workspace_id", "")
        pane_id = root_pane.get("pane_id", "")
        if not workspace_id or not pane_id:
            raise RuntimeError("Herdr did not return the new workspace and pane IDs")

        started_result = start_codex_in_pane(pane_id, workspace_id, remote=remote)
        agent = started_result.get("agent") or {}
        if agent.get("pane_id") != pane_id:
            raise RuntimeError("Herdr returned an unexpected agent pane")

        prompted = False
        prompt_warning = ""
        if prompt:
            try:
                if remote:
                    submit_agent_prompt(pane_id, prompt, remote=remote)
                else:
                    submit_agent_prompt(pane_id, prompt)
            except Exception:
                prompt_warning = "Codex started, but the initial prompt was not accepted"
            else:
                prompted = True

        global_pane_id = public_pane_id(source["id"], pane_id)
        return {
            "pane_id": global_pane_id,
            "raw_pane_id": pane_id,
            "source_id": source["id"],
            "workspace_id": workspace_id,
            "cwd": directory_path,
            "display_path": display_path,
            "project": label,
            "agent": "codex",
            "status": agent.get("agent_status", "idle"),
            "host": "local" if source["kind"] == "local" else source["label"],
            "prompted": prompted,
            "warning": prompt_warning,
        }
    except Exception:
        if workspace_id:
            try:
                if remote:
                    run_herdr_result(
                        "workspace", "close", workspace_id,
                        remote=remote,
                        timeout=15,
                    )
                else:
                    run_herdr_result("workspace", "close", workspace_id, timeout=15)
            except Exception:
                pass
        raise


def start_local_codex(cwd: str, prompt: str = "") -> dict:
    return start_codex_on_source(cwd, prompt, local_agent_source())


def source_status(source: dict, status: str, *, error: str = "", agent_count: int = 0) -> dict:
    return {
        "id": source["id"],
        "kind": source["kind"],
        "label": source["label"],
        "status": status,
        "error": error,
        "agent_count": agent_count,
        "can_browse": status == "online",
        "can_start_agent": status == "online",
    }


def agent_source_snapshot() -> list[dict]:
    return [
        agent_source_cache.get(source["id"])
        or source_status(source, "unknown", error="Waiting for health check")
        for source in configured_agent_sources()
    ]


def get_workspace_labels(remote=None) -> dict[str, str]:
    """Map workspace IDs to the names selected in Herdr."""
    raw = run_herdr("workspace", "list", remote=remote)
    try:
        workspaces = json.loads(raw).get("result", {}).get("workspaces", [])
    except (AttributeError, json.JSONDecodeError, TypeError):
        return {}
    return {
        item["workspace_id"]: item.get("label", "")
        for item in workspaces
        if isinstance(item, dict) and item.get("workspace_id") and item.get("label")
    }


def get_agents_from_source(source: dict) -> tuple[list[dict], dict]:
    remote = source if source["kind"] != "local" else None
    try:
        result = run_herdr_result("pane", "list", remote=remote, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return [], source_status(source, "offline", error="SSH or Herdr is unavailable")
    if result.returncode != 0:
        error = "SSH is unavailable" if result.returncode == 255 else "Herdr is unavailable"
        return [], source_status(source, "offline", error=error)
    try:
        data = json.loads(result.stdout)
        panes = data.get("result", {}).get("panes", [])
        workspace_labels = get_workspace_labels(remote=remote) if panes else {}
        agents = [
            {
                "pane_id": public_pane_id(source["id"], p["pane_id"]),
                "raw_pane_id": p["pane_id"],
                "source_id": source["id"],
                "agent": p.get("agent", ""),
                "label": p.get("label", ""),
                "workspace_label": workspace_labels.get(p.get("workspace_id", ""), ""),
                "status": p.get("agent_status", "unknown"),
                "cwd": p.get("cwd", ""),
                "project": os.path.basename(p.get("cwd", "")),
                "host": "local" if source["kind"] == "local" else source["label"],
                "workspace_id": p.get("workspace_id", ""),
                "tab_id": p.get("tab_id", ""),
            }
            for p in panes if p.get("agent")
        ]
        return agents, source_status(source, "online", agent_count=len(agents))
    except (AttributeError, json.JSONDecodeError, KeyError, TypeError):
        return [], source_status(source, "offline", error="Herdr returned invalid data")


def get_agents_from_host(remote=None):
    source = local_agent_source() if remote is None else (
        remote if isinstance(remote, dict) else legacy_agent_source(remote)
    )
    return get_agents_from_source(source)[0]


def get_all_agents():
    agents = []
    for source in configured_agent_sources():
        agents.extend(get_agents_from_source(source)[0])
    return agents


async def collect_all_agents() -> tuple[list[dict], list[dict], dict[str, dict]]:
    sources = configured_agent_sources()
    results = await asyncio.gather(*(
        asyncio.to_thread(get_agents_from_source, source)
        for source in sources
    ))
    agents = []
    statuses = []
    source_map = {source["id"]: source for source in sources}
    for source_agents, status in results:
        agents.extend(source_agents)
        statuses.append(status)
    return agents, statuses, source_map


def update_pane_maps(agents: list[dict], source_map: dict[str, dict] | None = None):
    current_pane_ids = {agent["pane_id"] for agent in agents}
    for agent in agents:
        pane_id = agent["pane_id"]
        raw_pane_id = agent.get("raw_pane_id", pane_id)
        if "remote" in agent:
            remote = agent.get("remote")
        else:
            source_id = str(agent.get("source_id") or "local")
            source = (source_map or {}).get(source_id)
            if source is None:
                try:
                    source = agent_source(source_id)
                except ValueError:
                    source = local_agent_source()
            remote = source if source["kind"] != "local" else None
        pane_remote_map[pane_id] = remote
        pane_raw_map[pane_id] = raw_pane_id
        known_panes.add(pane_id)
        agent_cache[pane_id] = agent

    stale = known_panes - current_pane_ids
    if stale:
        known_panes.difference_update(stale)
        for pane_id in stale:
            pane_remote_map.pop(pane_id, None)
            pane_raw_map.pop(pane_id, None)
            last_statuses.pop(pane_id, None)
            last_blocked_prompts.pop(pane_id, None)
            agent_cache.pop(pane_id, None)


def pane_target(pane_id: str, remote=None) -> tuple[str, dict | str | None]:
    if pane_id in pane_raw_map or pane_id in pane_remote_map:
        return pane_route(pane_id)
    return pane_id, remote


def read_pane(pane_id, remote=None):
    raw = run_herdr("pane", "read", pane_id, "--lines", "100", "--source", "recent", remote=remote)
    lines = [line for line in raw.splitlines() if line.strip() and not CHROME_RE.search(line)]
    display_lines = lines[-50:]
    question = detect_question("\n".join(lines))
    if question and question["text"] and question["text"] not in display_lines:
        option_start = next(
            (
                index for index in range(len(display_lines) - 1, -1, -1)
                if QUESTION_OPTION_RE.match(
                    display_lines[index].strip().strip("│|").strip()
                )
            ),
            None,
        )
        if option_start is not None:
            while option_start > 0 and QUESTION_OPTION_RE.match(
                display_lines[option_start - 1].strip().strip("│|").strip()
            ):
                option_start -= 1
        else:
            option_start = 0
        display_lines.insert(option_start, question["text"])
    return "\n".join(display_lines)


def detect_question(text):
    blocks = []
    current = []
    current_start = None
    lines = text.splitlines()
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("│|").strip()
        match = QUESTION_OPTION_RE.match(line)
        if not match:
            if current:
                blocks.append((current_start, current))
                current = []
                current_start = None
            continue
        if current_start is None:
            current_start = line_index
        marker = match.group("marker")
        current.append({
            "label": match.group("label").strip(),
            "selected": bool(match.group("cursor")),
            "multi": marker in {
                "\uf046", "\uf096", "\uf14a", "☐", "☑", "[ ]", "[x]", "[X]",
            },
            "checked": marker in {"\uf046", "\uf14a", "☑", "[x]", "[X]"},
        })
    if current:
        blocks.append((current_start, current))

    for block_start, block in reversed(blocks):
        has_other = any(option["label"] == QUESTION_OTHER for option in block)
        has_done = any("Done selecting" in option["label"] for option in block)
        if not has_other and not has_done:
            continue
        question_lines = []
        for raw_line in reversed(lines[:block_start]):
            line = raw_line.strip().strip("│|").strip()
            if not line:
                if question_lines:
                    break
                continue
            if (
                "submit" in line.casefold()
                or re.fullmatch(r"[\W_]*ask[\W_]*", line, re.IGNORECASE)
                or not any(character.isalnum() for character in line)
            ):
                if question_lines:
                    break
                continue
            question_lines.append(line)
        return {
            "options": block,
            "selected_index": next(
                (index for index, option in enumerate(block) if option["selected"]),
                0,
            ),
            "multi": any(option["multi"] for option in block) or has_done,
            "text": " ".join(reversed(question_lines)),
        }
    return None


def detect_approval_options(text):
    lower = text.lower()
    if "yes, single permission" in lower:
        return TOOL_OPTIONS
    if "approve all pending" in lower:
        return SUBAGENT_OPTIONS
    return []


def detect_options(text):
    approval_options = detect_approval_options(text)
    if approval_options:
        return approval_options
    question = detect_question(text)
    if not question:
        return []
    return [
        option["label"]
        for option in question["options"]
        if option["label"] != QUESTION_OTHER
        and "Done selecting" not in option["label"]
    ]


def custom_editor_active(text):
    return "Enter your response:" in text or (
        "Custom answer:" in text and "submit" in text.lower()
    )


def question_prompt_id(pane_id, content):
    question = detect_question(content)
    if not question:
        normalized = " ".join(content.split())
        return hashlib.sha256(f"{pane_id}\n{normalized}".encode("utf-8")).hexdigest()[:20]
    labels = [
        option["label"]
        for option in question["options"]
        if option["label"] != QUESTION_OTHER
        and "Done selecting" not in option["label"]
    ]
    signature = json.dumps(
        {
            "pane_id": pane_id,
            "question": question["text"],
            "multi": question["multi"],
            "labels": labels,
        },
        sort_keys=True,
    )
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:20]


def prompt_matches(pane_id, prompt_id, remote=None):
    if not prompt_id:
        return False
    raw_pane_id, routed_remote = pane_target(pane_id, remote)
    content = read_pane(raw_pane_id, remote=routed_remote)
    return question_prompt_id(pane_id, content) == prompt_id


def blocked_message(
    pane_id,
    agent,
    project,
    host,
    content,
    *,
    source_id="local",
):
    question = detect_question(content) if str(agent).casefold() == "omp" else None
    options = (
        detect_options(content)
        if str(agent).casefold() == "omp"
        else detect_approval_options(content)
    )
    return {
        "type": "blocked",
        "pane_id": pane_id,
        "agent": agent,
        "project": project,
        "host": host,
        "source_id": source_id,
        "prompt": content[-500:],
        "prompt_id": question_prompt_id(pane_id, content),
        "options": [] if question and question["multi"] else options,
        "multi_options": options if question and question["multi"] else [],
        "selected_options": [
            option["label"]
            for option in question["options"]
            if option["multi"]
            and option["label"] != QUESTION_OTHER
            and "Done selecting" not in option["label"]
            and option["checked"]
        ] if question else [],
        "interaction": "omp_question" if question else "prompt",
        "multi": bool(question and question["multi"]),
        "update": False,
    }


def pane_is_omp(pane_id, remote=None):
    cached = agent_cache.get(pane_id)
    if cached:
        return str(cached.get("agent", "")).casefold() == "omp"
    return any(
        agent.get("pane_id") == pane_id
        and str(agent.get("agent", "")).casefold() == "omp"
        for agent in get_all_agents()
    )


def move_question_cursor(pane_id, question, target_index, remote=None):
    raw_pane_id, routed_remote = pane_target(pane_id, remote)
    selected_index = question["selected_index"]
    direction = "Down" if target_index >= selected_index else "Up"
    keys = [direction] * abs(target_index - selected_index)
    return not keys or _mutate_herdr(
        "pane", "send-keys", raw_pane_id, *keys, remote=routed_remote
    )


def toggle_question_option(pane_id, option_label, remote=None):
    if not pane_is_omp(pane_id, remote=remote):
        return False
    raw_pane_id, routed_remote = pane_target(pane_id, remote)
    question = detect_question(read_pane(raw_pane_id, remote=routed_remote))
    if not question or not question["multi"]:
        return False
    target_index = next((
        index
        for index, option in enumerate(question["options"])
        if option["label"].casefold() == option_label.casefold()
    ), None)
    if target_index is None or not move_question_cursor(
        pane_id, question, target_index, remote=remote
    ):
        return False
    return _mutate_herdr(
        "pane", "send-keys", raw_pane_id, "Enter", remote=routed_remote
    )


def submit_multi_question(pane_id, remote=None):
    if not pane_is_omp(pane_id, remote=remote):
        return False
    raw_pane_id, routed_remote = pane_target(pane_id, remote)
    content = read_pane(raw_pane_id, remote=routed_remote)
    question = detect_question(content)
    if not question or not question["multi"]:
        return False
    done_index = next((
        index
        for index, option in enumerate(question["options"])
        if "Done selecting" in option["label"]
    ), None)
    if done_index is not None:
        if not move_question_cursor(pane_id, question, done_index, remote=remote):
            return False
        return _mutate_herdr(
            "pane", "send-keys", raw_pane_id, "Enter", remote=routed_remote
        )
    if "Submit" in content and any(
        marker in content for marker in ("\uf14a", "\uf046", "☑", "[x]", "[X]")
    ):
        return _mutate_herdr(
            "pane", "send-keys", raw_pane_id, "Tab", "Enter", remote=routed_remote
        )
    return False


def respond_to_question(pane_id, text, question, remote=None):
    options = question["options"]
    target_index = next(
        (
            index for index, option in enumerate(options)
            if option["label"].casefold() == text.casefold()
        ),
        None,
    )
    custom_response = target_index is None
    if custom_response:
        target_index = next(
            (
                index for index, option in enumerate(options)
                if option["label"] == QUESTION_OTHER
            ),
            None,
        )
    if target_index is None:
        return False

    raw_pane_id, routed_remote = pane_target(pane_id, remote)
    selected_index = question["selected_index"]
    direction = "Down" if target_index >= selected_index else "Up"
    keys = [direction] * abs(target_index - selected_index) + ["Enter"]
    if not _mutate_herdr(
        "pane", "send-keys", raw_pane_id, *keys, remote=routed_remote
    ):
        return False
    if not custom_response:
        return True

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        editor_content = read_pane(raw_pane_id, remote=routed_remote)
        if custom_editor_active(editor_content):
            break
        time.sleep(0.05)
    else:
        return False
    return _mutate_herdr(
        "pane", "send-text", raw_pane_id, text, remote=routed_remote
    ) and _mutate_herdr(
        "pane", "send-keys", raw_pane_id, "Enter", remote=routed_remote
    )


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


async def send_current_snapshot(ws):
    # Reuse the poller's cache. Running a fresh local/SSH poll for every short
    # command connection makes Telegram replies wait on slow or offline hosts.
    await ws.send(json.dumps(sessions_message()))
    agents = list(agent_cache.values())
    statuses = agent_source_snapshot()
    await ws.send(json.dumps({"type": "agent_sources", "sources": statuses}))
    await ws.send(json.dumps({"type": "agents", "agents": agents}))
    for agent in agents:
        if agent.get("status") != "blocked":
            continue
        pane_id = agent["pane_id"]
        raw_pane_id, remote = pane_route(pane_id)
        content = read_pane(raw_pane_id, remote=remote)
        await ws.send(json.dumps(blocked_message(
            pane_id,
            agent.get("agent", ""),
            agent.get("project", ""),
            agent.get("host", "local"),
            content,
            source_id=agent.get("source_id", "local"),
        )))


async def poll_loop():
    cycle = 0
    while True:
        try:
            await _poll_once()
            if cycle % SESSION_REFRESH_EVERY == 0:
                await broadcast_sessions()
        except Exception:
            log.exception("poll cycle failed; retrying")
        cycle += 1
        await asyncio.sleep(POLL_INTERVAL)


async def _poll_once():
    generation = POLL_GENERATION
    agents, statuses, source_map = await collect_all_agents()
    if generation != POLL_GENERATION:
        return
    agent_source_cache.clear()
    agent_source_cache.update({status["id"]: status for status in statuses})
    update_pane_maps(agents, source_map)
    await broadcast({"type": "agent_sources", "sources": statuses})
    if generation != POLL_GENERATION:
        return
    await broadcast({"type": "agents", "agents": agents})
    if generation != POLL_GENERATION:
        return
    for agent in agents:
        pane_id, status = agent["pane_id"], agent["status"]
        raw_pane_id, remote = pane_route(pane_id)
        if status == "blocked":
            content = read_pane(raw_pane_id, remote=remote)
            message = blocked_message(
                pane_id,
                agent.get("agent", ""),
                agent.get("project", ""),
                agent.get("host", "local"),
                content,
                source_id=agent.get("source_id", "local"),
            )
            fingerprint = (
                message["prompt_id"],
                tuple(message["selected_options"]),
                message["prompt"],
            )
            previous = last_blocked_prompts.get(pane_id)
            if previous != fingerprint:
                new_prompt = previous is None or previous[0] != message["prompt_id"]
                message["update"] = not new_prompt
                last_blocked_prompts[pane_id] = fingerprint
                await broadcast(message)
                if generation != POLL_GENERATION:
                    return
                if new_prompt:
                    await send_web_push(
                        title=f"🐑 {agent.get('project', 'Agent')} blocked",
                        body=content[:120],
                        url=f"/?pane={quote(pane_id)}",
                    )
                    if generation != POLL_GENERATION:
                        return
        else:
            last_blocked_prompts.pop(pane_id, None)
            if last_statuses.get(pane_id) == "blocked":
                await send_web_push("", "", clear=True)
                if generation != POLL_GENERATION:
                    return
        last_statuses[pane_id] = status


async def event_push():
    while True:
        event = dict(await event_queue.get())
        generation = POLL_GENERATION
        raw_pane_id = event.get("raw_pane_id") or event.get("pane_id", "")
        source_id = str(event.get("source_id") or "local")
        try:
            source = agent_source(source_id)
        except ValueError:
            source = local_agent_source()
            source_id = "local"
        pane_id = public_pane_id(source_id, raw_pane_id) if raw_pane_id else ""
        if pane_id:
            event["pane_id"] = pane_id
            event["raw_pane_id"] = raw_pane_id
            event["source_id"] = source_id
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

        if update:
            snapshot_agents, statuses, source_map = await collect_all_agents()
            if generation != POLL_GENERATION:
                continue
            if not snapshot_agents:
                snapshot_agents = list(agent_cache.values())
            event_agent = {
                **agent_cache.get(pane_id, {}),
                **agent_data,
                "pane_id": pane_id,
                "raw_pane_id": raw_pane_id,
                "source_id": source_id,
            }
            matching_index = next(
                (
                    index for index, agent in enumerate(snapshot_agents)
                    if agent.get("pane_id") == pane_id
                ),
                None,
            )
            if matching_index is None:
                snapshot_agents.append(event_agent)
            else:
                snapshot_agents[matching_index] = {
                    **snapshot_agents[matching_index],
                    **event_agent,
                }
            agent_source_cache.update({item["id"]: item for item in statuses})
            update_pane_maps(snapshot_agents, source_map)
            if statuses:
                await broadcast({"type": "agent_sources", "sources": statuses})
                if generation != POLL_GENERATION:
                    continue
            await broadcast({"type": "agents", "agents": snapshot_agents})
            if generation != POLL_GENERATION:
                continue
            if status != "blocked":
                await broadcast(update)
                if generation != POLL_GENERATION:
                    continue

        if pane_id and status != "blocked":
            last_blocked_prompts.pop(pane_id, None)
            if last_statuses.get(pane_id) == "blocked":
                await send_web_push("", "", clear=True)
                if generation != POLL_GENERATION:
                    continue
            last_statuses[pane_id] = status

        if status == "blocked" and pane_id:
            routed_pane_id, remote = pane_route(pane_id)
            if remote or host == "local":
                content = read_pane(routed_pane_id, remote=remote)
            else:
                content = event.get("prompt", "Agent is blocked")
            message = blocked_message(
                pane_id,
                agent_data.get("agent", ""),
                agent_data.get("project", ""),
                host,
                content or event.get("prompt", "Agent is blocked"),
                source_id=source_id,
            )
            previous = last_blocked_prompts.get(pane_id)
            new_prompt = previous is None or previous[0] != message["prompt_id"]
            message["update"] = not new_prompt
            last_blocked_prompts[pane_id] = (
                message["prompt_id"],
                tuple(message["selected_options"]),
                message["prompt"],
            )
            last_statuses[pane_id] = status
            await broadcast(message)
            if generation != POLL_GENERATION:
                continue
            if new_prompt:
                await send_web_push(
                    title=f"🐑 {agent_data.get('project', 'Agent')} blocked",
                    body=(content or message["prompt"])[:120],
                    url=f"/?pane={quote(pane_id)}",
                )
                if generation != POLL_GENERATION:
                    continue


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
    origin_identity = normalized_http_origin(origin)
    if origin_identity is None:
        return False
    if origin_identity in TRUSTED_ORIGIN_IDENTITIES:
        return True
    host_header = request_header(request, "Host")
    if not host_header:
        return False
    try:
        parsed_host = urlsplit(f"//{host_header}")
        origin_scheme, origin_host, origin_port = origin_identity
        request_host = (parsed_host.hostname or "").casefold().rstrip(".")
        if not origin_host or origin_host != request_host:
            return False
        request_port = parsed_host.port or (443 if origin_scheme == "https" else 80)
        if origin_port != request_port:
            return False
    except ValueError:
        return False
    return origin_scheme == "https" or is_loopback_host(origin_host)


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


def authenticated_principal(auth: dict) -> str:
    mode = str(auth.get("mode", ""))
    if mode == "tailscale":
        return f"tailscale:{str(auth.get('login', '')).casefold()}"
    if mode in {"token", "token-query", "web-session"}:
        return "relay-token"
    if mode == "development":
        return "development"
    # handle_client is only reached after process_request authentication. This
    # fallback also keeps direct unit-test WebSockets deterministic.
    return "authenticated-client"


def purge_workspace_downloads(now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    expired = [
        token for token, grant in workspace_downloads.items()
        if float(grant.get("expires", 0)) <= current
    ]
    for token in expired:
        workspace_downloads.pop(token, None)


def create_workspace_download(metadata: dict, auth: dict, now: float | None = None) -> dict:
    current = time.time() if now is None else float(now)
    token = secrets.token_urlsafe(32)
    grant = {
        "source_id": str(metadata["source_id"]),
        "path": str(metadata["path"]),
        "name": str(metadata["name"]),
        "size": int(metadata["size"]),
        "include_terminal_profiles": metadata.get("include_terminal_profiles") is True,
        "principal": authenticated_principal(auth),
        "expires": current + WORKSPACE_DOWNLOAD_TOKEN_TTL_SECONDS,
    }
    with workspace_download_lock:
        purge_workspace_downloads(current)
        if len(workspace_downloads) >= WORKSPACE_DOWNLOAD_TOKEN_LIMIT:
            oldest = min(
                workspace_downloads,
                key=lambda item: float(workspace_downloads[item].get("expires", 0)),
            )
            workspace_downloads.pop(oldest, None)
        workspace_downloads[token] = grant
    return {
        "type": "workspace_download_ready",
        "token": token,
        "url": f"/api/workspace-download?token={quote(token, safe='')}",
        "name": grant["name"],
        "size": grant["size"],
        "expires_in": WORKSPACE_DOWNLOAD_TOKEN_TTL_SECONDS,
    }


def consume_workspace_download(
    token: str,
    auth: dict,
    now: float | None = None,
) -> dict | None:
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        return None
    current = time.time() if now is None else float(now)
    principal = authenticated_principal(auth)
    with workspace_download_lock:
        purge_workspace_downloads(current)
        grant = workspace_downloads.get(token)
        if not grant or grant.get("principal") != principal:
            return None
        return workspace_downloads.pop(token)


def workspace_download_disposition(name: str) -> str:
    cleaned = "".join(
        character for character in str(name or "download")
        if 31 < ord(character) != 127
    ).strip() or "download"
    fallback = cleaned.encode("ascii", "ignore").decode() or "download"
    fallback = re.sub(r'[^A-Za-z0-9._ -]+', "_", fallback).strip(" .") or "download"
    return (
        f'attachment; filename="{fallback[:160]}"; '
        f"filename*=UTF-8''{quote(cleaned[:240], safe='')}"
    )


def websocket_request_id(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if 1 <= value <= 2_147_483_647 else 0


def bounded_history_messages(value) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("messages", value.get("history", []))
    if not isinstance(value, list):
        return []
    messages = []
    for item in value[-CONVERSATION_HISTORY_MAX_MESSAGES:]:
        if isinstance(item, dict):
            content = item.get("content", item.get("text", ""))
            role = str(item.get("role", item.get("type", "")))[:64]
            timestamp = str(
                item.get("timestamp", item.get("created_at", ""))
            )[:128]
        else:
            content = item
            role = ""
            timestamp = ""
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except (TypeError, ValueError):
                content = str(content)
        message = {"content": content[:CONVERSATION_HISTORY_CONTENT_MAX_CHARS]}
        if role:
            message["role"] = role
        if timestamp:
            message["timestamp"] = timestamp
        messages.append(message)
    return messages


def http_headers(content_type: str, cache_control: str = "no-cache", extra: list | None = None):
    from websockets.datastructures import Headers

    values = [
        ("Content-Type", content_type),
        ("Cache-Control", cache_control),
        ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
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

    if path == "/api/workspace-download":
        token = params.get("token", [""])[0]
        grant = consume_workspace_download(token, auth)
        if grant is None:
            return Response(
                404,
                "Not Found",
                http_headers("text/plain; charset=utf-8", "no-store"),
                b"download link is invalid or expired\n",
            )
        try:
            download_arguments = [
                workspace_file_download_for_source,
                grant["source_id"],
                grant["path"],
            ]
            if grant.get("include_terminal_profiles"):
                download_arguments.append(True)
            metadata, data = await asyncio.to_thread(*download_arguments)
        except ValueError as error:
            log.warning("Workspace download failed: %s", error)
            return Response(
                409,
                "Conflict",
                http_headers("text/plain; charset=utf-8", "no-store"),
                b"workspace file is no longer available\n",
            )
        if metadata["size"] != grant["size"] or metadata["name"] != grant["name"]:
            return Response(
                409,
                "Conflict",
                http_headers("text/plain; charset=utf-8", "no-store"),
                b"workspace file changed before download\n",
            )
        remote = getattr(connection, "remote_address", None)
        ip = remote[0] if isinstance(remote, (tuple, list)) and remote else "unknown"
        audit(
            "workspace_download",
            str(ip),
            clean_identity_header(request_header(request, "User-Agent"))[:80] or "browser",
            "",
            f"source={metadata['source_id']} name={metadata['name']!r} bytes={metadata['size']}",
        )
        extra_headers = [
            ("Content-Disposition", workspace_download_disposition(metadata["name"])),
            ("Content-Length", str(len(data))),
        ]
        return Response(
            200,
            "OK",
            http_headers("application/octet-stream", "no-store", extra_headers),
            data,
        )

    web_dir = Path(__file__).resolve().parent.parent / "web"
    static_files = {
        "/": ("index.html", "text/html; charset=utf-8", "no-cache"),
        "/index.html": ("index.html", "text/html; charset=utf-8", "no-cache"),
        "/app.css": ("app.css", "text/css; charset=utf-8", "no-cache"),
        "/app.js": ("app.js", "application/javascript; charset=utf-8", "no-cache"),
        "/vendor/xterm/xterm.css": (
            "vendor/xterm/xterm.css", "text/css; charset=utf-8", "public, max-age=86400",
        ),
        "/vendor/xterm/xterm.js": (
            "vendor/xterm/xterm.js", "application/javascript; charset=utf-8", "public, max-age=86400",
        ),
        "/vendor/xterm/addon-fit.js": (
            "vendor/xterm/addon-fit.js", "application/javascript; charset=utf-8", "public, max-age=86400",
        ),
        "/vendor/preview/marked-18.0.10.js": (
            "vendor/preview/marked-18.0.10.js",
            "application/javascript; charset=utf-8",
            "public, max-age=31536000, immutable",
        ),
        "/vendor/preview/dompurify-3.4.14.min.js": (
            "vendor/preview/dompurify-3.4.14.min.js",
            "application/javascript; charset=utf-8",
            "public, max-age=31536000, immutable",
        ),
        "/vendor/preview/highlight-11.12.0.min.js": (
            "vendor/preview/highlight-11.12.0.min.js",
            "application/javascript; charset=utf-8",
            "public, max-age=31536000, immutable",
        ),
        "/vendor/fonts/firacode-nerd-mono-v3.3.0.woff2": (
            "vendor/fonts/firacode-nerd-mono-v3.3.0.woff2",
            "font/woff2",
            "public, max-age=31536000, immutable",
        ),
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
    command_connection = bool(
        ws.request
        and ws.request.headers.get("X-Herdr-Remote-Command") == "1"
    )

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
    terminal_session = None
    workspace_upload = None
    workspace_upload_id = 0

    async def send_terminal_event(event: dict):
        if (
            event.get("type") == "terminal_exit"
            and terminal_session
            and event.get("session_id") == terminal_session.session_id
        ):
            active_terminal_sessions.discard(terminal_session)
        await ws.send(json.dumps(event))

    async def send_terminal_error(message: str, operation: str = "terminal"):
        await ws.send(json.dumps({
            "type": "terminal_error",
            "operation": operation,
            "message": message,
        }))

    async def close_terminal():
        nonlocal terminal_session
        if terminal_session is None:
            return
        active_terminal_sessions.discard(terminal_session)
        await terminal_session.close()
        terminal_session = None

    async def close_workspace_upload():
        nonlocal workspace_upload, workspace_upload_id
        if workspace_upload is None:
            return
        upload = workspace_upload
        workspace_upload = None
        workspace_upload_id = 0
        await asyncio.to_thread(upload.cancel)

    async def send_workspace_upload_error(message: str, upload_id: int = 0):
        await ws.send(json.dumps({
            "type": "workspace_upload_error",
            "upload_id": upload_id,
            "message": message,
        }))

    def command_error(message: str, request_id=None) -> dict:
        response = {"type": "error", "message": message}
        if request_id:
            response["request_id"] = request_id
        return response

    def command_result(command: str, request_id=None, **extra) -> dict:
        response = {"type": "command_result", "command": command, "ok": True, **extra}
        if request_id:
            response["request_id"] = request_id
        return response

    try:
        terminal_enabled = terminal_access_allowed(auth)
        if not command_connection:
            await ws.send(json.dumps({
                "type": "session",
                "auth": auth.get("mode", "token"),
                "user": {
                    "login": auth.get("login", ""),
                    "name": auth.get("name", ""),
                },
                "features": {
                    "terminal": terminal_enabled,
                    "native_ssh": TAILSCALE_SSH_ENABLED,
                    "workspace_files": True,
                    "workspace_upload": terminal_enabled,
                    "conversation_history": True,
                },
                "limits": {
                    "workspace_upload_max_bytes": WORKSPACE_UPLOAD_MAX_BYTES,
                    "workspace_upload_chunk_bytes": WORKSPACE_UPLOAD_CHUNK_BYTES,
                },
                "machine": machine_access_info(),
                "terminal_profiles": configured_terminal_profiles() if terminal_enabled else [],
            }))
            await send_current_snapshot(ws)
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")
            if msg_type == "question_toggle":
                pane_id = msg.get("pane_id", "")
                option = msg.get("option", "")
                request_id = msg.get("request_id")
                if pane_id not in known_panes or not isinstance(option, str) or not option:
                    await ws.send(json.dumps(command_error(
                        "invalid question option", request_id
                    )))
                    continue
                if not prompt_matches(pane_id, msg.get("prompt_id", "")):
                    await ws.send(json.dumps(command_error(
                        "question changed; refresh and try again", request_id
                    )))
                    continue
                if not toggle_question_option(pane_id, option):
                    await ws.send(json.dumps(command_error(
                        "question option toggle failed", request_id
                    )))
                    continue
                await ws.send(json.dumps(command_result(
                    "question_toggle", request_id
                )))
            elif msg_type == "question_submit":
                pane_id = msg.get("pane_id", "")
                request_id = msg.get("request_id")
                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id", request_id)))
                    continue
                if not prompt_matches(pane_id, msg.get("prompt_id", "")):
                    await ws.send(json.dumps(command_error(
                        "question changed; refresh and try again", request_id
                    )))
                    continue
                if not submit_multi_question(pane_id):
                    await ws.send(json.dumps(command_error(
                        "question submission failed", request_id
                    )))
                    continue
                await ws.send(json.dumps(command_result(
                    "question_submit", request_id
                )))
            elif msg_type == "terminal_profiles_request":
                if not terminal_enabled:
                    await send_terminal_error("Web terminal access is not authorized")
                    continue
                await ws.send(json.dumps({
                    "type": "terminal_profiles",
                    "profiles": configured_terminal_profiles(),
                }))
            elif msg_type == "ssh_profile_save":
                if not terminal_enabled:
                    await send_terminal_error(
                        "Web terminal access is not authorized", "ssh_profile"
                    )
                    continue
                raw_profile = msg.get("profile")
                previous_profile = None
                if isinstance(raw_profile, dict) and raw_profile.get("id"):
                    with contextlib.suppress(TerminalConfigError):
                        previous_profile = terminal_profile(str(raw_profile["id"]))
                try:
                    saved_profile = await asyncio.to_thread(
                        save_ssh_profile,
                        SSH_HOSTS_FILE,
                        raw_profile,
                    )
                    endpoint_changed = previous_profile and (
                        previous_profile.get("target"), previous_profile.get("port")
                    ) != (saved_profile.get("target"), saved_profile.get("port"))
                    if endpoint_changed:
                        await asyncio.to_thread(
                            terminate_persistent_session,
                            previous_profile,
                            TMUX_BINARY,
                        )
                    profiles = configured_terminal_profiles()
                except TerminalConfigError as e:
                    await send_terminal_error(str(e), "ssh_profile")
                    continue
                audit("ssh_profile_save", ip, device, "", f"profile={saved_profile['id']}")
                agent_source_cache.pop(saved_profile["id"], None)
                await ws.send(json.dumps({"type": "terminal_profiles", "profiles": profiles}))
                await broadcast({"type": "agent_sources", "sources": agent_source_snapshot()})
                await ws.send(json.dumps({
                    "type": "command_result",
                    "command": "ssh_profile_save",
                    "ok": True,
                    "profile_id": saved_profile["id"],
                }))
            elif msg_type == "ssh_profile_delete":
                if not terminal_enabled:
                    await send_terminal_error(
                        "Web terminal access is not authorized", "ssh_profile"
                    )
                    continue
                profile_id = msg.get("profile_id", "")
                try:
                    deleted_profile = terminal_profile(str(profile_id))
                    await asyncio.to_thread(delete_ssh_profile, SSH_HOSTS_FILE, profile_id)
                    await asyncio.to_thread(
                        terminate_persistent_session,
                        deleted_profile,
                        TMUX_BINARY,
                    )
                    if terminal_session and terminal_session.profile["id"] == profile_id:
                        await close_terminal()
                    profiles = configured_terminal_profiles()
                except TerminalConfigError as e:
                    await send_terminal_error(str(e), "ssh_profile")
                    continue
                audit("ssh_profile_delete", ip, device, "", f"profile={profile_id}")
                agent_source_cache.pop(str(profile_id), None)
                await ws.send(json.dumps({"type": "terminal_profiles", "profiles": profiles}))
                await broadcast({"type": "agent_sources", "sources": agent_source_snapshot()})
                await ws.send(json.dumps({
                    "type": "command_result",
                    "command": "ssh_profile_delete",
                    "ok": True,
                    "profile_id": profile_id,
                }))
            elif msg_type == "terminal_open":
                if not terminal_enabled:
                    await send_terminal_error("Web terminal access is not authorized")
                    continue
                await close_terminal()
                if len(active_terminal_sessions) >= TERMINAL_MAX_SESSIONS:
                    await send_terminal_error("Too many web terminal sessions are active")
                    continue
                session = None
                try:
                    profile = terminal_profile(str(msg.get("profile_id", "")))
                    session = TerminalSession(
                        profile,
                        send_terminal_event,
                        shell_binary=TERMINAL_SHELL,
                        ssh_binary=SSH_BINARY,
                        ssh_config_file=SSH_CONFIG_FILE if SSH_CONFIG_FILE.is_file() else None,
                        tmux_binary=TMUX_BINARY,
                        cwd=terminal_working_directory(),
                        cols=msg.get("cols", 120),
                        rows=msg.get("rows", 32),
                    )
                    # Reserve the slot before spawning so concurrent clients cannot
                    # race past the global session limit while process creation awaits.
                    active_terminal_sessions.add(session)
                    await session.spawn()
                except TerminalConfigError as e:
                    if session is not None:
                        active_terminal_sessions.discard(session)
                        await session.close()
                    await send_terminal_error(str(e))
                    continue
                except (OSError, subprocess.SubprocessError) as e:
                    if session is not None:
                        active_terminal_sessions.discard(session)
                        await session.close()
                    log.warning(
                        "Terminal start failed for profile %s (%s)",
                        msg.get("profile_id", ""),
                        type(e).__name__,
                    )
                    await send_terminal_error("Could not start the terminal session")
                    continue
                terminal_session = session
                audit("terminal_open", ip, device, "", f"profile={profile['id']}")
                await ws.send(json.dumps({
                    "type": "terminal_opened",
                    "session_id": session.session_id,
                    "profile": profile,
                    "persistent": session.persistent,
                    "cols": session.cols,
                    "rows": session.rows,
                }))
                session.start_reader()
            elif msg_type == "terminal_input":
                if not terminal_enabled or terminal_session is None:
                    await send_terminal_error("Terminal session is not running")
                    continue
                if msg.get("session_id") != terminal_session.session_id:
                    await send_terminal_error("Terminal session id does not match")
                    continue
                encoded = msg.get("data", "")
                if not isinstance(encoded, str) or len(encoded) > 24 * 1024:
                    await send_terminal_error("Terminal input is invalid")
                    continue
                try:
                    data = base64.b64decode(encoded, validate=True)
                    await terminal_session.write(data)
                except (binascii.Error, TerminalConfigError) as e:
                    await send_terminal_error(str(e))
            elif msg_type == "terminal_resize":
                if not terminal_enabled or terminal_session is None:
                    await send_terminal_error(
                        "Terminal session is not running", "terminal_resize"
                    )
                    continue
                if msg.get("session_id") != terminal_session.session_id:
                    await send_terminal_error(
                        "Terminal session id does not match", "terminal_resize"
                    )
                    continue
                resize_id = msg.get("resize_id")
                if (
                    resize_id is not None
                    and (
                        isinstance(resize_id, bool)
                        or not isinstance(resize_id, int)
                        or not 1 <= resize_id <= 2_147_483_647
                    )
                ):
                    await send_terminal_error(
                        "Terminal resize id is invalid", "terminal_resize"
                    )
                    continue
                try:
                    cols, rows = await terminal_session.resize(
                        msg.get("cols"), msg.get("rows")
                    )
                except TerminalConfigError as e:
                    await send_terminal_error(str(e), "terminal_resize")
                    continue
                if resize_id is not None:
                    await ws.send(json.dumps({
                        "type": "terminal_resized",
                        "session_id": terminal_session.session_id,
                        "resize_id": resize_id,
                        "cols": cols,
                        "rows": rows,
                    }))
            elif msg_type == "terminal_capture":
                if not terminal_enabled or terminal_session is None:
                    await send_terminal_error(
                        "Terminal session is not running", "terminal_capture"
                    )
                    continue
                if msg.get("session_id") != terminal_session.session_id:
                    await send_terminal_error(
                        "Terminal session id does not match", "terminal_capture"
                    )
                    continue
                try:
                    content, truncated = await terminal_session.capture()
                except TerminalConfigError as e:
                    await send_terminal_error(str(e), "terminal_capture")
                    continue
                capture_id = msg.get("capture_id")
                await ws.send(json.dumps({
                    "type": "terminal_capture",
                    "session_id": terminal_session.session_id,
                    "capture_id": capture_id if isinstance(capture_id, int) else 0,
                    "content": content,
                    "truncated": truncated,
                }))
            elif msg_type == "terminal_close":
                if terminal_session is not None:
                    profile_id = terminal_session.profile["id"]
                    await close_terminal()
                    audit("terminal_close", ip, device, "", f"profile={profile_id}")
                await ws.send(json.dumps({
                    "type": "command_result",
                    "command": "terminal_close",
                    "ok": True,
                }))
            elif msg_type == "respond":
                pane_id = msg.get("pane_id", "")
                request_id = msg.get("request_id")
                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id", request_id)))
                    continue
                raw_text = msg.get("text", "")
                if not isinstance(raw_text, str):
                    await ws.send(json.dumps(command_error(
                        "response must be text", request_id
                    )))
                    continue
                text = raw_text.strip()
                if not text:
                    await ws.send(json.dumps(command_error(
                        "response empty", request_id
                    )))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                content = read_pane(raw_pane_id, remote=remote)
                if question_prompt_id(pane_id, content) != msg.get("prompt_id", ""):
                    await ws.send(json.dumps(command_error(
                        "prompt changed; refresh and try again", request_id
                    )))
                    continue
                question = detect_question(content) if pane_is_omp(pane_id) else None
                log.info(
                    "Response from %s (%s): pane=%s chars=%d",
                    ip,
                    device,
                    pane_id,
                    len(text),
                )
                audit("respond", ip, device, pane_id, sensitive_detail(text))
                if question:
                    delivered = respond_to_question(pane_id, text, question)
                elif custom_editor_active(content) or text.casefold() in SAFE_RESPONSES:
                    delivered = _mutate_herdr(
                        "pane", "send-text", raw_pane_id, text, remote=remote
                    ) and _mutate_herdr(
                        "pane", "send-keys", raw_pane_id, "Enter", remote=remote
                    )
                else:
                    await ws.send(json.dumps(command_error(
                        "free-text response requires a detected question",
                        request_id,
                    )))
                    continue
                if not delivered:
                    await ws.send(json.dumps(command_error(
                        "response delivery failed", request_id
                    )))
                    continue
                await ws.send(json.dumps(command_result("respond", request_id)))
            elif msg_type == "session_switch":
                request_id = msg.get("request_id")
                source_identifier = msg.get("source_id") or msg.get("host")
                ok, error, changed = apply_session_switch(
                    source_identifier,
                    msg.get("session"),
                    ip,
                    device,
                )
                if not ok:
                    await ws.send(json.dumps(command_error(error, request_id)))
                    continue
                if changed:
                    await broadcast_sessions()
                await ws.send(json.dumps(command_result("session_switch", request_id)))
                if changed:
                    try:
                        await _poll_once()
                    except Exception:
                        log.exception("post-switch poll failed")
            elif msg_type == "agent_event":
                event_queue.put_nowait(msg)
            elif msg_type == "workspace_upload_start":
                upload_id = websocket_request_id(msg.get("upload_id"))
                if not terminal_enabled:
                    await send_workspace_upload_error(
                        "Workspace upload requires authorized Remote Shell access",
                        upload_id,
                    )
                    continue
                if not upload_id:
                    await send_workspace_upload_error("Workspace upload id is invalid")
                    continue
                if workspace_upload is not None:
                    await send_workspace_upload_error(
                        "Another workspace upload is already active",
                        upload_id,
                    )
                    continue
                raw_size = msg.get("size")
                if (
                    isinstance(raw_size, bool)
                    or not isinstance(raw_size, int)
                    or not 0 <= raw_size <= WORKSPACE_UPLOAD_MAX_BYTES
                ):
                    await send_workspace_upload_error(
                        "Workspace upload file size is invalid or exceeds the limit",
                        upload_id,
                    )
                    continue
                try:
                    source = workspace_source(
                        msg.get("source_id"),
                        include_terminal_profiles=True,
                    )
                    upload = await asyncio.to_thread(
                        WorkspaceUpload,
                        source,
                        msg.get("directory", ""),
                        msg.get("name", ""),
                        raw_size,
                        msg.get("overwrite") is True,
                    )
                except (OSError, ValueError) as error:
                    await send_workspace_upload_error(str(error), upload_id)
                    continue
                workspace_upload = upload
                workspace_upload_id = upload_id
                await ws.send(json.dumps({
                    "type": "workspace_upload_ready",
                    "upload_id": upload_id,
                    "size": raw_size,
                    "chunk_bytes": WORKSPACE_UPLOAD_CHUNK_BYTES,
                }))
            elif msg_type == "workspace_upload_chunk":
                upload_id = websocket_request_id(msg.get("upload_id"))
                if workspace_upload is None or upload_id != workspace_upload_id:
                    await send_workspace_upload_error(
                        "Workspace upload session does not match",
                        upload_id,
                    )
                    continue
                encoded = msg.get("data")
                maximum_encoded = ((WORKSPACE_UPLOAD_CHUNK_BYTES + 2) // 3) * 4 + 4
                if not isinstance(encoded, str) or not encoded or len(encoded) > maximum_encoded:
                    await close_workspace_upload()
                    await send_workspace_upload_error(
                        "Workspace upload chunk is invalid",
                        upload_id,
                    )
                    continue
                try:
                    data = base64.b64decode(encoded, validate=True)
                    offset = msg.get("offset")
                    if isinstance(offset, bool) or not isinstance(offset, int):
                        raise ValueError("Workspace upload chunk offset is invalid")
                    received = await asyncio.to_thread(
                        workspace_upload.write,
                        offset,
                        data,
                    )
                except (binascii.Error, OSError, ValueError) as error:
                    await close_workspace_upload()
                    await send_workspace_upload_error(str(error), upload_id)
                    continue
                await ws.send(json.dumps({
                    "type": "workspace_upload_progress",
                    "upload_id": upload_id,
                    "received": received,
                    "size": workspace_upload.size,
                }))
            elif msg_type == "workspace_upload_finish":
                upload_id = websocket_request_id(msg.get("upload_id"))
                if workspace_upload is None or upload_id != workspace_upload_id:
                    await send_workspace_upload_error(
                        "Workspace upload session does not match",
                        upload_id,
                    )
                    continue
                upload = workspace_upload
                workspace_upload = None
                workspace_upload_id = 0
                await ws.send(json.dumps({
                    "type": "workspace_upload_committing",
                    "upload_id": upload_id,
                    "remote": upload.source.get("kind") != "local",
                }))
                try:
                    uploaded = await asyncio.to_thread(upload.finish)
                except (OSError, ValueError) as error:
                    upload.cancel()
                    await send_workspace_upload_error(str(error), upload_id)
                    continue
                audit(
                    "workspace_upload",
                    ip,
                    device,
                    "",
                    f"source={uploaded['source_id']} name={uploaded['name']!r} "
                    f"bytes={uploaded['size']}",
                )
                await ws.send(json.dumps({
                    "type": "workspace_upload_complete",
                    "upload_id": upload_id,
                    **uploaded,
                }))
            elif msg_type == "workspace_upload_cancel":
                upload_id = websocket_request_id(msg.get("upload_id"))
                if workspace_upload is not None and upload_id == workspace_upload_id:
                    await close_workspace_upload()
                await ws.send(json.dumps({
                    "type": "workspace_upload_cancelled",
                    "upload_id": upload_id,
                }))
            elif msg_type == "list_workspace_files":
                request_id = websocket_request_id(msg.get("request_id"))
                try:
                    listing = await asyncio.to_thread(
                        workspace_file_listing_for_source,
                        msg.get("source_id"),
                        msg.get("path"),
                        terminal_enabled,
                    )
                except ValueError as error:
                    await ws.send(json.dumps({
                        "type": "workspace_error",
                        "operation": "list",
                        "request_id": request_id,
                        "source_id": str(msg.get("source_id") or "local"),
                        "message": str(error),
                    }))
                    continue
                listing["request_id"] = request_id
                await ws.send(json.dumps(listing))
            elif msg_type == "read_workspace_file":
                request_id = websocket_request_id(msg.get("request_id"))
                try:
                    workspace_file = await asyncio.to_thread(
                        workspace_file_read_for_source,
                        msg.get("source_id"),
                        msg.get("path", ""),
                        terminal_enabled,
                    )
                except ValueError as error:
                    await ws.send(json.dumps({
                        "type": "workspace_error",
                        "operation": "read",
                        "request_id": request_id,
                        "source_id": str(msg.get("source_id") or "local"),
                        "message": str(error),
                    }))
                    continue
                workspace_file["request_id"] = request_id
                await ws.send(json.dumps(workspace_file))
            elif msg_type == "prepare_workspace_download":
                request_id = websocket_request_id(msg.get("request_id"))
                try:
                    metadata = await asyncio.to_thread(
                        workspace_file_metadata_for_source,
                        msg.get("source_id"),
                        msg.get("path", ""),
                        terminal_enabled,
                    )
                    metadata["include_terminal_profiles"] = terminal_enabled
                    download = create_workspace_download(metadata, auth)
                except ValueError as error:
                    await ws.send(json.dumps({
                        "type": "workspace_error",
                        "operation": "download",
                        "request_id": request_id,
                        "source_id": str(msg.get("source_id") or "local"),
                        "message": str(error),
                    }))
                    continue
                download.update({
                    "request_id": request_id,
                    "source_id": metadata["source_id"],
                    "source_label": metadata["source_label"],
                })
                await ws.send(json.dumps(download))
            elif msg_type == "list_directories":
                try:
                    listing = await asyncio.to_thread(
                        workspace_directory_listing_for_source,
                        msg.get("source_id"),
                        msg.get("path"),
                    )
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                await ws.send(json.dumps(listing))
            elif msg_type == "start_agent":
                if msg.get("kind", "codex") != "codex":
                    await ws.send(json.dumps({"type": "error", "message": "Only Codex can be started remotely"}))
                    continue
                if agent_start_in_progress:
                    await ws.send(json.dumps({"type": "error", "message": "Another agent is currently starting"}))
                    continue
                cwd = msg.get("cwd", "")
                prompt = msg.get("prompt", "")
                if not isinstance(prompt, str):
                    await ws.send(json.dumps({"type": "error", "message": "Prompt must be text"}))
                    continue
                try:
                    source = agent_source(msg.get("source_id"))
                    if source["kind"] == "local":
                        directory = resolve_workspace_path(cwd)
                        start_cwd = str(directory)
                        display_path = display_workspace_path(directory)
                    else:
                        listing = await asyncio.to_thread(
                            remote_workspace_directory_listing,
                            source,
                            cwd,
                        )
                        if not listing.get("can_start_agent") or not listing.get("path"):
                            raise ValueError("Remote workspace directory cannot start an Agent")
                        start_cwd = str(listing["path"])
                        display_path = str(listing.get("display_path") or start_cwd)
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                log.info(
                    "Start Codex from %s (%s): source=%s cwd=%s prompt_chars=%d",
                    ip, device, source["id"], display_path, len(prompt),
                )
                audit(
                    "start_agent", ip, device, "",
                    f"kind=codex source={source['id']} cwd={display_path} {sensitive_detail(prompt)}",
                )
                agent_start_in_progress = True
                try:
                    if source["kind"] == "local":
                        started_agent = await asyncio.to_thread(start_local_codex, start_cwd, prompt)
                    else:
                        started_agent = await asyncio.to_thread(
                            start_codex_on_source,
                            start_cwd,
                            prompt,
                            source,
                        )
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
                    "raw_pane_id": started_agent.get("raw_pane_id", pane_id),
                    "source_id": started_agent.get("source_id", source["id"]),
                    "agent": "codex",
                    "status": started_agent.get("status", "idle"),
                    "cwd": started_agent["cwd"],
                    "project": started_agent["project"],
                    "host": started_agent.get("host", "local"),
                    "workspace_id": started_agent["workspace_id"],
                }
                known_panes.add(pane_id)
                pane_remote_map[pane_id] = source if source["kind"] != "local" else None
                pane_raw_map[pane_id] = agent_update["raw_pane_id"]
                agent_cache[pane_id] = agent_update
                await ws.send(json.dumps({"type": "agent_started", "ok": True, "agent": started_agent}))
                await broadcast({"type": "agent_update", "agent": agent_update})
            elif msg_type == "read_pane":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                try:
                    lines = max(1, min(int(msg.get("lines", 500)), PANE_READ_MAX_LINES))
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "message": "lines must be an integer"}))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                content, ansi_content = read_pane_snapshot(
                    raw_pane_id,
                    lines,
                    remote=remote,
                    agent_status=agent_cache.get(pane_id, {}).get("status", ""),
                )
                await ws.send(json.dumps({
                    "type": "pane_content",
                    "pane_id": pane_id,
                    "content": content,
                    "ansi_content": ansi_content,
                }))
            elif msg_type == "get_history":
                pane_id = msg.get("pane_id", "")
                if pane_id not in known_panes:
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": "unknown pane_id",
                    }))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                history = await asyncio.to_thread(
                    run_herdr,
                    "agent",
                    "history",
                    raw_pane_id,
                    "--format",
                    "json",
                    remote=remote,
                )
                try:
                    history_data = json.loads(history) if history else {}
                except (json.JSONDecodeError, TypeError):
                    history_data = {}
                await ws.send(json.dumps({
                    "type": "history",
                    "pane_id": pane_id,
                    "messages": bounded_history_messages(history_data),
                }))
            elif msg_type == "agent_seen":
                pane_id = msg.get("pane_id", "")
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                try:
                    agent_info, focused = await asyncio.to_thread(
                        mark_agent_seen,
                        raw_pane_id,
                        remote,
                    )
                except Exception as e:
                    log.warning(
                        "agent_seen command failed for pane %s (%s)",
                        pane_id,
                        type(e).__name__,
                    )
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": "agent_seen command failed",
                    }))
                    continue

                status = str(agent_info.get("agent_status", "unknown")).casefold()
                agent_update = {
                    **agent_cache.get(pane_id, {}),
                    "pane_id": pane_id,
                    "status": status,
                }
                agent_cache[pane_id] = agent_update
                if focused:
                    log.info("Marked agent output as seen: pane=%s", pane_id)
                    audit("agent_seen", ip, device, pane_id)
                await broadcast({"type": "agent_update", "agent": agent_update})
                await ws.send(json.dumps({
                    "type": "command_result",
                    "command": "agent_seen",
                    "ok": True,
                    "changed": focused,
                    "status": status,
                }))
            elif msg_type == "send_keys":
                pane_id = msg.get("pane_id", "")
                request_id = msg.get("request_id")
                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id", request_id)))
                    continue
                keys = msg.get("keys", [])
                if not isinstance(keys, list) or not keys or len(keys) > 16 or not all(k in SAFE_KEYS for k in keys):
                    await ws.send(json.dumps(command_error(
                        "keys contain disallowed values", request_id
                    )))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                if any(key.isdigit() for key in keys):
                    content = read_pane(raw_pane_id, remote=remote)
                    if (
                        not detect_approval_options(content)
                        or question_prompt_id(pane_id, content)
                        != msg.get("prompt_id", "")
                    ):
                        await ws.send(json.dumps(command_error(
                            "prompt changed; refresh and try again", request_id
                        )))
                        continue
                log.info("Keys from %s (%s): pane=%s keys=%s", ip, device, pane_id, keys)
                audit("send_keys", ip, device, pane_id, f"keys={keys}")
                try:
                    result = run_herdr_result("pane", "send-keys", raw_pane_id, *keys, remote=remote)
                except Exception as e:
                    log.warning("send_keys command failed for pane %s: %s", pane_id, e)
                    await ws.send(json.dumps(command_error(
                        "send_keys command failed", request_id
                    )))
                    continue
                if result.returncode != 0:
                    log.warning("send_keys command failed for pane %s with exit %s", pane_id, result.returncode)
                    await ws.send(json.dumps(command_error(
                        "send_keys command failed", request_id
                    )))
                    continue
                await ws.send(json.dumps(command_result("send_keys", request_id)))
            elif msg_type == "agent_prompt":
                pane_id = msg.get("pane_id", "")
                request_id = msg.get("request_id")
                if pane_id not in known_panes:
                    await ws.send(json.dumps(command_error("unknown pane_id", request_id)))
                    continue
                text = msg.get("text", "")
                try:
                    images = decode_prompt_images(msg.get("images", []))
                except ValueError as error:
                    await ws.send(json.dumps(command_error(str(error), request_id)))
                    continue
                if not isinstance(text, str) or (not text and not images):
                    await ws.send(json.dumps(command_error(
                        "prompt is empty", request_id
                    )))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                log.info(
                    "Agent prompt from %s (%s): pane=%s chars=%d images=%d",
                    ip, device, pane_id, len(text), len(images),
                )
                audit(
                    "agent_prompt", ip, device, pane_id,
                    f"{sensitive_detail(text)} images={len(images)}",
                )
                try:
                    if images:
                        delivery = await asyncio.to_thread(
                            submit_codex_image_prompt,
                            raw_pane_id,
                            text,
                            images,
                            remote,
                        )
                    else:
                        delivery = await asyncio.to_thread(submit_agent_prompt, raw_pane_id, text, remote)
                except ValueError as error:
                    await ws.send(json.dumps(command_error(str(error), request_id)))
                    continue
                except Exception as e:
                    # Exception strings from subprocess may embed the full prompt command.
                    log.warning("agent_prompt command failed for pane %s (%s)", pane_id, type(e).__name__)
                    await ws.send(json.dumps(command_error(
                        "agent_prompt command failed", request_id
                    )))
                    continue
                log.info("Agent prompt accepted for pane %s: delivery=%s", pane_id, delivery)
                await ws.send(json.dumps(command_result(
                    "agent_prompt", request_id, delivery=delivery
                )))
            elif msg_type == "agent_prompt_queue":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not isinstance(text, str) or not text:
                    await ws.send(json.dumps({"type": "error", "message": "text empty"}))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                log.info("Agent prompt cache from %s (%s): pane=%s chars=%d", ip, device, pane_id, len(text))
                audit("agent_prompt_queue", ip, device, pane_id, sensitive_detail(text))
                try:
                    delivery = await asyncio.to_thread(
                        cache_agent_prompt_with_tab,
                        raw_pane_id,
                        text,
                        remote,
                    )
                except ValueError as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
                    continue
                except Exception as e:
                    log.warning(
                        "agent_prompt_queue command failed for pane %s (%s)",
                        pane_id,
                        type(e).__name__,
                    )
                    await ws.send(json.dumps({
                        "type": "error",
                        "message": "agent_prompt_queue command failed",
                    }))
                    continue
                log.info("Agent prompt cached for pane %s", pane_id)
                await ws.send(json.dumps({
                    "type": "command_result",
                    "command": "agent_prompt_queue",
                    "ok": True,
                    "delivery": delivery,
                }))
            elif msg_type == "send_text":
                pane_id = msg["pane_id"]
                if pane_id not in known_panes:
                    await ws.send(json.dumps({"type": "error", "message": "unknown pane_id"}))
                    continue
                text = msg.get("text", "")
                if not isinstance(text, str) or not text:
                    await ws.send(json.dumps({"type": "error", "message": "text empty"}))
                    continue
                raw_pane_id, remote = pane_route(pane_id)
                log.info("Text from %s (%s): pane=%s chars=%d", ip, device, pane_id, len(text))
                audit("send_text", ip, device, pane_id, sensitive_detail(text))
                run_herdr("pane", "send-text", raw_pane_id, text, remote=remote)
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
        await close_workspace_upload()
        await close_terminal()
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
    if WEB_TERMINAL_ENABLED:
        if not TAILSCALE_WEB_ENABLED and not TERMINAL_ALLOW_DEVELOPMENT:
            raise RuntimeError(
                "HERDR_WEB_TERMINAL requires HERDR_TAILSCALE_WEB=1. "
                "Development access needs the explicit HERDR_TERMINAL_ALLOW_DEVELOPMENT=1 opt-in."
            )
        if TAILSCALE_WEB_ENABLED and not TERMINAL_ALLOWED_USERS:
            raise RuntimeError(
                "HERDR_TERMINAL_ALLOWED_USERS is required when HERDR_WEB_TERMINAL=1."
            )
        if "*" in TERMINAL_ALLOWED_USERS:
            raise RuntimeError(
                "HERDR_TERMINAL_ALLOWED_USERS must contain explicit Tailscale login names; "
                "wildcard terminal access is not allowed."
            )
        if (
            TAILSCALE_WEB_ENABLED
            and "*" not in TAILSCALE_ALLOWED_USERS
            and "*" not in TERMINAL_ALLOWED_USERS
            and not TERMINAL_ALLOWED_USERS.issubset(TAILSCALE_ALLOWED_USERS)
        ):
            raise RuntimeError(
                "Every HERDR_TERMINAL_ALLOWED_USERS login must also be in "
                "HERDR_TAILSCALE_ALLOWED_USERS."
            )
        if not os.path.isfile(TERMINAL_SHELL) or not os.access(TERMINAL_SHELL, os.X_OK):
            raise RuntimeError("HERDR_TERMINAL_SHELL must point to an executable shell.")
        if not os.path.isfile(SSH_BINARY) or not os.access(SSH_BINARY, os.X_OK):
            raise RuntimeError("OpenSSH is required when HERDR_WEB_TERMINAL=1.")
        try:
            load_ssh_profiles(SSH_HOSTS_FILE)
        except TerminalConfigError as error:
            raise RuntimeError(str(error)) from error
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
    loop = asyncio.get_running_loop()
    zc = info = udp_transport = server = None
    tasks = []
    loop_signal_handlers = []
    fallback_signal_handlers = {}
    stop = loop.create_future()

    def resolve_stop():
        if not stop.done():
            stop.set_result(None)

    def request_stop(*_):
        loop.call_soon_threadsafe(resolve_stop)

    try:
        zc, info = start_mdns()
        try:
            udp_transport, _ = await loop.create_datagram_endpoint(
                UDPPlugin,
                local_addr=("127.0.0.1", 8376),
            )
        except OSError:
            log.warning("UDP 8376 in use, plugin push disabled")
        tasks = [
            asyncio.create_task(poll_loop()),
            asyncio.create_task(event_push()),
        ]
        server = await serve(
            handle_client,
            RELAY_HOST,
            WS_PORT,
            process_request=process_request,
            max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
            max_queue=32,
        )
        sources = configured_agent_sources()
        polling = [f'{source["label"]} ({source["id"]})' for source in sources]
        log.info(
            "herdr-remote relay on %s:%d (WebSocket + HTTP POST)",
            RELAY_HOST,
            WS_PORT,
        )
        log.info("Polling Agent Sources: %s", ", ".join(polling))
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, request_stop)
                loop_signal_handlers.append(sig)
            except NotImplementedError:
                fallback_signal_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, request_stop)
        await stop
    finally:
        for sig in loop_signal_handlers:
            loop.remove_signal_handler(sig)
        for sig, handler in fallback_signal_handlers.items():
            signal.signal(sig, handler)
        if server is not None:
            server.close()
            await server.wait_closed()
        if udp_transport is not None:
            udp_transport.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if zc is not None:
            try:
                if info is not None:
                    zc.unregister_service(info)
            finally:
                zc.close()


if __name__ == "__main__":
    asyncio.run(main())
