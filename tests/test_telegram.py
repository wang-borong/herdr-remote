#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-telegram-bot>=21.0", "websockets>=14.0"]
# ///
import ast
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))
os.environ.setdefault("HERDR_TG_TOKEN", "test-token")

tg = importlib.import_module("herdr_telegram")


def make_agents(count, *, status="idle", project="project"):
    return [
        {
            "pane_id": f"w{i}:p1",
            "agent": "opencode",
            "status": status,
            "project": project,
            "cwd": f"/work/{project}/{i}",
            "host": "local",
        }
        for i in range(count)
    ]


def make_directory_listing(
    path="/home/wbr/Workspace/others",
    *,
    can_start_agent=False,
    source_id="local",
    source_label="本机",
):
    return {
        "type": "directory_listing",
        "source_id": source_id,
        "source_label": source_label,
        "path": path,
        "display_path": path.replace("/home/wbr", "~"),
        "parent": "/home/wbr/Workspace" if path else None,
        "entries": [
            {
                "name": "herdr-remote",
                "path": f"{path}/herdr-remote",
                "display_path": "~/Workspace/others/herdr-remote",
                "is_repo": True,
            },
            {
                "name": "notes",
                "path": f"{path}/notes",
                "display_path": "~/Workspace/others/notes",
                "is_repo": False,
            },
        ],
        "can_start_agent": can_start_agent,
        "truncated": False,
    }


class FakeMessage:
    def __init__(self, chat_id=42, chat_type="private", message_id=10):
        self.replies = []
        self.message_id = message_id
        self.chat_id = chat_id
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.reply_markup = None
        self.reply_to_message = None
        self.text = ""

    async def reply_text(self, text, **kwargs):
        sent = SimpleNamespace(message_id=self.message_id * 100 + len(self.replies), chat_id=self.chat_id)
        self.replies.append((text, kwargs, sent))
        return sent


class FakeCallback:
    def __init__(self, data, chat_id=42, chat_type="private", message_id=10):
        self.data = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        self.message = FakeMessage(chat_id, chat_type, message_id)
        self.answers = []
        self.edited_markup = None
        self.edit_calls = 0

    async def answer(self, text=None):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edit_calls += 1
        self.edited_markup = reply_markup


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        message = SimpleNamespace(message_id=500 + len(self.sent), chat_id=chat_id)
        self.sent.append((chat_id, text, kwargs, message))
        return message


class FakeRelayConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        return json.dumps(next(self.responses))

    def __aiter__(self):
        async def empty_messages():
            if False:
                yield None
        return empty_messages()


def make_update(chat_id=42, chat_type="private", user_id=42, callback=None, message=None):
    if callback is not None:
        callback.message.chat_id = chat_id
        callback.message.chat = SimpleNamespace(id=chat_id, type=chat_type)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        effective_user=SimpleNamespace(id=user_id),
        message=message or FakeMessage(chat_id, chat_type),
        callback_query=callback,
    )


def make_active_approval_keyboard(pane_id, options):
    markup = tg.make_keyboard(pane_id, options)
    generation = json.loads(markup.inline_keyboard[0][0].callback_data)["g"]
    tg.approval_tokens[pane_id] = generation
    tg.blocked_prompt_ids[pane_id] = "prompt-123"
    return markup


class TelegramDashboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_chat_id = tg.CHAT_ID
        self.old_user_id = tg.USER_ID
        self.old_require_private_chat = tg.REQUIRE_PRIVATE_CHAT
        self.old_allow_persistent_trust = tg.ALLOW_PERSISTENT_TRUST
        tg.CHAT_ID = "42"
        tg.USER_ID = "42"
        tg.REQUIRE_PRIVATE_CHAT = True
        tg.ALLOW_PERSISTENT_TRUST = True
        tg.agents = []
        tg.agent_sources = []
        tg.relay_connected = False
        tg.pending.clear()
        tg.selected_agent_sources.clear()
        tg.selected_workspace_dirs.clear()
        tg.directory_path_tokens.clear()
        tg.approval_tokens.clear()
        tg.approval_trust_keys.clear()
        tg.blocked_prompt_ids.clear()
        tg.prev_statuses.clear()
        tg.daily_stats.clear()

    def tearDown(self):
        tg.CHAT_ID = self.old_chat_id
        tg.USER_ID = self.old_user_id
        tg.REQUIRE_PRIVATE_CHAT = self.old_require_private_chat
        tg.ALLOW_PERSISTENT_TRUST = self.old_allow_persistent_trust
        tg.agents = []
        tg.agent_sources = []
        tg.relay_connected = False
        tg.selected_agent_sources.clear()
        tg.selected_workspace_dirs.clear()
        tg.directory_path_tokens.clear()
        tg.approval_tokens.clear()
        tg.approval_trust_keys.clear()
        tg.blocked_prompt_ids.clear()
        tg.prev_statuses.clear()
        tg.daily_stats.clear()

    async def test_start_rejects_unauthorized_chat(self):
        update = make_update(chat_id=7)

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        self.assertEqual(update.message.replies, [])

    async def test_start_fails_closed_without_authorized_identity(self):
        tg.CHAT_ID = ""
        update = make_update(chat_id=-123)

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        self.assertEqual(update.message.replies, [])

    async def test_start_rejects_unauthorized_user_in_authorized_chat(self):
        update = make_update(user_id=7)

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        self.assertEqual(update.message.replies, [])

    def test_runtime_configuration_requires_two_level_auth_and_relay_token(self):
        with (
            patch.object(tg, "CHAT_ID", "42"),
            patch.object(tg, "USER_ID", "42"),
            patch.object(tg, "_RELAY_TOKEN", "a" * 32),
        ):
            tg.validate_runtime_config()

        with patch.object(tg, "USER_ID", ""):
            with self.assertRaisesRegex(RuntimeError, "HERDR_TG_USER_ID"):
                tg.validate_runtime_config()

        with patch.object(tg, "_RELAY_TOKEN", ""):
            with self.assertRaisesRegex(RuntimeError, "token in HERDR_RELAY"):
                tg.validate_runtime_config()

        with (
            patch.object(tg, "_RELAY_TOKEN", "a" * 32),
            patch.object(tg, "_relay_parts", tg.urllib.parse.urlsplit("ws://example.com:8375?token=abc")),
        ):
            with self.assertRaisesRegex(RuntimeError, "loopback HERDR_RELAY"):
                tg.validate_runtime_config()

    def test_codex_output_keeps_timing_and_removes_trailing_tui_chrome(self):
        content = (
            "\x1b[32mImplemented <feature> & tests.\x1b[0m\n\n"
            "─ Worked for 10m 35s ─────────────────────────\n\n"
            "› Explain this codebase\n\n"
            "  gpt-5.6-sol max · ~/Workspace/others/herdr-remote\n"
        )

        cleaned = tg.clean_pane_output(content)

        self.assertEqual(cleaned, "Implemented <feature> & tests.\n\nWorked for 10m 35s")
        self.assertNotIn("Explain this codebase", cleaned)
        self.assertNotIn("gpt-5.6-sol", cleaned)

    def test_long_terminal_dividers_become_a_single_blank_line(self):
        content = (
            "First section\n"
            "────────────────────────────────────────────────────────────────\n"
            "\n"
            "Second section"
        )

        cleaned = tg.clean_pane_output(content)

        self.assertEqual(cleaned, "First section\n\nSecond section")
        self.assertNotIn("────────", cleaned)

    async def test_read_pane_requests_more_context_by_default(self):
        connection = FakeRelayConnection([{"type": "pane_content", "content": "recent output"}])

        with patch("websockets.connect", return_value=connection):
            content = await tg.read_pane("w0:p1")

        self.assertEqual(content, "recent output")
        self.assertEqual(connection.sent[0]["lines"], 60)

    async def test_read_pane_skips_any_number_of_snapshot_messages(self):
        connection = FakeRelayConnection([
            {"type": "session"},
            {"type": "agent_sources", "sources": []},
            {"type": "agents", "agents": []},
            *(
                {"type": "blocked", "pane_id": f"w{index}:p1"}
                for index in range(8)
            ),
            {"type": "pane_content", "content": "recent output"},
        ])

        with patch("websockets.connect", return_value=connection):
            content = await tg.read_pane("w0:p1")

        self.assertEqual(content, "recent output")

    async def test_long_reply_output_is_split_and_safely_formatted_as_html(self):
        agent = make_agents(1)[0]
        message = FakeMessage()
        chat = SimpleNamespace(id=42, type="private")
        content = "<unsafe>&\n" + ("long output line\n" * 12)

        with patch.object(tg, "PANE_CHUNK_ESCAPED_CHARS", 80):
            final_message = await tg.send_reply_prompt(message, chat, agent, content)

        self.assertGreater(len(message.replies), 1)
        self.assertIn("&lt;unsafe&gt;&amp;", message.replies[0][0])
        self.assertTrue(all(kwargs["parse_mode"] == "HTML" for _, kwargs, _ in message.replies))
        self.assertTrue(all(len(text) < 4096 for text, _, _ in message.replies))
        self.assertNotIn("reply_markup", message.replies[0][1])
        self.assertIsInstance(message.replies[-1][1]["reply_markup"], tg.ForceReply)
        self.assertEqual(
            sum("Reply to this message" in text for text, _, _ in message.replies),
            1,
        )
        self.assertIs(final_message, message.replies[-1][2])
        self.assertTrue(all(
            tg.pending_pane(42, sent.message_id) == "w0:p1"
            for _, _, sent in message.replies
        ))

    async def test_start_reports_disconnected_and_empty_states(self):
        disconnected = make_update()
        await tg.cmd_start(disconnected, SimpleNamespace(args=[]))
        self.assertIn("disconnected", disconnected.message.replies[0][0].lower())
        disconnected_markup = disconnected.message.replies[0][1]["reply_markup"]
        disconnected_actions = {
            tg.parse_callback_data(button.callback_data)["action"]
            for row in disconnected_markup.inline_keyboard
            for button in row
        }
        self.assertEqual(
            disconnected_actions,
            {"picker", "dashboard", "help", "hosts", "browse", "new_codex"},
        )

        tg.relay_connected = True
        empty = make_update()
        await tg.cmd_start(empty, SimpleNamespace(args=[]))
        self.assertIn("no agents", empty.message.replies[0][0].lower())
        self.assertEqual(len(empty.message.replies[0][1]["reply_markup"].inline_keyboard), 3)

    async def test_start_lists_current_sixteen_agent_herd(self):
        tg.relay_connected = True
        tg.agents = make_agents(16)
        update = make_update()

        await tg.cmd_start(update, SimpleNamespace(args=[]))

        markup = update.message.replies[0][1]["reply_markup"]
        agent_buttons = [
            button
            for row in markup.inline_keyboard
            for button in row
            if tg.parse_callback_data(button.callback_data).get("action") == "select_reply"
        ]
        self.assertEqual(len(agent_buttons), 16)
        self.assertTrue(all(tg.parse_callback_data(button.callback_data)["action"] == "select_reply" for button in agent_buttons))

    async def test_dashboard_shortcuts_open_send_and_interrupt_pickers(self):
        tg.relay_connected = True
        tg.agents = make_agents(2, status="working")

        for menu, expected_action in (("select_send", "select_send"), ("interrupt", "interrupt")):
            callback = FakeCallback(tg.simple_callback_data("picker", menu=menu))
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

            markup = callback.message.replies[0][1]["reply_markup"]
            actions = [
                tg.parse_callback_data(row[0].callback_data)["action"]
                for row in markup.inline_keyboard
            ]
            self.assertEqual(actions, [expected_action, expected_action])

    async def test_help_command_and_button_return_command_guidance(self):
        command_update = make_update()
        await tg.cmd_help(command_update, SimpleNamespace(args=[]))

        command_text, command_kwargs, _ = command_update.message.replies[0]
        self.assertIn("/start", command_text)
        self.assertIn("/hosts", command_text)
        self.assertIn("命令补全", command_text)
        self.assertEqual(command_kwargs["parse_mode"], "HTML")

        callback = FakeCallback(tg.simple_callback_data("help"))
        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())
        self.assertIn("/interrupt", callback.message.replies[0][0])

    async def test_command_completion_is_scoped_to_authorized_chat(self):
        bot = SimpleNamespace(
            set_my_commands=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
        )

        with patch.object(tg, "ALLOW_PERSISTENT_TRUST", False):
            await tg.configure_bot_ui(SimpleNamespace(bot=bot))

        commands = bot.set_my_commands.await_args.args[0]
        scope = bot.set_my_commands.await_args.kwargs["scope"]
        self.assertEqual(scope.chat_id, 42)
        self.assertEqual(
            [command.command for command in commands],
            [
                "start", "agents", "status", "hosts", "read", "reply", "send", "interrupt",
                "digest", "browse", "cd", "cwd", "codex", "help",
            ],
        )
        self.assertNotIn("trust", [command.command for command in commands])
        self.assertEqual(bot.set_chat_menu_button.await_args.kwargs["chat_id"], 42)
        self.assertIsInstance(
            bot.set_chat_menu_button.await_args.kwargs["menu_button"],
            tg.MenuButtonCommands,
        )

    async def test_directory_and_codex_requests_include_selected_source(self):
        listing = make_directory_listing(source_id="server", source_label="Herdr 192.168.2.99")
        with patch.object(tg, "relay_request", AsyncMock(return_value=listing)) as request:
            await tg.list_directories_from_relay("~/Workspace", source_id="server")

        request.assert_awaited_once_with(
            {
                "type": "list_directories",
                "source_id": "server",
                "path": "~/Workspace",
            },
            "directory_listing",
        )

        started = {
            "pane_id": "server::w9:p1",
            "source_id": "server",
            "cwd": "/home/wbr/Workspace/project",
        }
        with patch.object(
            tg,
            "relay_request",
            AsyncMock(return_value={"type": "agent_started", "agent": started}),
        ) as request:
            await tg.start_codex_from_relay(
                "/home/wbr/Workspace/project",
                "run tests",
                source_id="server",
            )

        request.assert_awaited_once_with(
            {
                "type": "start_agent",
                "kind": "codex",
                "source_id": "server",
                "cwd": "/home/wbr/Workspace/project",
                "prompt": "run tests",
            },
            "agent_started",
            timeout=90,
        )

    async def test_hosts_command_selects_online_remote_source_and_opens_its_roots(self):
        tg.relay_connected = True
        tg.agent_sources = [
            {
                "id": "local",
                "label": "本机",
                "status": "online",
                "agent_count": 2,
                "can_browse": True,
            },
            {
                "id": "server",
                "label": "Herdr 192.168.2.99",
                "status": "online",
                "agent_count": 1,
                "can_browse": True,
            },
        ]
        tg.selected_workspace_dirs[42] = "/home/wbr/Workspace/local-project"
        update = make_update()

        await tg.cmd_hosts(update, SimpleNamespace(args=[]))

        text, kwargs, _ = update.message.replies[0]
        self.assertIn("Codex hosts", text)
        remote_button = kwargs["reply_markup"].inline_keyboard[1][0]
        self.assertIn("Herdr 192.168.2.99", remote_button.text)
        self.assertLessEqual(len(remote_button.callback_data.encode()), 64)
        self.assertLessEqual(
            len(tg.simple_callback_data("source", s="x" * 32).encode()),
            64,
        )

        callback = FakeCallback(remote_button.callback_data)
        listing = make_directory_listing(
            path="",
            source_id="server",
            source_label="Herdr 192.168.2.99",
        )
        with patch.object(
            tg,
            "list_directories_from_relay",
            AsyncMock(return_value=listing),
        ) as browse:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        browse.assert_awaited_once_with(None, source_id="server")
        self.assertEqual(tg.selected_agent_sources[42], "server")
        self.assertNotIn(42, tg.selected_workspace_dirs)
        self.assertIn("Selected Codex host", callback.message.replies[0][0])
        self.assertIn("Host: <b>Herdr 192.168.2.99</b>", callback.message.replies[1][0])

    async def test_offline_remote_source_cannot_replace_current_host(self):
        tg.relay_connected = True
        tg.agent_sources = [
            {"id": "local", "label": "本机", "status": "online", "can_browse": True},
            {
                "id": "server",
                "label": "Herdr 192.168.2.99",
                "status": "offline",
                "error": "SSH is unavailable",
                "can_browse": False,
            },
        ]
        callback = FakeCallback(tg.simple_callback_data("source", s="server"))

        with patch.object(tg, "list_directories_from_relay", AsyncMock()) as browse:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        browse.assert_not_awaited()
        self.assertEqual(tg.selected_agent_source_id(42), "local")
        self.assertIn("SSH is unavailable", callback.message.replies[0][0])

    async def test_browse_command_builds_opaque_directory_buttons(self):
        listing = make_directory_listing()
        update = make_update()

        with patch.object(tg, "list_directories_from_relay", AsyncMock(return_value=listing)) as browse:
            await tg.cmd_browse(update, SimpleNamespace(args=["~/Workspace/others"]))

        browse.assert_awaited_once_with("~/Workspace/others", source_id="local")
        text, kwargs, _ = update.message.replies[0]
        self.assertIn("Workspace browser", text)
        self.assertEqual(kwargs["parse_mode"], "HTML")
        first_button = kwargs["reply_markup"].inline_keyboard[0][0]
        callback = json.loads(first_button.callback_data)
        self.assertEqual(callback["action"], "dir")
        self.assertNotIn("/home/wbr", first_button.callback_data)
        self.assertEqual(
            tg.resolve_directory_token(callback["d"]),
            "/home/wbr/Workspace/others/herdr-remote",
        )

    async def test_remote_directory_buttons_preserve_source_scope(self):
        listing = make_directory_listing(
            source_id="server",
            source_label="Herdr 192.168.2.99",
        )
        button = tg.directory_browser_keyboard(listing).inline_keyboard[0][0]
        data = json.loads(button.callback_data)
        self.assertEqual(
            tg.resolve_directory_location(data["d"]),
            ("server", "/home/wbr/Workspace/others/herdr-remote"),
        )

        callback = FakeCallback(button.callback_data)
        child_listing = make_directory_listing(
            "/home/wbr/Workspace/others/herdr-remote",
            can_start_agent=True,
            source_id="server",
            source_label="Herdr 192.168.2.99",
        )
        with patch.object(
            tg,
            "list_directories_from_relay",
            AsyncMock(return_value=child_listing),
        ) as browse:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        browse.assert_awaited_once_with(
            "/home/wbr/Workspace/others/herdr-remote",
            source_id="server",
        )
        self.assertEqual(tg.selected_agent_sources[42], "server")

    async def test_cd_resolves_relative_to_selected_directory(self):
        tg.selected_workspace_dirs[42] = "/home/wbr/Workspace/others"
        listing = make_directory_listing(
            "/home/wbr/Workspace/others/herdr-remote",
            can_start_agent=True,
        )
        update = make_update()

        with patch.object(tg, "list_directories_from_relay", AsyncMock(return_value=listing)) as browse:
            await tg.cmd_cd(update, SimpleNamespace(args=["herdr-remote"]))

        browse.assert_awaited_once_with(
            "/home/wbr/Workspace/others/herdr-remote",
            source_id="local",
        )
        self.assertEqual(tg.selected_workspace_dirs[42], listing["path"])
        self.assertIn("Selected workspace", update.message.replies[0][0])
        buttons = update.message.replies[0][1]["reply_markup"].inline_keyboard[0]
        self.assertEqual([button.text for button in buttons], ["🚀 Start Codex", "📂 Browse"])

    async def test_codex_starts_in_selected_directory_and_prompts_for_first_task(self):
        selected = "/home/wbr/Workspace/others/herdr-remote"
        tg.selected_workspace_dirs[42] = selected
        update = make_update()
        started = {
            "pane_id": "w9:p1",
            "workspace_id": "w9",
            "cwd": selected,
            "display_path": "~/Workspace/others/herdr-remote",
            "project": "herdr-remote",
            "status": "idle",
            "prompted": False,
            "warning": "",
        }

        with patch.object(tg, "start_codex_from_relay", AsyncMock(return_value=started)) as start:
            await tg.cmd_codex(update, SimpleNamespace(args=[]))

        start.assert_awaited_once_with(selected, "", source_id="local")
        self.assertIn("Starting Codex", update.message.replies[0][0])
        self.assertIn("Codex started", update.message.replies[1][0])
        final_text, final_kwargs, final_message = update.message.replies[-1]
        self.assertIn("Reply to this message", final_text)
        self.assertIsInstance(final_kwargs["reply_markup"], tg.ForceReply)
        self.assertEqual(tg.pending_pane(42, final_message.message_id), "w9:p1")
        self.assertEqual(tg.find_agent("w9:p1")["cwd"], selected)

    async def test_codex_command_submits_optional_initial_prompt(self):
        selected = "/home/wbr/Workspace/others/herdr-remote"
        tg.selected_workspace_dirs[42] = selected
        update = make_update()
        started = {
            "pane_id": "w9:p1",
            "workspace_id": "w9",
            "cwd": selected,
            "display_path": "~/Workspace/others/herdr-remote",
            "project": "herdr-remote",
            "status": "working",
            "prompted": True,
            "warning": "",
        }

        with patch.object(tg, "start_codex_from_relay", AsyncMock(return_value=started)) as start:
            await tg.cmd_codex(update, SimpleNamespace(args=["Explain", "this", "repository"]))

        start.assert_awaited_once_with(
            selected,
            "Explain this repository",
            source_id="local",
        )
        self.assertIn("Initial prompt submitted", update.message.replies[-1][0])
        self.assertEqual(tg.pending_pane(42, update.message.replies[-1][2].message_id), "w9:p1")

    async def test_codex_starts_on_selected_remote_host_and_remembers_global_agent(self):
        selected = "/home/wbr/workspace-ai/yolo-pose"
        tg.agent_sources = [
            {"id": "local", "label": "本机", "status": "online"},
            {"id": "server", "label": "Herdr 192.168.2.99", "status": "online"},
        ]
        tg.selected_agent_sources[42] = "server"
        tg.selected_workspace_dirs[42] = selected
        update = make_update()
        started = {
            "pane_id": "server::w17:p1",
            "raw_pane_id": "w17:p1",
            "source_id": "server",
            "workspace_id": "w17",
            "cwd": selected,
            "display_path": "~/workspace-ai/yolo-pose",
            "project": "yolo-pose",
            "agent": "codex",
            "host": "Herdr 192.168.2.99",
            "status": "working",
            "prompted": True,
            "warning": "",
        }

        with patch.object(tg, "start_codex_from_relay", AsyncMock(return_value=started)) as start:
            await tg.cmd_codex(update, SimpleNamespace(args=["run", "the", "tests"]))

        start.assert_awaited_once_with(selected, "run the tests", source_id="server")
        remote_agent = tg.find_agent("server::w17:p1")
        self.assertEqual(remote_agent["raw_pane_id"], "w17:p1")
        self.assertEqual(remote_agent["source_id"], "server")
        self.assertEqual(remote_agent["host"], "Herdr 192.168.2.99")
        self.assertIn("Herdr 192.168.2.99", update.message.replies[-1][0])

    def test_labels_sort_status_and_disambiguate_duplicate_agents(self):
        agent_list = [
            *make_agents(2, status="idle", project="same"),
            *make_agents(1, status="working", project="work"),
            *make_agents(1, status="blocked", project="blocked"),
        ]
        agent_list[-1]["host"] = "remote.example"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertTrue(labels[0].startswith("[BLOCKED]"))
        self.assertTrue(labels[1].startswith("[WORKING]"))
        self.assertIn("remote.example", labels[0])
        duplicate_labels = [label for label in labels if "same" in label]
        self.assertEqual(len(set(duplicate_labels)), 2)
        self.assertTrue(all("w" in label and ":p1" in label for label in duplicate_labels))

    async def test_remote_agent_picker_routes_global_pane_id_and_shows_host(self):
        tg.relay_connected = True
        remote_agent = {
            "pane_id": "server::w17:p1",
            "raw_pane_id": "w17:p1",
            "source_id": "server",
            "agent": "codex",
            "status": "idle",
            "project": "yolo-pose",
            "cwd": "/home/wbr/workspace-ai/yolo-pose",
            "host": "Herdr 192.168.2.99",
        }
        tg.agents = [remote_agent]
        button = tg.build_agent_keyboard("read").inline_keyboard[0][0]
        self.assertIn("Herdr 192.168.2.99", button.text)
        self.assertEqual(
            tg.parse_callback_data(button.callback_data)["pane_id"],
            "server::w17:p1",
        )

        callback = FakeCallback(button.callback_data)
        with patch.object(tg, "read_pane", AsyncMock(return_value="remote output")) as read:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        read.assert_awaited_once_with("server::w17:p1")
        self.assertIn("Herdr 192.168.2.99", callback.message.replies[0][0])

    async def test_status_reports_local_and_remote_agent_source_health(self):
        tg.relay_connected = True
        tg.agents = make_agents(2)
        tg.agent_sources = [
            {"id": "local", "label": "本机", "status": "online", "agent_count": 2},
            {
                "id": "server",
                "label": "Herdr 192.168.2.99",
                "status": "online",
                "agent_count": 1,
            },
        ]
        update = make_update()

        await tg.cmd_status(update, SimpleNamespace(args=[]))

        text = update.message.replies[0][0]
        self.assertIn("Agent Sources:", text)
        self.assertIn("本机: ONLINE · 2 Agents", text)
        self.assertIn("Herdr 192.168.2.99: ONLINE · 1 Agent", text)

    def test_large_keyboard_paginates_without_omitting_agents(self):
        agent_list = make_agents(tg.AGENT_PAGE_SIZE + 5)
        tg.agents = agent_list

        first = tg.build_agent_keyboard("read", page=0, agent_list=agent_list)
        second = tg.build_agent_keyboard("read", page=1, agent_list=agent_list)
        first_ids = [tg.parse_callback_data(row[0].callback_data)["pane_id"] for row in first.inline_keyboard[:-1]]
        second_ids = [tg.parse_callback_data(row[0].callback_data)["pane_id"] for row in second.inline_keyboard[:-1]]

        self.assertEqual(len(first_ids), tg.AGENT_PAGE_SIZE)
        self.assertEqual(len(second_ids), 5)
        self.assertEqual(len(set(first_ids + second_ids)), tg.AGENT_PAGE_SIZE + 5)

    def test_long_labels_remain_unique_and_preserve_remote_host(self):
        long_prefix = "project-" + "x" * 70
        agent_list = make_agents(3)
        agent_list[0]["project"] = long_prefix + "-one"
        agent_list[1]["project"] = long_prefix + "-two"
        agent_list[2]["project"] = long_prefix + "-three"
        agent_list[2]["host"] = "remote-" + "host" * 30 + ".example"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertEqual(len(set(labels)), 3)
        self.assertTrue(any("@remote-" in label for label in labels))
        self.assertTrue(all(len(label) <= 64 for label in labels))

    def test_compacted_pane_hash_collision_still_has_unique_labels(self):
        agent_list = make_agents(2, project="same")
        agent_list[0]["pane_id"] = "sameprefx-very-long-pane-id-2606"
        agent_list[1]["pane_id"] = "sameprefx-very-long-pane-id-3604"

        markup = tg.build_agent_keyboard("read", agent_list=agent_list)
        labels = [row[0].text for row in markup.inline_keyboard]

        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(len(label) <= 64 for label in labels))

    def test_all_pane_callbacks_fit_telegram_byte_limit(self):
        pane_id = "pane-" + "кирилица" * 100
        tg.agents = make_agents(1)
        tg.agents[0]["pane_id"] = pane_id
        markups = [
            tg.build_agent_keyboard(action)
            for action in ("read", "interrupt", "select_send", "select_reply", "trust")
        ]
        markups.extend([tg.make_keyboard(pane_id, None), tg.interaction_keyboard(pane_id)])
        markups.append(tg.directory_browser_keyboard(make_directory_listing()))

        callbacks = [
            button.callback_data
            for markup in markups
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertTrue(all(len(callback.encode()) <= 64 for callback in callbacks))

    async def test_read_and_filtered_trust_pickers_do_not_truncate(self):
        tg.agents = make_agents(16)
        read_update = make_update()
        await tg.cmd_read(read_update, SimpleNamespace(args=[]))
        self.assertEqual(len(read_update.message.replies[0][1]["reply_markup"].inline_keyboard), 16)

        tg.agents = make_agents(12, status="blocked") + make_agents(3, status="idle")
        trust_update = make_update()
        await tg.cmd_trust(trust_update, SimpleNamespace(args=[]))
        self.assertEqual(len(trust_update.message.replies[0][1]["reply_markup"].inline_keyboard), 12)

    async def test_direct_read_send_and_reply_forms_preserve_behavior(self):
        tg.agents = make_agents(1)
        read_update = make_update()
        send_update = make_update()
        reply_update = make_update()

        with (
            patch.object(tg, "read_pane", AsyncMock(side_effect=["read output", "reply output"])),
            patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text,
        ):
            await tg.cmd_read(read_update, SimpleNamespace(args=["project"]))
            await tg.cmd_send(send_update, SimpleNamespace(args=["project", "hello"]))
            await tg.cmd_reply(reply_update, SimpleNamespace(args=["project"]))

        self.assertIn("read output", read_update.message.replies[0][0])
        send_text.assert_awaited_once_with("w0:p1", "hello")
        self.assertIn("Sent to project", send_update.message.replies[0][0])
        reply_text, reply_kwargs, reply_message = reply_update.message.replies[0]
        self.assertIn("reply output", reply_text)
        self.assertIsInstance(reply_kwargs["reply_markup"], tg.ForceReply)
        self.assertEqual(tg.pending_pane(42, reply_message.message_id), "w0:p1")

    async def test_done_remote_read_marks_seen_after_output_is_presented(self):
        remote_agent = {
            "pane_id": "server::w17:p1",
            "raw_pane_id": "w17:p1",
            "source_id": "server",
            "agent": "codex",
            "status": "done",
            "project": "yolo-pose",
            "cwd": "/home/wbr/workspace-ai/yolo-pose",
            "host": "Herdr 192.168.2.99",
        }
        tg.agents = [remote_agent]
        update = make_update()

        async def mark_seen(pane_id):
            self.assertTrue(update.message.replies)
            self.assertEqual(pane_id, "server::w17:p1")
            return {"command": "agent_seen", "ok": True, "status": "idle"}

        with (
            patch.object(tg, "read_pane", AsyncMock(return_value="completed output")),
            patch.object(tg, "mark_agent_seen_at_relay", side_effect=mark_seen) as mark,
        ):
            await tg.cmd_read(update, SimpleNamespace(args=["yolo-pose"]))

        mark.assert_awaited_once_with("server::w17:p1")
        self.assertIn("completed output", update.message.replies[0][0])
        self.assertEqual(remote_agent["status"], "idle")

    async def test_open_output_and_reply_marks_done_agent_seen(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="done")
        button = tg.interaction_keyboard("w0:p1").inline_keyboard[0][0]
        callback = FakeCallback(button.callback_data)

        with (
            patch.object(tg, "read_pane", AsyncMock(return_value="recent output")),
            patch.object(
                tg,
                "mark_agent_seen_at_relay",
                AsyncMock(return_value={"command": "agent_seen", "ok": True, "status": "idle"}),
            ) as mark,
        ):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        mark.assert_awaited_once_with("w0:p1")
        self.assertIn("recent output", callback.message.replies[0][0])
        self.assertIsInstance(callback.message.replies[0][1]["reply_markup"], tg.ForceReply)

    async def test_non_done_or_failed_reads_do_not_mark_agent_seen(self):
        with patch.object(tg, "mark_agent_seen_at_relay", AsyncMock()) as mark:
            await tg.acknowledge_agent_output(make_agents(1, status="idle")[0], "idle output")
            await tg.acknowledge_agent_output(make_agents(1, status="working")[0], "working output")
            await tg.acknowledge_agent_output(make_agents(1, status="done")[0], "(no response)")
            await tg.acknowledge_agent_output(
                make_agents(1, status="done")[0],
                "(error reading pane: relay unavailable)",
            )

        mark.assert_not_awaited()

    async def test_read_still_succeeds_if_mark_seen_fails(self):
        tg.agents = make_agents(1, status="done")
        update = make_update()

        with (
            patch.object(tg, "read_pane", AsyncMock(return_value="completed output")),
            patch.object(
                tg,
                "mark_agent_seen_at_relay",
                AsyncMock(side_effect=RuntimeError("relay unavailable")),
            ) as mark,
        ):
            await tg.cmd_read(update, SimpleNamespace(args=["project"]))

        mark.assert_awaited_once_with("w0:p1")
        self.assertIn("completed output", update.message.replies[0][0])

    async def test_interrupt_uses_canonical_key_and_waits_for_relay_ack(self):
        tg.agents = make_agents(1, status="working")
        update = make_update()

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.cmd_interrupt(update, SimpleNamespace(args=["project"]))

        send_keys.assert_awaited_once_with("w0:p1", ["C-c"])
        self.assertIn("Sent Ctrl+C", update.message.replies[0][0])

    async def test_send_and_reply_pickers_target_the_expected_actions(self):
        tg.agents = make_agents(1)
        send_update = make_update()
        reply_update = make_update()

        await tg.cmd_send(send_update, SimpleNamespace(args=[]))
        await tg.cmd_reply(reply_update, SimpleNamespace(args=[]))

        send_button = send_update.message.replies[0][1]["reply_markup"].inline_keyboard[0][0]
        reply_button = reply_update.message.replies[0][1]["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(tg.parse_callback_data(send_button.callback_data)["action"], "select_send")
        self.assertEqual(tg.parse_callback_data(reply_button.callback_data)["action"], "select_reply")

    async def test_done_agent_is_listed_and_sends_finished_notification(self):
        tg.agents = make_agents(1, status="done", project="completed")
        tg.agents[0]["host"] = "Herdr 192.168.2.99"
        update = make_update()

        await tg.cmd_agents(update, SimpleNamespace(args=[]))

        self.assertIn("DONE:", update.message.replies[0][0])
        bot = FakeBot()
        app = SimpleNamespace(bot=bot)
        tg.prev_statuses["w0:p1"] = "working"
        await tg.track_agent_updates(app, tg.agents)
        chat_id, text, kwargs, sent = bot.sent[0]
        self.assertEqual(chat_id, 42)
        self.assertIn("completed (opencode) @Herdr 192.168.2.99 finished", text)
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.text, "Open output & reply")
        self.assertEqual(tg.parse_callback_data(button.callback_data)["pane_id"], "w0:p1")
        self.assertEqual(tg.pending_pane(42, sent.message_id), "w0:p1")

    async def test_digest_disambiguates_remote_agent_by_host(self):
        tg.daily_stats["server::w17:p1"] = {
            "agent": "codex",
            "project": "yolo-pose",
            "host": "Herdr 192.168.2.99",
            "source_id": "server",
            "blocked_count": 2,
            "working_mins": 75,
            "last_change": 0,
        }
        update = make_update()

        await tg.cmd_digest(update, SimpleNamespace(args=[]))

        text = update.message.replies[0][0]
        self.assertIn("yolo-pose (codex) @Herdr 192.168.2.99", text)
        self.assertIn("1h15m working, blocked 2x", text)

    async def test_page_callback_rebuilds_from_latest_cache(self):
        tg.relay_connected = True
        tg.agents = make_agents(tg.AGENT_PAGE_SIZE + 5)
        callback = FakeCallback({"action": "page", "menu": "read", "page": 1})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertEqual(len(callback.edited_markup.inline_keyboard), 6)

    async def test_dashboard_selection_creates_private_reply_prompt(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        button = tg.build_agent_keyboard("select_reply").inline_keyboard[0][0]
        callback = FakeCallback(button.callback_data)

        with patch.object(tg, "read_pane", AsyncMock(return_value="recent output")):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        text, kwargs, sent = callback.message.replies[0]
        self.assertIn("recent output", text)
        self.assertIsInstance(kwargs["reply_markup"], tg.ForceReply)
        self.assertEqual(kwargs["reply_markup"].input_field_placeholder, "Reply to project")
        self.assertEqual(tg.pending_pane(42, sent.message_id), "w0:p1")

    async def test_callback_ack_failure_does_not_block_reply_selection(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        errors = [
            tg.NetworkError("temporary connection failure"),
            tg.BadRequest("Query is too old and response timeout expired or query id is invalid"),
        ]

        for error in errors:
            with self.subTest(error=type(error).__name__):
                button = tg.build_agent_keyboard("select_reply").inline_keyboard[0][0]
                callback = FakeCallback(button.callback_data)
                callback.answer = AsyncMock(side_effect=error)

                with patch.object(tg, "read_pane", AsyncMock(return_value="recent output")):
                    await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

                callback.answer.assert_awaited_once_with(None)
                self.assertIn("recent output", callback.message.replies[0][0])
                self.assertIsInstance(callback.message.replies[0][1]["reply_markup"], tg.ForceReply)

    async def test_global_error_handler_scrubs_unexpected_errors(self):
        error = RuntimeError(f"request failed with {tg.TOKEN}")

        with self.assertLogs(tg.log, level="ERROR") as captured:
            await tg.handle_telegram_error(SimpleNamespace(), SimpleNamespace(error=error))

        output = "\n".join(captured.output)
        self.assertIn("Unhandled Telegram error", output)
        self.assertNotIn(tg.TOKEN, output)
        self.assertIn("<redacted>", output)

    def test_application_builder_extends_telegram_connect_timeouts(self):
        builder = MagicMock()
        builder.token.return_value = builder
        builder.connect_timeout.return_value = builder
        builder.get_updates_connect_timeout.return_value = builder

        with patch.object(tg.Application, "builder", return_value=builder):
            result = tg.build_application()

        self.assertIs(result, builder.build.return_value)
        builder.token.assert_called_once_with(tg.TOKEN)
        builder.connect_timeout.assert_called_once_with(tg.TELEGRAM_CONNECT_TIMEOUT)
        builder.get_updates_connect_timeout.assert_called_once_with(tg.TELEGRAM_CONNECT_TIMEOUT)

    async def test_stale_selection_offers_refresh(self):
        tg.relay_connected = True
        callback = FakeCallback({"action": "select_reply", "pane_id": "gone:pane"})

        await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        text, kwargs, _ = callback.message.replies[0]
        self.assertIn("no longer available", text.lower())
        self.assertIn("reply_markup", kwargs)

    async def test_disconnected_callback_cannot_use_stale_agent_cache(self):
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        with patch.object(tg, "read_pane", AsyncMock()) as read_pane:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        read_pane.assert_not_awaited()
        self.assertIn("disconnected", callback.message.replies[0][0].lower())

    async def test_callback_rechecks_command_eligibility(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="idle")
        callback = FakeCallback({"action": "trust", "pane_id": "w0:p1"})

        with patch.object(tg, "send_to_relay", AsyncMock()) as send_to_relay:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_to_relay.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())

    async def test_stale_approval_cannot_type_into_unblocked_agent(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup
        tg.agents[0]["status"] = "working"

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_approval_transport_failure_preserves_controls(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock(side_effect=OSError("offline"))):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        self.assertIn("failed", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)
        self.assertIn("w0:p1", tg.approval_tokens)

    async def test_approval_sends_key_and_removes_controls_on_success(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        markup = make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback(markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_awaited_once_with("w0:p1", ["1"], prompt_id="prompt-123")
        self.assertEqual(callback.edit_calls, 1)
        self.assertIn("Sent: yes", callback.message.replies[0][0])
        self.assertNotIn("w0:p1", tg.approval_tokens)

    async def test_persistent_trust_is_hidden_and_command_disabled_by_default(self):
        tg.ALLOW_PERSISTENT_TRUST = False
        options = ["yes, single permission", "trust, always allow", "no (tab to edit)"]
        markup = tg.make_keyboard("w0:p1", options)
        labels = [row[0].text for row in markup.inline_keyboard]
        self.assertNotIn("Trust (always)", labels)

        tg.agents = make_agents(1, status="blocked")
        update = make_update()
        await tg.cmd_trust(update, SimpleNamespace(args=[]))
        self.assertIn("disabled", update.message.replies[0][0].lower())

    async def test_persistent_trust_requires_a_second_confirmation(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        options = ["yes, single permission", "trust, always allow", "no (tab to edit)"]
        markup = make_active_approval_keyboard("w0:p1", options)
        trust_button = markup.inline_keyboard[1][0]
        self.assertEqual(tg.parse_callback_data(trust_button.callback_data)["action"], "review_trust")
        review = FakeCallback(trust_button.callback_data)
        review.message.reply_markup = markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=review), SimpleNamespace())
        send_keys.assert_not_awaited()

        confirmation_markup = review.message.replies[0][1]["reply_markup"]
        confirm_button = confirmation_markup.inline_keyboard[0][0]
        confirm = FakeCallback(confirm_button.callback_data)
        confirm.message.reply_markup = confirmation_markup
        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=confirm), SimpleNamespace())
        send_keys.assert_awaited_once_with(
            "w0:p1",
            ["2"],
            prompt_id="prompt-123",
        )

    async def test_approval_from_previous_blocked_prompt_is_rejected(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        old_markup = make_active_approval_keyboard("w0:p1", ["old yes", "old no"])
        make_active_approval_keyboard("w0:p1", ["new yes", "new no"])
        callback = FakeCallback(old_markup.inline_keyboard[0][0].callback_data)
        callback.message.reply_markup = old_markup

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("older prompt", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_legacy_approval_is_rejected_without_discarding_controls(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        make_active_approval_keyboard("w0:p1", ["yes", "no"])
        callback = FakeCallback({"pane_id": "w0:p1", "k": "1"})

        with patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_keys.assert_not_awaited()
        self.assertIn("older prompt", callback.message.replies[0][0].lower())
        self.assertEqual(callback.edit_calls, 0)

    async def test_send_keys_requires_positive_relay_acknowledgement(self):
        accepted = FakeRelayConnection([
            {"type": "agents", "agents": []},
            {"type": "command_result", "command": "send_keys", "ok": True, "request_id": "request-123"},
        ])
        with patch("websockets.connect", return_value=accepted), patch.object(
            tg.secrets, "token_hex", return_value="request-123"
        ):
            await tg.send_keys_to_relay("w0:p1", ["1"])
        self.assertEqual(accepted.sent, [{
            "type": "send_keys",
            "pane_id": "w0:p1",
            "keys": ["1"],
            "request_id": "request-123",
        }])

        rejected = FakeRelayConnection([{"type": "error", "message": "keys contain disallowed values"}])
        with patch("websockets.connect", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "disallowed"):
                await tg.send_keys_to_relay("w0:p1", ["1"])

    async def test_read_pane_skips_arbitrarily_many_unrelated_messages(self):
        # The connect preamble (sessions, agents, one `blocked` per blocked
        # agent) can put any number of messages ahead of pane_content. A
        # fixed-count skip budget breaks once there are enough blocked
        # agents; the skip must be bounded by time, not by a message count.
        preamble = [{"type": "sessions", "sources": []}, {"type": "agents", "agents": []}]
        preamble += [{"type": "blocked", "pane_id": f"w{i}:p1"} for i in range(8)]
        preamble.append({"type": "pane_content", "content": "hello from pane"})
        connection = FakeRelayConnection(preamble)

        with patch("websockets.connect", return_value=connection):
            content = await tg.read_pane("w0:p1")

        self.assertEqual(content, "hello from pane")

    async def test_agent_prompt_uses_semantic_relay_command_and_requires_ack(self):
        accepted = FakeRelayConnection([
            {"type": "agents", "agents": []},
            {"type": "command_result", "command": "agent_prompt", "ok": True},
        ])
        with patch("websockets.connect", return_value=accepted):
            await tg.send_agent_prompt_to_relay("w0:p1", "run the tests")
        self.assertEqual(accepted.sent, [{
            "type": "agent_prompt",
            "pane_id": "w0:p1",
            "text": "run the tests",
        }])

        rejected = FakeRelayConnection([{"type": "error", "message": "agent_prompt command failed"}])
        with patch("websockets.connect", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "agent_prompt"):
                await tg.send_agent_prompt_to_relay("w0:p1", "run the tests")

        response = {"type": "command_result", "command": "agent_prompt", "ok": True}
        with patch.object(tg, "relay_request", new=AsyncMock(return_value=response)) as request:
            await tg.send_agent_prompt_to_relay("w0:p1", "run the tests")
        request.assert_awaited_once_with(
            {
                "type": "agent_prompt",
                "pane_id": "w0:p1",
                "text": "run the tests",
            },
            "command_result",
            timeout=20,
        )

    def test_relay_allows_numeric_approval_keys_and_acknowledges_them(self):
        relay_path = ROOT / "relay" / "herdr_relay.py"
        source = relay_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        safe_keys = next(
            node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "SAFE_KEYS" for target in node.targets)
        )
        values = eval(compile(ast.Expression(safe_keys), str(relay_path), "eval"), {"range": range})
        self.assertTrue({"1", "2", "3"}.issubset(values))
        send_keys_acknowledgements = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "command_result"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "send_keys"
        ]
        self.assertTrue(send_keys_acknowledgements)
        self.assertIn('"ok": True', source)
        self.assertIn("if result.returncode != 0:", source)

    async def test_omp_choice_uses_question_response_with_label_and_prompt_id(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.agents[0]["agent"] = "omp"
        tg.blocked_prompt_ids["w0:p1"] = "prompt-123"
        markup = tg.make_keyboard(
            "w0:p1",
            ["Red", "Blue"],
            generation="generation",
            interaction="omp_question",
        )
        tg.approval_tokens["w0:p1"] = "generation"
        callback = FakeCallback(markup.inline_keyboard[1][0].callback_data)
        callback.message.reply_markup = markup

        with (
            patch.object(tg, "send_to_relay", AsyncMock()) as send_to_relay,
            patch.object(tg, "send_keys_to_relay", AsyncMock()) as send_keys,
        ):
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        send_to_relay.assert_awaited_once_with("w0:p1", "Blue", prompt_id="prompt-123")
        send_keys.assert_not_awaited()

    def test_relay_disconnect_clears_approval_generations(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.agent_sources = [{"id": "local", "status": "online"}]
        tg.approval_tokens["w0:p1"] = "generation"
        tg.blocked_prompt_ids["w0:p1"] = "prompt"

        tg.clear_relay_connection_state()

        self.assertFalse(tg.relay_connected)
        self.assertEqual(tg.agents, [])
        self.assertEqual(tg.agent_sources, [])
        self.assertEqual(tg.approval_tokens, {})
        self.assertEqual(tg.blocked_prompt_ids, {})

    async def test_failed_blocked_notification_preserves_previous_generation(self):
        tg.approval_tokens["w0:p1"] = "previous"
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=OSError("offline"))))

        with self.assertRaisesRegex(OSError, "offline"):
            await tg.notify_blocked(app, "w0:p1", "opencode", "project", "prompt", ["yes", "no"])

        self.assertEqual(tg.approval_tokens["w0:p1"], "previous")

    async def test_blocked_notification_failure_does_not_disconnect_relay(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.approval_tokens["w0:p1"] = "previous"
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=OSError("offline"))))

        await tg.notify_blocked_safely(app, {
            "pane_id": "w0:p1",
            "agent": "opencode",
            "project": "project",
            "prompt": "prompt",
            "options": ["yes", "no"],
        })

        self.assertTrue(tg.relay_connected)
        self.assertEqual(tg.agents[0]["pane_id"], "w0:p1")
        self.assertEqual(tg.approval_tokens["w0:p1"], "previous")

    async def test_graceful_relay_close_clears_connection_state(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.approval_tokens["w0:p1"] = "generation"
        calls = 0

        def connect(_url):
            nonlocal calls
            calls += 1
            if calls == 1:
                return FakeRelayConnection([])
            self.assertFalse(tg.relay_connected)
            self.assertEqual(tg.agents, [])
            self.assertEqual(tg.approval_tokens, {})
            raise KeyboardInterrupt

        with patch("websockets.connect", side_effect=connect):
            with self.assertRaises(KeyboardInterrupt):
                await tg.relay_listener(SimpleNamespace())

    async def test_callback_rejects_unauthorized_chat(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        await tg.handle_callback(make_update(chat_id=7, callback=callback), SimpleNamespace())

        self.assertEqual(callback.answers, ["Unauthorized"])
        self.assertEqual(callback.message.replies, [])

    async def test_callback_rejects_unauthorized_user(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        callback = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"})

        await tg.handle_callback(make_update(user_id=7, callback=callback), SimpleNamespace())

        self.assertEqual(callback.answers, ["Unauthorized"])
        self.assertEqual(callback.message.replies, [])

    async def test_private_chat_requirement_rejects_groups_by_default(self):
        update = make_update(chat_type="group")

        await tg.cmd_agents(update, SimpleNamespace(args=[]))

        self.assertEqual(update.message.replies, [])

    def test_pending_registry_is_chat_scoped_and_bounded(self):
        tg.register_pending(42, 10, "local:pane")
        tg.register_pending(43, 10, "other:pane")
        self.assertEqual(tg.pending_pane(42, 10), "local:pane")
        self.assertEqual(tg.pending_pane(43, 10), "other:pane")

        for message_id in range(tg.PENDING_LIMIT + 2):
            tg.register_pending(42, 1000 + message_id, f"pane:{message_id}")
        self.assertEqual(len(tg.pending), tg.PENDING_LIMIT)
        self.assertIsNone(tg.pending_pane(42, 1000))

    async def test_blocked_notification_keeps_approvals_and_adds_interaction(self):
        bot = FakeBot()
        await tg.notify_blocked(
            SimpleNamespace(bot=bot),
            pane_id="w0:p1",
            agent="opencode",
            project="blocked-project",
            prompt="Allow tool?",
            options=["yes, single permission", "trust, always allow", "no (tab to edit)"],
        )

        chat_id, text, kwargs, sent = bot.sent[0]
        rows = kwargs["reply_markup"].inline_keyboard
        self.assertEqual([json.loads(row[0].callback_data)["k"] for row in rows[:3]], ["1", "2", "3"])
        self.assertEqual(rows[-1][0].text, "Open output & reply")
        self.assertIn("reply to this notification", text)
        self.assertNotIn("parse_mode", kwargs)
        self.assertEqual(tg.pending_pane(chat_id, sent.message_id), "w0:p1")

    async def test_remote_blocked_notification_keeps_global_id_and_names_host(self):
        remote_pane = "server::w17:p1"
        tg.agents = [{
            "pane_id": remote_pane,
            "source_id": "server",
            "agent": "codex",
            "status": "blocked",
            "project": "yolo-pose",
            "host": "Herdr 192.168.2.99",
        }]
        bot = FakeBot()

        await tg.notify_blocked(
            SimpleNamespace(bot=bot),
            pane_id=remote_pane,
            agent="codex",
            project="yolo-pose",
            prompt="Allow tool?",
            options=["yes, single permission", "no (tab to edit)"],
            host="Herdr 192.168.2.99",
        )

        chat_id, text, kwargs, sent = bot.sent[0]
        self.assertIn("@Herdr 192.168.2.99", text)
        self.assertEqual(tg.pending_pane(chat_id, sent.message_id), remote_pane)
        open_button = kwargs["reply_markup"].inline_keyboard[-1][0]
        self.assertEqual(tg.parse_callback_data(open_button.callback_data)["pane_id"], remote_pane)

    async def test_group_prompts_keep_independent_pane_mappings(self):
        tg.REQUIRE_PRIVATE_CHAT = False
        tg.relay_connected = True
        tg.agents = make_agents(2)
        first = FakeCallback({"action": "select_reply", "pane_id": "w0:p1"}, chat_type="group", message_id=10)
        second = FakeCallback({"action": "select_reply", "pane_id": "w1:p1"}, chat_type="group", message_id=11)

        with patch.object(tg, "read_pane", AsyncMock(return_value="output")):
            await tg.handle_callback(make_update(chat_type="group", callback=first), SimpleNamespace())
            await tg.handle_callback(make_update(chat_type="group", callback=second), SimpleNamespace())

        first_text, first_kwargs, first_sent = first.message.replies[0]
        second_text, second_kwargs, second_sent = second.message.replies[0]
        self.assertNotIn("reply_markup", first_kwargs)
        self.assertNotIn("reply_markup", second_kwargs)
        self.assertIn("Reply to this message", first_text)
        self.assertEqual(tg.pending_pane(42, first_sent.message_id), "w0:p1")
        self.assertEqual(tg.pending_pane(42, second_sent.message_id), "w1:p1")

        first_reply = FakeMessage(chat_type="group")
        first_reply.reply_to_message = SimpleNamespace(message_id=first_sent.message_id)
        first_reply.text = "first response"
        second_reply = FakeMessage(chat_type="group")
        second_reply.reply_to_message = SimpleNamespace(message_id=second_sent.message_id)
        second_reply.text = "second response"
        with patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(chat_type="group", message=first_reply), SimpleNamespace())
            await tg.handle_text(make_update(chat_type="group", message=second_reply), SimpleNamespace())
        self.assertEqual(
            send_text.await_args_list,
            [unittest.mock.call("w0:p1", "first response"), unittest.mock.call("w1:p1", "second response")],
        )

    async def test_native_notification_reply_routes_to_associated_pane(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "follow up"

        with patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())

        send_text.assert_awaited_once_with("w0:p1", "follow up")
        self.assertEqual(message.replies[0][0], "Sent")

    async def test_blocked_notification_reply_uses_prompt_response(self):
        tg.relay_connected = True
        tg.agents = make_agents(1, status="blocked")
        tg.blocked_prompt_ids["w0:p1"] = "prompt-123"
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "custom answer"

        with (
            patch.object(tg, "send_to_relay", AsyncMock()) as send_response,
            patch.object(tg, "send_text_to_relay", AsyncMock()) as send_text,
        ):
            await tg.handle_text(make_update(message=message), SimpleNamespace())

        send_response.assert_awaited_once_with(
            "w0:p1", "custom answer", prompt_id="prompt-123"
        )
        send_text.assert_not_awaited()

    async def test_mapped_reply_rejects_disconnected_and_stale_panes(self):
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "follow up"

        with patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())
            send_text.assert_not_awaited()
        self.assertIn("disconnected", message.replies[0][0].lower())

        tg.relay_connected = True
        message.replies.clear()
        with patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(message=message), SimpleNamespace())
            send_text.assert_not_awaited()
        self.assertIn("no longer available", message.replies[0][0].lower())

    async def test_unauthorized_native_reply_is_ignored(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(7, 77, "w0:p1")
        message = FakeMessage(chat_id=7)
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "not allowed"

        with patch.object(tg, "send_agent_prompt_to_relay", AsyncMock()) as send_text:
            await tg.handle_text(make_update(chat_id=7, message=message), SimpleNamespace())

        send_text.assert_not_awaited()
        self.assertEqual(message.replies, [])

    async def test_duplicate_cross_host_pane_identity_fails_closed(self):
        tg.relay_connected = True
        tg.agents = make_agents(2)
        tg.agents[1]["pane_id"] = tg.agents[0]["pane_id"]
        tg.agents[1]["host"] = "remote.example"
        button = tg.build_agent_keyboard("select_reply").inline_keyboard[0][0]
        callback = FakeCallback(button.callback_data)

        with patch.object(tg, "read_pane", AsyncMock()) as read_pane:
            await tg.handle_callback(make_update(callback=callback), SimpleNamespace())

        read_pane.assert_not_awaited()
        self.assertIn("no longer available", callback.message.replies[0][0].lower())

    async def test_long_native_reply_is_forwarded_to_relay(self):
        tg.relay_connected = True
        tg.agents = make_agents(1)
        tg.register_pending(42, 77, "w0:p1")
        message = FakeMessage()
        message.reply_to_message = SimpleNamespace(message_id=77)
        message.text = "x" * 1001

        with patch.object(
            tg,
            "relay_request",
            AsyncMock(return_value={"command": "agent_prompt", "ok": True}),
        ) as relay_request:
            await tg.handle_text(make_update(message=message), SimpleNamespace())

        relay_request.assert_awaited_once()
        self.assertEqual(relay_request.await_args.args[0]["text"], message.text)
        self.assertEqual(message.replies[0][0], "Sent")


if __name__ == "__main__":
    unittest.main()
