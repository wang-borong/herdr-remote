#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
"""herdr-remote Telegram bot — monitor and approve agents from Telegram."""
import asyncio, hashlib, html, ipaddress, json, os, logging, re, secrets, urllib.parse
from collections import Counter, OrderedDict

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

from agent_state import apply_agent_message

os.umask(0o077)
logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO; that URL contains the bot token. Silence it.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("herdr-tg")

TOKEN = os.environ.get("HERDR_TG_TOKEN", "")
CHAT_ID = os.environ.get("HERDR_TG_CHAT_ID", "")
USER_ID = os.environ.get("HERDR_TG_USER_ID", "")
RELAY_WS = os.environ.get("HERDR_RELAY", "ws://127.0.0.1:8375")
_relay_parts = urllib.parse.urlsplit(RELAY_WS)
RELAY_WS_SAFE = urllib.parse.urlunsplit((_relay_parts.scheme, _relay_parts.netloc, _relay_parts.path, "", ""))
_RELAY_TOKEN = urllib.parse.parse_qs(_relay_parts.query).get("token", [""])[0]


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


REQUIRE_PRIVATE_CHAT = env_flag("HERDR_TG_REQUIRE_PRIVATE_CHAT", default=True)
REQUIRE_LOCAL_RELAY = env_flag("HERDR_TG_REQUIRE_LOCAL_RELAY", default=True)
ALLOW_PERSISTENT_TRUST = env_flag("HERDR_TG_ALLOW_PERSISTENT_TRUST")
PANE_READ_LINES = bounded_env_int("HERDR_TG_READ_LINES", 60, 15, 200)
PANE_OUTPUT_MAX_CHARS = bounded_env_int("HERDR_TG_OUTPUT_MAX_CHARS", 12000, 3500, 24000)
PANE_CHUNK_ESCAPED_CHARS = 3200

ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:\][^\x07]*(?:\x07|\x1B\\)|[@-_][0-?]*[ -/]*[@-~])"
)
CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
WORKED_FOR_RE = re.compile(
    r"^\s*[─━═—–_\-=]*\s*Worked for\s+"
    r"(?P<duration>(?:<?\d+(?:\.\d+)?[dhms]\s*)+)"
    r"[─━═—–_\-=]*\s*$",
    re.IGNORECASE,
)
DIVIDER_LINE_RE = re.compile(r"^[ \t]*[─━═]{8,}[ \t]*$")


def scrub(value) -> str:
    """Strip secrets from any string before it is logged or sent to Telegram.

    WebSocket exceptions (e.g. InvalidURI) embed the full relay URL incl. the
    ?token= query, so raw exception text must never be surfaced unredacted.
    """
    s = str(value)
    for secret in (_RELAY_TOKEN, TOKEN):
        if secret:
            s = s.replace(secret, "<redacted>")
    return s

if not TOKEN:
    print("Set HERDR_TG_TOKEN (from @BotFather)")
    exit(1)

# State
pending: OrderedDict[tuple[int, int], str] = OrderedDict()  # (chat_id, message_id) -> pane_id
selected_workspace_dirs: dict[int, str] = {}
directory_path_tokens: OrderedDict[str, str] = OrderedDict()
approval_tokens: dict[str, str] = {}  # pane_id -> current blocked-notification generation
approval_trust_keys: dict[str, str] = {}  # pane_id -> current persistent-trust option key
agents: list[dict] = []       # current agent list from relay
prev_statuses: dict[str, str] = {}  # pane_id -> last known status
relay_connected = False
daily_stats: dict[str, dict] = {}  # pane_id -> {agent, project, blocked_count, working_mins, last_change}

AGENT_PAGE_SIZE = 20
PENDING_LIMIT = 500
BROWSE_PATH_LIMIT = 1000
BROWSE_PAGE_SIZE = 10
STATUS_ORDER = {"blocked": 0, "working": 1, "done": 2, "idle": 3, "unknown": 3}
STATUS_LABELS = {"blocked": "BLOCKED", "working": "WORKING", "done": "DONE", "idle": "IDLE", "unknown": "IDLE"}
ACTION_CODES = {
    "read": "r",
    "interrupt": "i",
    "select_send": "s",
    "select_reply": "q",
    "trust": "t",
    "approval": "k",
    "review_trust": "u",
    "cancel": "x",
}
CODE_ACTIONS = {code: action for action, code in ACTION_CODES.items()}

BOT_COMMAND_DEFINITIONS = [
    ("start", "打开 Herdr 控制面板"),
    ("agents", "查看全部 Agent 状态"),
    ("status", "检查 Relay 连接状态"),
    ("read", "读取 Agent 最近输出"),
    ("reply", "查看输出并回复 Agent"),
    ("send", "向 Agent 发送新 Prompt"),
    ("interrupt", "中断正在运行的 Agent"),
    ("digest", "查看今日活动摘要"),
    ("browse", "浏览允许的本地仓库目录"),
    ("cd", "选择 Codex 工作目录"),
    ("cwd", "查看当前选择的目录"),
    ("codex", "在所选目录启动 Codex"),
    ("help", "查看命令和使用说明"),
]


# --- Relay communication ---

async def send_to_relay(pane_id: str, text: str):
    """Send a response to the relay via WebSocket."""
    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "respond", "pane_id": pane_id, "text": text}))
    except Exception as e:
        log.warning(f"Failed to send to relay: {scrub(e)}")


async def send_keys_to_relay(pane_id: str, keys: list[str]):
    """Send raw key presses to the relay via WebSocket (e.g. ["1"] to pick a prompt option)."""
    import websockets
    async with websockets.connect(RELAY_WS) as ws:
        await ws.send(json.dumps({"type": "send_keys", "pane_id": pane_id, "keys": keys}))
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            response = json.loads(raw)
            if response.get("type") == "error":
                raise RuntimeError(response.get("message", "relay rejected keys"))
            if response.get("type") == "command_result" and response.get("command") == "send_keys":
                if not response.get("ok"):
                    raise RuntimeError(response.get("message", "relay rejected keys"))
                return
        raise RuntimeError("relay did not acknowledge keys")


async def read_pane(pane_id: str, lines: int = PANE_READ_LINES) -> str:
    """Read pane content from relay."""
    import websockets
    try:
        async with websockets.connect(RELAY_WS) as ws:
            await ws.send(json.dumps({"type": "read_pane", "pane_id": pane_id, "lines": lines}))
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msg = json.loads(raw)
            # Might get an agents broadcast first, skip to pane_content
            for _ in range(5):
                if msg.get("type") == "pane_content":
                    return msg.get("content", "(empty)")
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                msg = json.loads(raw)
    except Exception as e:
        return f"(error reading pane: {scrub(e)})"
    return "(no response)"


async def relay_request(payload: dict, expected_type: str, timeout: int = 15) -> dict:
    """Run a small authenticated RPC over a dedicated relay WebSocket."""
    import websockets
    try:
        async with websockets.connect(RELAY_WS, open_timeout=5) as ws:
            await ws.send(json.dumps(payload))
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("relay request timed out")
                response = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if response.get("type") == "error":
                    raise RuntimeError(response.get("message", "relay rejected request"))
                if response.get("type") == expected_type:
                    return response
    except Exception as e:
        raise RuntimeError(scrub(e)) from None


async def list_directories_from_relay(path: str | None = None) -> dict:
    return await relay_request(
        {"type": "list_directories", "path": path or ""},
        "directory_listing",
    )


async def start_codex_from_relay(cwd: str, prompt: str = "") -> dict:
    response = await relay_request(
        {"type": "start_agent", "kind": "codex", "cwd": cwd, "prompt": prompt},
        "agent_started",
        timeout=90,
    )
    return response["agent"]


async def send_agent_prompt_to_relay(pane_id: str, text: str):
    """Submit a prompt through herdr's semantic agent API."""
    if not text or len(text) > 1000:
        raise ValueError("text must contain 1-1000 characters")
    import websockets
    async with websockets.connect(RELAY_WS) as ws:
        await ws.send(json.dumps({"type": "agent_prompt", "pane_id": pane_id, "text": text}))
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            response = json.loads(raw)
            if response.get("type") == "error":
                raise RuntimeError(response.get("message", "relay rejected prompt"))
            if response.get("type") == "command_result" and response.get("command") == "agent_prompt":
                if not response.get("ok"):
                    raise RuntimeError(response.get("message", "relay rejected prompt"))
                return
        raise RuntimeError("relay did not acknowledge prompt")


# --- Auth guard ---

def authorized(update: Update) -> bool:
    """Reject messages from unauthorized users."""
    if not CHAT_ID or not USER_ID:
        return False
    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)
    if chat is None or user is None:
        return False
    if REQUIRE_PRIVATE_CHAT and getattr(chat, "type", None) != "private":
        return False
    return str(chat.id) == CHAT_ID and str(user.id) == USER_ID


def register_pending(chat_id: int, message_id: int, pane_id: str):
    key = (int(chat_id), int(message_id))
    pending[key] = pane_id
    pending.move_to_end(key)
    while len(pending) > PENDING_LIMIT:
        pending.popitem(last=False)


def pending_pane(chat_id: int, message_id: int) -> str | None:
    return pending.get((int(chat_id), int(message_id)))


def find_agent(pane_id: str) -> dict | None:
    matches = [agent for agent in agents if agent.get("pane_id") == pane_id]
    return matches[0] if len(matches) == 1 else None


def clear_relay_connection_state():
    global agents, relay_connected
    relay_connected = False
    agents = []
    approval_tokens.clear()
    approval_trust_keys.clear()


def clean_pane_output(content: str) -> str:
    """Remove terminal control bytes and collapse Codex's completed-run footer."""
    if not content:
        return "(empty)"

    cleaned = ANSI_ESCAPE_RE.sub("", str(content))
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = CONTROL_CHARACTER_RE.sub("", cleaned)
    lines = [line.rstrip() for line in cleaned.split("\n")]

    worked_for_indexes = []
    for index, line in enumerate(lines):
        match = WORKED_FOR_RE.match(line)
        if match:
            lines[index] = f"Worked for {match.group('duration').strip()}"
            worked_for_indexes.append(index)
            continue
        if DIVIDER_LINE_RE.match(line):
            lines[index] = ""

    # Codex leaves its next-prompt/model/path chrome below the final timing line.
    # Only collapse a marker near the bottom so an older completed turn does not
    # hide a newer, still-running turn in a larger pane capture.
    if worked_for_indexes:
        final_index = worked_for_indexes[-1]
        if len(lines) - final_index <= 12:
            lines = lines[:final_index + 1]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    compact_lines = []
    for line in lines:
        if not line.strip() and compact_lines and not compact_lines[-1].strip():
            continue
        compact_lines.append(line)

    result = "\n".join(compact_lines) or "(empty)"
    if len(result) > PANE_OUTPUT_MAX_CHARS:
        notice = "… earlier output omitted …\n"
        result = notice + result[-(PANE_OUTPUT_MAX_CHARS - len(notice)):]
    return result


def split_pane_output(content: str) -> list[str]:
    """Split pane text into HTML-safe Telegram-sized chunks."""
    content = clean_pane_output(content)
    chunks: list[str] = []
    current: list[str] = []
    escaped_size = 0

    for character in content:
        character_size = len(html.escape(character, quote=False))
        if current and escaped_size + character_size > PANE_CHUNK_ESCAPED_CHARS:
            chunks.append("".join(current))
            current = []
            escaped_size = 0
        current.append(character)
        escaped_size += character_size

    if current:
        chunks.append("".join(current))
    return chunks or ["(empty)"]


def pane_message_html(agent: dict, content: str | None, part: int = 1, total: int = 1, reply_prompt: bool = False) -> str:
    project = agent.get("project") or agent.get("agent") or "agent"
    agent_name = agent.get("agent") or "agent"
    status = (agent.get("status") or "unknown").upper()
    host = agent.get("host") or "local"

    metadata = [f"<code>{html.escape(agent_name)}</code>", html.escape(status)]
    if host != "local":
        metadata.append(f"<code>{html.escape(host)}</code>")
    parts = [f"<b>{html.escape(project)}</b>", " · ".join(metadata)]
    if total > 1:
        parts.append(f"<i>Output {part}/{total}</i>")
    if content is not None:
        parts.extend(["", f"<pre>{html.escape(content, quote=False)}</pre>"])
    if reply_prompt:
        parts.extend(["", "<i>Reply to this message to send your response.</i>"])
    return "\n".join(parts)


async def send_pane_output(message, chat, agent: dict, content: str | None, reply_prompt: bool = False):
    chunks = split_pane_output(content) if content is not None else [None]
    sent_messages = []
    for index, chunk in enumerate(chunks, start=1):
        is_last = index == len(chunks)
        kwargs = {"parse_mode": "HTML"}
        if reply_prompt and is_last and getattr(chat, "type", None) == "private":
            project = agent.get("project") or agent.get("agent") or "agent"
            kwargs["reply_markup"] = ForceReply(
                selective=True,
                input_field_placeholder=f"Reply to {project}"[:64],
            )
        sent = await message.reply_text(
            pane_message_html(
                agent,
                chunk,
                part=index,
                total=len(chunks),
                reply_prompt=reply_prompt and is_last,
            ),
            **kwargs,
        )
        register_pending(chat.id, sent.message_id, agent["pane_id"])
        sent_messages.append(sent)
    return sent_messages


async def send_reply_prompt(message, chat, agent: dict, content: str | None = None):
    sent_messages = await send_pane_output(message, chat, agent, content, reply_prompt=True)
    return sent_messages[-1]


def agents_for_action(action: str) -> list[dict]:
    if action == "interrupt":
        return [agent for agent in agents if agent.get("status") in ("working", "blocked")]
    if action == "trust":
        return [agent for agent in agents if agent.get("status") == "blocked"]
    return list(agents)


def sorted_agents(agent_list: list[dict]) -> list[dict]:
    return sorted(agent_list, key=lambda agent: (
        STATUS_ORDER.get(agent.get("status", "unknown"), 3),
        agent.get("project", "").lower(),
        agent.get("agent", "").lower(),
        agent.get("host", "local").lower(),
        agent.get("pane_id", ""),
    ))


def compact_identifier(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    digest = hashlib.sha1(value.encode()).hexdigest()[:12]
    return value[:limit - 15] + "..." + digest


def pane_callback_token(pane_id: str) -> str:
    return hashlib.sha256(pane_id.encode()).hexdigest()[:16]


def pane_callback_data(action: str, pane_id: str, **extra) -> str:
    data = {"a": ACTION_CODES[action], "p": pane_callback_token(pane_id), **extra}
    return json.dumps(data, separators=(",", ":"))


def resolve_pane_token(token: str) -> str | None:
    agent_matches = [
        agent.get("pane_id") for agent in agents
        if agent.get("pane_id") and pane_callback_token(agent["pane_id"]) == token
    ]
    if len(agent_matches) == 1:
        return agent_matches[0]
    if len(agent_matches) > 1:
        return None
    pending_matches = {
        pane_id for pane_id in pending.values()
        if pane_id and pane_callback_token(pane_id) == token
    }
    return next(iter(pending_matches)) if len(pending_matches) == 1 else None


def parse_callback_data(raw: str) -> dict:
    data = json.loads(raw)
    if "a" in data:
        data["action"] = CODE_ACTIONS.get(data["a"], "invalid")
    if "p" in data:
        data["pane_id"] = resolve_pane_token(data["p"])
    return data


def agent_button_labels(agent_list: list[dict]) -> list[str]:
    bases = []
    contexts = []
    for agent in agent_list:
        status = STATUS_LABELS.get(agent.get("status", "unknown"), "UNKNOWN")
        project = agent.get("project") or agent.get("cwd") or "unknown"
        name = agent.get("agent") or "agent"
        host = agent.get("host", "local")
        bases.append(f"[{status}] {project} ({name})")
        contexts.append(f" @{compact_identifier(host, 24)}" if host != "local" else "")

    provisional = [base[:max(1, 64 - len(context))] + context for base, context in zip(bases, contexts)]
    counts = Counter(provisional)
    labels = []
    for agent, base, context, candidate in zip(agent_list, bases, contexts, provisional):
        if counts[candidate] == 1:
            labels.append(candidate)
            continue
        pane_id = compact_identifier(agent.get("pane_id", "?"), 18)
        suffix = context + f" [{pane_id}]"
        labels.append(base[:max(1, 64 - len(suffix))] + suffix)
    unique_labels = []
    used = set()
    for label in labels:
        candidate = label
        ordinal = 2
        while candidate in used:
            marker = f" #{ordinal}"
            candidate = label[:64 - len(marker)] + marker
            ordinal += 1
        used.add(candidate)
        unique_labels.append(candidate)
    return unique_labels


def build_agent_keyboard(action: str, page: int = 0, agent_list: list[dict] | None = None) -> InlineKeyboardMarkup:
    ordered = sorted_agents(agents_for_action(action) if agent_list is None else agent_list)
    page_count = max(1, (len(ordered) + AGENT_PAGE_SIZE - 1) // AGENT_PAGE_SIZE)
    page = min(max(page, 0), page_count - 1)
    start = page * AGENT_PAGE_SIZE
    visible = ordered[start:start + AGENT_PAGE_SIZE]
    labels = agent_button_labels(ordered)[start:start + AGENT_PAGE_SIZE]
    keyboard = [[InlineKeyboardButton(
        label,
        callback_data=pane_callback_data(action, agent["pane_id"]),
    )] for agent, label in zip(visible, labels)]

    if page_count > 1:
        navigation = []
        if page > 0:
            navigation.append(InlineKeyboardButton(
                "Previous",
                callback_data=json.dumps(
                    {"action": "page", "menu": action, "page": page - 1}, separators=(",", ":")
                ),
            ))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton(
                "Next",
                callback_data=json.dumps(
                    {"action": "page", "menu": action, "page": page + 1}, separators=(",", ":")
                ),
            ))
        keyboard.append(navigation)
    return InlineKeyboardMarkup(keyboard)


def refresh_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Refresh agents",
            callback_data=json.dumps({"action": "page", "menu": action, "page": 0}, separators=(",", ":")),
        )
    ]])


def simple_callback_data(action: str, **extra) -> str:
    return json.dumps({"action": action, **extra}, separators=(",", ":"))


def remember_directory_path(path: str) -> str:
    token = hashlib.sha256(path.encode()).hexdigest()[:12]
    if token in directory_path_tokens and directory_path_tokens[token] != path:
        token = secrets.token_hex(8)
    directory_path_tokens[token] = path
    directory_path_tokens.move_to_end(token)
    while len(directory_path_tokens) > BROWSE_PATH_LIMIT:
        directory_path_tokens.popitem(last=False)
    return token


def directory_callback_data(operation: str, path: str = "", page: int = 0) -> str:
    data = {"action": "dir", "o": operation}
    if path:
        data["d"] = remember_directory_path(path)
    if page:
        data["g"] = page
    return json.dumps(data, separators=(",", ":"))


def resolve_directory_token(token: str) -> str | None:
    path = directory_path_tokens.get(token)
    if path:
        directory_path_tokens.move_to_end(token)
    return path


def requested_directory_path(chat_id: int, value: str) -> str:
    value = value.strip()
    if not value or value.startswith(("/", "~")):
        return value
    selected = selected_workspace_dirs.get(int(chat_id))
    return os.path.join(selected, value) if selected else value


def directory_browser_text(listing: dict, chat_id: int) -> str:
    entries = listing.get("entries", [])
    selected = selected_workspace_dirs.get(int(chat_id))
    lines = [
        "<b>Repository browser</b>",
        f"<code>{html.escape(listing.get('display_path') or 'Configured workspace roots')}</code>",
        "",
        f"Folders: {len(entries)}" + ("+" if listing.get("truncated") else ""),
    ]
    if selected:
        lines.append(f"Selected: <code>{html.escape(selected)}</code>")
    lines.append("Tap a folder to open it, or select the current directory.")
    return "\n".join(lines)


def directory_browser_keyboard(listing: dict, page: int = 0) -> InlineKeyboardMarkup:
    entries = listing.get("entries", [])
    page_count = max(1, (len(entries) + BROWSE_PAGE_SIZE - 1) // BROWSE_PAGE_SIZE)
    page = min(max(int(page), 0), page_count - 1)
    start = page * BROWSE_PAGE_SIZE
    visible = entries[start:start + BROWSE_PAGE_SIZE]
    rows = []
    for entry in visible:
        icon = "📦" if entry.get("is_repo") else "📁"
        label = f"{icon} {entry.get('name') or 'directory'}"[:64]
        rows.append([InlineKeyboardButton(
            label,
            callback_data=directory_callback_data("o", entry["path"]),
        )])

    if page_count > 1:
        navigation = []
        current_path = listing.get("path", "")
        if page > 0:
            navigation.append(InlineKeyboardButton(
                "Previous",
                callback_data=directory_callback_data("o", current_path, page - 1),
            ))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton(
                "Next",
                callback_data=directory_callback_data("o", current_path, page + 1),
            ))
        rows.append(navigation)

    current_path = listing.get("path", "")
    if current_path:
        actions = [InlineKeyboardButton(
            "✅ Select here",
            callback_data=directory_callback_data("s", current_path),
        )]
        if listing.get("can_start_agent"):
            actions.append(InlineKeyboardButton(
                "🚀 Codex here",
                callback_data=directory_callback_data("c", current_path),
            ))
        rows.append(actions)

    parent = listing.get("parent")
    navigation = [InlineKeyboardButton(
        "⬆️ Up" if parent else "🏠 Roots",
        callback_data=directory_callback_data("o", parent or ""),
    )]
    navigation.append(InlineKeyboardButton(
        "🎛 Control panel",
        callback_data=simple_callback_data("dashboard"),
    ))
    rows.append(navigation)
    return InlineKeyboardMarkup(rows)


async def send_directory_browser(message, chat, path: str | None = None, page: int = 0):
    requested = requested_directory_path(chat.id, path or "")
    try:
        listing = await list_directories_from_relay(requested or None)
    except Exception as e:
        await message.reply_text(f"Cannot browse that directory: {scrub(e)}")
        return None
    await message.reply_text(
        directory_browser_text(listing, chat.id),
        parse_mode="HTML",
        reply_markup=directory_browser_keyboard(listing, page=page),
    )
    return listing


def selected_directory_keyboard(path: str, can_start_agent: bool = True) -> InlineKeyboardMarkup:
    row = []
    if can_start_agent:
        row.append(InlineKeyboardButton(
            "🚀 Start Codex",
            callback_data=directory_callback_data("c", path),
        ))
    row.append(InlineKeyboardButton(
        "📂 Browse",
        callback_data=directory_callback_data("o", path),
    ))
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("🎛 Control panel", callback_data=simple_callback_data("dashboard"))],
    ])


def remember_started_agent(started: dict) -> dict:
    global agents
    agent = {
        "pane_id": started["pane_id"],
        "agent": "codex",
        "status": started.get("status", "idle"),
        "cwd": started["cwd"],
        "project": started.get("project") or os.path.basename(started["cwd"]),
        "host": "local",
        "workspace_id": started.get("workspace_id", ""),
    }
    agents = apply_agent_message(agents, {"type": "agent_update", "agent": agent})
    return agent


async def start_codex_for_chat(message, chat, cwd: str, prompt: str = ""):
    if len(prompt) > 1000:
        await message.reply_text("Prompt must contain at most 1000 characters.")
        return None
    await message.reply_text(f"Starting Codex in {cwd} …")
    try:
        started = await start_codex_from_relay(cwd, prompt)
    except Exception as e:
        await message.reply_text(f"Could not start Codex: {scrub(e)}")
        return None

    selected_workspace_dirs[int(chat.id)] = started["cwd"]
    agent = remember_started_agent(started)
    warning = started.get("warning", "")
    if prompt:
        status = "Initial prompt submitted." if started.get("prompted") else (warning or "Initial prompt was not submitted.")
        sent = await message.reply_text(
            f"Codex started in {started.get('display_path', started['cwd'])}.\n{status}",
            reply_markup=interaction_keyboard(agent["pane_id"]),
        )
        register_pending(chat.id, sent.message_id, agent["pane_id"])
    else:
        await message.reply_text(f"Codex started in {started.get('display_path', started['cwd'])}.")
        await send_reply_prompt(message, chat, agent)
    return agent


def dashboard_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("📖 Read", callback_data=simple_callback_data("picker", menu="read")),
            InlineKeyboardButton("💬 Reply", callback_data=simple_callback_data("picker", menu="select_reply")),
            InlineKeyboardButton("✉️ Send", callback_data=simple_callback_data("picker", menu="select_send")),
        ],
        [
            InlineKeyboardButton("⏹ Interrupt", callback_data=simple_callback_data("picker", menu="interrupt")),
            InlineKeyboardButton("🔄 Refresh", callback_data=simple_callback_data("dashboard")),
            InlineKeyboardButton("❓ Help", callback_data=simple_callback_data("help")),
        ],
        [
            InlineKeyboardButton("📂 Repositories", callback_data=simple_callback_data("browse")),
            InlineKeyboardButton("🆕 New Codex", callback_data=simple_callback_data("new_codex")),
        ],
    ]
    if relay_connected and agents:
        keyboard.extend(build_agent_keyboard("select_reply").inline_keyboard)
    return InlineKeyboardMarkup(keyboard)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Control panel", callback_data=simple_callback_data("dashboard")),
    ]])


def help_text() -> str:
    return (
        "<b>herdr-remote commands</b>\n\n"
        "/start — 打开控制面板\n"
        "/agents — 查看 Agent 状态\n"
        "/status — 检查 Relay 连接\n"
        "/read — 读取最近输出\n"
        "/reply — 查看输出并回复\n"
        "/send — 发送新 Prompt\n"
        "/interrupt — 发送 Ctrl+C\n"
        "/digest — 查看今日摘要\n\n"
        "/browse — 浏览允许的仓库目录\n"
        "/cd — 选择 Codex 工作目录\n"
        "/cwd — 查看当前目录\n"
        "/codex — 在当前目录启动 Codex\n\n"
        "也可以点击 Telegram 输入框旁的 <b>Menu</b>，或直接输入 <code>/</code> 使用命令补全。"
    )


def bot_commands() -> list[BotCommand]:
    definitions = list(BOT_COMMAND_DEFINITIONS)
    if ALLOW_PERSISTENT_TRUST:
        definitions.insert(-1, ("trust", "确认永久信任当前 Agent"))
    return [BotCommand(command, description) for command, description in definitions]


async def configure_bot_ui(app: Application):
    """Install command completion and the Telegram chat menu for the paired chat."""
    try:
        scope = BotCommandScopeChat(chat_id=int(CHAT_ID))
        await app.bot.set_my_commands(bot_commands(), scope=scope)
        await app.bot.set_chat_menu_button(
            chat_id=int(CHAT_ID),
            menu_button=MenuButtonCommands(),
        )
        log.info("Telegram command menu configured for the authorized chat")
    except Exception as e:
        # A transient Bot API failure must not take monitoring/control offline.
        log.warning("Failed to configure Telegram command menu: %s", scrub(e))


def dashboard_text() -> str:
    if not relay_connected:
        text = "herdr-remote bot\n\nRelay disconnected. Use /status for connection details."
    elif not agents:
        text = "herdr-remote bot\n\nConnected to relay. No agents are running."
    else:
        blocked = sum(agent.get("status") == "blocked" for agent in agents)
        working = sum(agent.get("status") == "working" for agent in agents)
        done = sum(agent.get("status") == "done" for agent in agents)
        idle = len(agents) - blocked - working - done
        text = f"herdr-remote bot\n\nAgents: {len(agents)} ({blocked} blocked, {working} working, {done} done, {idle} idle)\nSelect an agent to read and reply."
    return text


# --- Bot commands ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not authorized(update):
        return
    text = dashboard_text()
    await update.message.reply_text(text, reply_markup=dashboard_keyboard())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /help — show command completion and shortcut guidance."""
    if not authorized(update):
        return
    await update.message.reply_text(
        help_text(),
        parse_mode="HTML",
        reply_markup=help_keyboard(),
    )


async def cmd_browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /browse [path] — browse allowlisted local workspace directories."""
    if not authorized(update):
        return
    path = " ".join(ctx.args).strip() or None
    await send_directory_browser(update.message, update.effective_chat, path)


async def cmd_cd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /cd [path] — select the directory used by /codex."""
    if not authorized(update):
        return
    if not ctx.args:
        selected = selected_workspace_dirs.get(int(update.effective_chat.id))
        if selected:
            await send_directory_browser(update.message, update.effective_chat, selected)
        else:
            await update.message.reply_text("No directory selected. Choose one below.")
            await send_directory_browser(update.message, update.effective_chat)
        return

    requested = requested_directory_path(update.effective_chat.id, " ".join(ctx.args))
    try:
        listing = await list_directories_from_relay(requested)
    except Exception as e:
        await update.message.reply_text(f"Cannot select that directory: {scrub(e)}")
        return
    selected_workspace_dirs[int(update.effective_chat.id)] = listing["path"]
    await update.message.reply_text(
        f"Selected workspace:\n{listing['display_path']}",
        reply_markup=selected_directory_keyboard(
            listing["path"],
            can_start_agent=listing.get("can_start_agent", False),
        ),
    )


async def cmd_cwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /cwd — show the directory selected for new agents."""
    if not authorized(update):
        return
    selected = selected_workspace_dirs.get(int(update.effective_chat.id))
    if not selected:
        await update.message.reply_text("No workspace selected. Use /browse or /cd first.")
        return
    await update.message.reply_text(
        f"Selected workspace:\n{selected}",
        reply_markup=selected_directory_keyboard(selected),
    )


async def cmd_codex(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /codex [prompt] — create a new Codex agent in the selected directory."""
    if not authorized(update):
        return
    selected = selected_workspace_dirs.get(int(update.effective_chat.id))
    if not selected:
        await update.message.reply_text("Select a repository before starting Codex.")
        await send_directory_browser(update.message, update.effective_chat)
        return
    prompt = " ".join(ctx.args).strip()
    await start_codex_for_chat(update.message, update.effective_chat, selected, prompt)


async def cmd_agents(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /agents — list current agents with status."""
    if not authorized(update):
        return
    if not agents:
        await update.message.reply_text("No agents connected." if relay_connected else "Not connected to relay.")
        return

    blocked = [a for a in agents if a.get("status") == "blocked"]
    working = [a for a in agents if a.get("status") == "working"]
    done = [a for a in agents if a.get("status") == "done"]
    idle = [a for a in agents if a.get("status") in ("idle", "unknown")]

    lines = []
    if blocked:
        lines.append("BLOCKED:")
        for a in blocked:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if working:
        lines.append("WORKING:")
        for a in working:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if done:
        lines.append("DONE:")
        for a in done:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")
    if idle:
        lines.append("IDLE:")
        for a in idle:
            host = f" @{a['host']}" if a.get('host', 'local') != 'local' else ''
            lines.append(f"  {a['project']} ({a['agent']}){host}")

    await update.message.reply_text("\n".join(lines))


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /status — show connection info."""
    if not authorized(update):
        return
    b = len([a for a in agents if a.get("status") == "blocked"])
    w = len([a for a in agents if a.get("status") == "working"])
    d = len([a for a in agents if a.get("status") == "done"])
    i = len([a for a in agents if a.get("status") in ("idle", "unknown")])

    status = "Connected" if relay_connected else "Disconnected"
    text = (
        f"Relay: {RELAY_WS_SAFE}\n"
        f"Status: {status}\n"
        f"Agents: {len(agents)} ({b} blocked, {w} working, {d} done, {i} idle)"
    )
    await update.message.reply_text(text)


async def cmd_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /read [project] — read pane output."""
    if not authorized(update):
        return
    args = ctx.args
    if not args:
        # Show agent picker
        if not agents:
            await update.message.reply_text("No agents. Use /agents to check.")
            return
        await update.message.reply_text("Read which agent?", reply_markup=build_agent_keyboard("read"))
        return

    # Find agent by project name
    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'. Use /agents to see list.")
        return

    content = await read_pane(match["pane_id"])
    await send_pane_output(update.message, update.effective_chat, match, content)


async def cmd_interrupt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /interrupt [project] — send the canonical C-c key."""
    if not authorized(update):
        return
    args = ctx.args
    if not args:
        if not agents:
            await update.message.reply_text("No agents.")
            return
        working = [a for a in agents if a.get("status") in ("working", "blocked")]
        if not working:
            await update.message.reply_text("No active agents to interrupt.")
            return
        await update.message.reply_text(
            "Interrupt which agent?",
            reply_markup=build_agent_keyboard("interrupt", agent_list=working),
        )
        return

    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'.")
        return

    try:
        await send_keys_to_relay(match["pane_id"], ["C-c"])
        await update.message.reply_text(f"Sent Ctrl+C to {match['project']}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /send [project] [text] — submit a prompt through herdr."""
    if not authorized(update):
        return
    args = ctx.args
    if not args:
        if not agents:
            await update.message.reply_text("No agents.")
            return
        await update.message.reply_text(
            "Send to which agent?\n(After selecting, reply with your text)",
            reply_markup=build_agent_keyboard("select_send"),
        )
        return

    query = args[0].lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'. Use /agents to see list.")
        return

    text = " ".join(args[1:])
    if not text:
        await send_reply_prompt(update.message, update.effective_chat, match)
        return

    try:
        await send_agent_prompt_to_relay(match["pane_id"], text)
        await update.message.reply_text(f"Sent to {match['project']}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /reply [project] — show agent output then accept input."""
    if not authorized(update):
        return
    args = ctx.args

    if not agents:
        await update.message.reply_text("No agents.")
        return

    if not args:
        await update.message.reply_text("Reply to which agent?", reply_markup=build_agent_keyboard("select_reply"))
        return

    query = " ".join(args).lower()
    match = next((a for a in agents if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No agent matching '{query}'.")
        return

    content = await read_pane(match["pane_id"])
    await send_reply_prompt(update.message, update.effective_chat, match, content)


async def cmd_trust(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /trust [project] — offer a second-step persistent-trust confirmation."""
    if not authorized(update):
        return
    if not ALLOW_PERSISTENT_TRUST:
        await update.message.reply_text(
            "Persistent trust is disabled. Use the one-time approval button instead."
        )
        return
    args = ctx.args
    blocked = [a for a in agents if a.get("status") == "blocked"]

    if not blocked:
        await update.message.reply_text("No blocked agents.")
        return

    if not args:
        await update.message.reply_text(
            "Trust which agent?",
            reply_markup=build_agent_keyboard("trust", agent_list=blocked),
        )
        return

    query = " ".join(args).lower()
    match = next((a for a in blocked if query in a.get("project", "").lower() or query in a.get("agent", "").lower()), None)
    if not match:
        await update.message.reply_text(f"No blocked agent matching '{query}'.")
        return

    await offer_trust_confirmation(update.message, match)


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /digest — show today's agent activity summary."""
    if not authorized(update):
        return
    if not daily_stats:
        await update.message.reply_text("No activity recorded yet today.")
        return

    import time
    lines = ["Today's activity:\n"]
    sorted_agents = sorted(daily_stats.values(), key=lambda x: x.get("working_mins", 0), reverse=True)
    for s in sorted_agents:
        blocked = f", blocked {s['blocked_count']}x" if s.get("blocked_count") else ""
        mins = s.get("working_mins", 0)
        time_str = f"{mins}m" if mins < 60 else f"{mins//60}h{mins%60}m"
        lines.append(f"  {s['project']} ({s['agent']}): {time_str} working{blocked}")

    await update.message.reply_text("\n".join(lines))


def trust_confirmation_keyboard(pane_id: str, generation: str, key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "Confirm persistent trust",
            callback_data=pane_callback_data("approval", pane_id, g=generation, k=key),
        )],
        [InlineKeyboardButton(
            "Cancel",
            callback_data=pane_callback_data("cancel", pane_id, g=generation),
        )],
    ])


async def offer_trust_confirmation(message, agent: dict, generation: str | None = None, key: str | None = None):
    if not ALLOW_PERSISTENT_TRUST:
        await message.reply_text("Persistent trust is disabled. Use one-time approval instead.")
        return
    pane_id = agent["pane_id"]
    generation = generation or approval_tokens.get(pane_id)
    key = key or approval_trust_keys.get(pane_id)
    if not generation or generation != approval_tokens.get(pane_id):
        await message.reply_text(
            "No current blocked prompt is available for persistent trust. Use the latest notification."
        )
        return
    if not key:
        await message.reply_text("The current blocked prompt does not offer persistent trust.")
        return
    await message.reply_text(
        f"Confirm persistent trust for {agent.get('project') or agent.get('agent') or 'this agent'}?\n\n"
        "This allows future tool requests without asking again for this agent session.",
        reply_markup=trust_confirmation_keyboard(pane_id, generation, key),
    )


# --- Callback handler (buttons) ---

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    if not authorized(update):
        await query.answer("Unauthorized")
        return
    await query.answer()

    data = parse_callback_data(query.data)
    action = data.get("action", "approval")

    if action == "dashboard":
        await query.message.reply_text(dashboard_text(), reply_markup=dashboard_keyboard())
        return

    if action == "help":
        await query.message.reply_text(
            help_text(),
            parse_mode="HTML",
            reply_markup=help_keyboard(),
        )
        return

    if action == "browse":
        await send_directory_browser(query.message, update.effective_chat)
        return

    if action == "new_codex":
        selected = selected_workspace_dirs.get(int(update.effective_chat.id))
        if not selected:
            await query.message.reply_text("Select a repository before starting Codex.")
            await send_directory_browser(query.message, update.effective_chat)
            return
        await query.message.reply_text(
            f"Start a new Codex agent in:\n{selected}",
            reply_markup=selected_directory_keyboard(selected),
        )
        return

    if action == "dir":
        operation = data.get("o", "")
        token = data.get("d", "")
        path = resolve_directory_token(token) if token else ""
        if token and not path:
            await query.message.reply_text("That directory button expired. Open /browse again.")
            return
        if operation == "o":
            await send_directory_browser(
                query.message,
                update.effective_chat,
                path or None,
                page=data.get("g", 0),
            )
            return
        if operation == "s" and path:
            try:
                listing = await list_directories_from_relay(path)
            except Exception as e:
                await query.message.reply_text(f"Cannot select that directory: {scrub(e)}")
                return
            selected_workspace_dirs[int(update.effective_chat.id)] = listing["path"]
            await query.message.reply_text(
                f"Selected workspace:\n{listing['display_path']}",
                reply_markup=selected_directory_keyboard(
                    listing["path"],
                    can_start_agent=listing.get("can_start_agent", False),
                ),
            )
            return
        if operation == "c" and path:
            await start_codex_for_chat(query.message, update.effective_chat, path)
            return
        await query.message.reply_text("That directory action is no longer valid. Open /browse again.")
        return

    if action == "picker":
        if not relay_connected:
            await query.message.reply_text("Relay disconnected. Use Refresh after it reconnects.")
            return
        menu = data.get("menu", "")
        labels = {
            "read": "Read which agent?",
            "select_reply": "Open and reply to which agent?",
            "select_send": "Send a new prompt to which agent?",
            "interrupt": "Interrupt which active agent?",
        }
        if menu not in labels:
            await query.message.reply_text("That shortcut is no longer valid. Open /start again.")
            return
        eligible_agents = agents_for_action(menu)
        if not eligible_agents:
            await query.message.reply_text("No eligible agents are available for that action.")
            return
        await query.message.reply_text(
            labels[menu],
            reply_markup=build_agent_keyboard(menu, agent_list=eligible_agents),
        )
        return

    if not relay_connected:
        await query.message.reply_text("Relay disconnected. Use /start after it reconnects.")
        return

    if action == "page":
        menu = data.get("menu", "select_reply")
        if not agents_for_action(menu):
            await query.message.reply_text("No eligible agents are available.")
            return
        await query.edit_message_reply_markup(
            reply_markup=build_agent_keyboard(menu, page=data.get("page", 0))
        )
        return

    selected_agent = None
    if action in {"read", "interrupt", "select_send", "select_reply", "trust", "review_trust", "approval"}:
        eligible_action = "trust" if action in {"approval", "review_trust"} else action
        selected_agent = next(
            (agent for agent in agents_for_action(eligible_action) if agent.get("pane_id") == data.get("pane_id")),
            None,
        )
        if selected_agent is None:
            await query.message.reply_text(
                "That agent is no longer available for this action. Refresh the list and choose another agent.",
                reply_markup=refresh_keyboard(eligible_action),
            )
            return

    if action == "read":
        content = await read_pane(data["pane_id"])
        await send_pane_output(query.message, update.effective_chat, selected_agent, content)
        return

    if action == "interrupt":
        try:
            await send_keys_to_relay(data["pane_id"], ["C-c"])
            await query.message.reply_text("Sent Ctrl+C")
        except Exception as e:
            await query.message.reply_text(f"Failed: {scrub(e)}")
        return

    if action == "select_send":
        await send_reply_prompt(query.message, update.effective_chat, selected_agent)
        return

    if action == "select_reply":
        content = await read_pane(data["pane_id"])
        await send_reply_prompt(query.message, update.effective_chat, selected_agent, content)
        return

    if action == "trust":
        await offer_trust_confirmation(query.message, selected_agent)
        return

    if action == "review_trust":
        generation = data.get("g", "")
        if not generation or generation != approval_tokens.get(data["pane_id"]):
            await query.message.reply_text(
                "That approval belongs to an older prompt. Use the controls on the latest blocked notification."
            )
            return
        await offer_trust_confirmation(
            query.message,
            selected_agent,
            generation=generation,
            key=data.get("k", "2"),
        )
        return

    if action == "cancel":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Cancelled.")
        return

    if action != "approval":
        await query.message.reply_text("That action is no longer valid. Refresh the list and try again.")
        return

    pane_id = data["pane_id"]
    if not data.get("g") or data["g"] != approval_tokens.get(pane_id):
        await query.message.reply_text(
            "That approval belongs to an older prompt. Use the controls on the latest blocked notification."
        )
        return

    # Confirm a blocked agent's prompt by pressing the option number.
    # Sending the option *text* via `respond` does NOT work: the relay pastes it via
    # send-text, and Claude's TUI treats a pasted trailing newline as paste content,
    # not as Enter, so the prompt never gets confirmed. A real key press does.
    key = data.get("k")
    if key is None:
        await query.message.reply_text("That approval action is no longer supported. Use the latest notification.")
        return
    # Recover the pressed button's label for the confirmation (kept out of
    # callback_data to respect Telegram's 64-byte limit).
    label = f"option {key}"
    if query.message and query.message.reply_markup:
        for row in query.message.reply_markup.inline_keyboard:
            for btn in row:
                try:
                    if json.loads(btn.callback_data).get("k") == key:
                        label = btn.text
                except (ValueError, TypeError):
                    pass
    try:
        await send_keys_to_relay(pane_id, [key])
    except Exception as e:
        await query.message.reply_text(f"Failed: {scrub(e)}")
        return
    approval_tokens.pop(pane_id, None)
    approval_trust_keys.pop(pane_id, None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(f"Sent: {label}")


# --- Free text reply ---

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Send a Telegram reply to its associated agent pane."""
    if not authorized(update) or not update.message.reply_to_message:
        return
    pane_id = pending_pane(update.effective_chat.id, update.message.reply_to_message.message_id)
    if not pane_id:
        return
    if not relay_connected:
        await update.message.reply_text("Relay disconnected. Use /start after it reconnects.")
        return
    if find_agent(pane_id) is None:
        await update.message.reply_text(
            "That agent is no longer available. Refresh the list and choose another agent.",
            reply_markup=refresh_keyboard("select_reply"),
        )
        return

    try:
        await send_agent_prompt_to_relay(pane_id, update.message.text)
        await update.message.reply_text("Sent")
    except Exception as e:
        await update.message.reply_text(f"Failed: {scrub(e)}")


# --- Blocked notification ---

TOOL_BUTTONS = [
    ("Yes (once)", "yes, single permission"),
    ("Trust (always)", "trust, always allow"),
    ("No", "no (tab to edit)"),
]

SUBAGENT_BUTTONS = [
    ("Approve all", "approve all pending"),
    ("Configure", "configure individually"),
    ("Cancel", "exit (cancel subagents)"),
]


def make_keyboard(
    pane_id: str,
    options: list[str] | None,
    generation: str | None = None,
) -> InlineKeyboardMarkup:
    if options and "trust" in " ".join(options).lower():
        buttons = TOOL_BUTTONS
    elif options and "approve all" in " ".join(options).lower():
        buttons = SUBAGENT_BUTTONS
    else:
        buttons = [(opt.split(",")[0], opt) for opt in (options or ["yes, single permission", "no (tab to edit)"])]

    # Encode the option's 1-based position as "k"; the callback presses that number
    # key on the agent's prompt. Sending the option *text* does not work (see handle_callback).
    # callback_data must stay under Telegram's 64-byte limit, so keep it to the
    # pane token and the option number; the confirmation label is recovered from the
    # keyboard on press (see handle_callback).
    generation = generation or secrets.token_hex(4)
    keyboard = []
    for i, (label, response) in enumerate(buttons):
        is_persistent_trust = "trust" in response.lower()
        if is_persistent_trust and not ALLOW_PERSISTENT_TRUST:
            continue
        action = "review_trust" if is_persistent_trust else "approval"
        keyboard.append([InlineKeyboardButton(
            label,
            callback_data=pane_callback_data(action, pane_id, g=generation, k=str(i + 1)),
        )])
    keyboard.append([InlineKeyboardButton(
        "Open output & reply",
        callback_data=pane_callback_data("select_reply", pane_id),
    )])
    return InlineKeyboardMarkup(keyboard)


def interaction_keyboard(pane_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "Open output & reply",
            callback_data=pane_callback_data("select_reply", pane_id),
        )
    ]])


async def notify_blocked(app: Application, pane_id: str, agent: str, project: str, prompt: str, options: list[str] | None):
    if not CHAT_ID:
        return
    text = (
        f"{agent} blocked in {project}\n\n{prompt[:400]}\n\n"
        "Use an approval button, open the output, or reply to this notification."
    )
    generation = secrets.token_hex(4)
    keyboard = make_keyboard(pane_id, options, generation=generation)
    msg = await app.bot.send_message(
        chat_id=int(CHAT_ID), text=text, reply_markup=keyboard
    )
    approval_tokens[pane_id] = generation
    trust_key = next(
        (str(index + 1) for index, option in enumerate(options or []) if "trust" in option.lower()),
        None,
    )
    if trust_key and ALLOW_PERSISTENT_TRUST:
        approval_trust_keys[pane_id] = trust_key
    else:
        approval_trust_keys.pop(pane_id, None)
    register_pending(int(CHAT_ID), msg.message_id, pane_id)


async def notify_blocked_safely(app: Application, msg: dict):
    try:
        await notify_blocked(
            app,
            pane_id=msg["pane_id"],
            agent=msg.get("agent", "unknown"),
            project=msg.get("project", ""),
            prompt=msg.get("prompt", ""),
            options=msg.get("options"),
        )
    except Exception as e:
        log.warning("Failed to send blocked notification: %s", scrub(e))


# --- Relay listener ---

async def track_agent_updates(app: Application, updated_agents: list[dict]):
    """Track status transitions for full snapshots and pane-level updates."""
    if not CHAT_ID:
        return

    import time
    now = time.time()
    for agent_data in updated_agents:
        pane_id = agent_data["pane_id"]
        new_status = agent_data.get("status", "unknown")
        old_status = prev_statuses.get(pane_id)

        if pane_id not in daily_stats:
            daily_stats[pane_id] = {
                "agent": agent_data.get("agent", ""),
                "project": agent_data.get("project", ""),
                "blocked_count": 0,
                "working_mins": 0,
                "last_change": now,
            }
        stats = daily_stats[pane_id]
        if old_status == "working" and old_status != new_status:
            stats["working_mins"] += int((now - stats["last_change"]) / 60)
        if new_status == "blocked" and old_status != "blocked":
            stats["blocked_count"] += 1
        if old_status != new_status:
            stats["last_change"] = now
        if agent_data.get("status") and new_status != "blocked":
            approval_tokens.pop(pane_id, None)
            approval_trust_keys.pop(pane_id, None)

        if old_status and old_status != new_status and new_status in ("idle", "done") and old_status in ("working", "blocked"):
            try:
                msg = await app.bot.send_message(
                    chat_id=int(CHAT_ID),
                    text=(
                        f"{agent_data['project']} ({agent_data['agent']}) finished.\n\n"
                        "Open the output below, or reply to this notification to send a follow-up."
                    ),
                    reply_markup=interaction_keyboard(pane_id),
                )
                register_pending(int(CHAT_ID), msg.message_id, pane_id)
            except Exception as e:
                log.warning("Failed to send completion notification: %s", scrub(e))
        prev_statuses[pane_id] = new_status


async def relay_listener(app: Application):
    """Persistent WebSocket connection to relay."""
    import websockets
    global agents, relay_connected, prev_statuses

    while True:
        try:
            async with websockets.connect(RELAY_WS) as ws:
                relay_connected = True
                log.info(f"Connected to relay at {RELAY_WS_SAFE}")
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if msg.get("type") == "agents":
                        new_agents = msg.get("agents", [])
                        blocked_panes = {
                            agent.get("pane_id") for agent in new_agents
                            if agent.get("status") == "blocked"
                        }
                        for pane_id in list(approval_tokens):
                            if pane_id not in blocked_panes:
                                approval_tokens.pop(pane_id, None)
                                approval_trust_keys.pop(pane_id, None)
                        await track_agent_updates(app, new_agents)
                        agents = apply_agent_message(agents, msg)

                    elif msg.get("type") == "agent_update":
                        updated_agent = msg.get("agent") or {}
                        if updated_agent.get("pane_id"):
                            await track_agent_updates(app, [updated_agent])
                            agents = apply_agent_message(agents, msg)

                    elif msg.get("type") == "blocked":
                        await notify_blocked_safely(app, msg)
                clear_relay_connection_state()
        except Exception as e:
            clear_relay_connection_state()
            log.warning("Relay connection lost: %s, reconnecting in 5s...", scrub(e))
            await asyncio.sleep(5)


# --- Main ---

def validate_runtime_config():
    missing = []
    if not CHAT_ID:
        missing.append("HERDR_TG_CHAT_ID")
    if not USER_ID:
        missing.append("HERDR_TG_USER_ID")
    if not _RELAY_TOKEN:
        missing.append("token in HERDR_RELAY")
    if missing:
        raise RuntimeError("Missing required secure configuration: " + ", ".join(missing))
    try:
        chat_id = int(CHAT_ID)
        int(USER_ID)
    except ValueError as e:
        raise RuntimeError("Telegram chat and user IDs must be integers") from e
    if REQUIRE_PRIVATE_CHAT and chat_id <= 0:
        raise RuntimeError("Telegram-only secure mode requires a private chat ID")
    if REQUIRE_LOCAL_RELAY:
        hostname = _relay_parts.hostname or ""
        try:
            is_loopback = hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise RuntimeError("Telegram-only secure mode requires a loopback HERDR_RELAY URL")


def main():
    try:
        validate_runtime_config()
    except RuntimeError as e:
        log.error("Configuration error: %s", e)
        return 1

    app = Application.builder().token(TOKEN).build()
    auth_filter = filters.Chat(chat_id=int(CHAT_ID)) & filters.User(user_id=int(USER_ID))

    app.add_handler(CommandHandler("start", cmd_start, filters=auth_filter))
    app.add_handler(CommandHandler("agents", cmd_agents, filters=auth_filter))
    app.add_handler(CommandHandler("status", cmd_status, filters=auth_filter))
    app.add_handler(CommandHandler("read", cmd_read, filters=auth_filter))
    app.add_handler(CommandHandler("reply", cmd_reply, filters=auth_filter))
    app.add_handler(CommandHandler("send", cmd_send, filters=auth_filter))
    app.add_handler(CommandHandler("trust", cmd_trust, filters=auth_filter))
    app.add_handler(CommandHandler("digest", cmd_digest, filters=auth_filter))
    app.add_handler(CommandHandler("interrupt", cmd_interrupt, filters=auth_filter))
    app.add_handler(CommandHandler("help", cmd_help, filters=auth_filter))
    app.add_handler(CommandHandler("browse", cmd_browse, filters=auth_filter))
    app.add_handler(CommandHandler("cd", cmd_cd, filters=auth_filter))
    app.add_handler(CommandHandler("cwd", cmd_cwd, filters=auth_filter))
    app.add_handler(CommandHandler("codex", cmd_codex, filters=auth_filter))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & auth_filter, handle_text))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run():
        async with app:
            await app.start()
            await configure_bot_ui(app)
            await app.updater.start_polling()
            await relay_listener(app)

    loop.run_until_complete(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
