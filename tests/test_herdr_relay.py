import asyncio
import importlib.util
import json
import logging
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock
import uuid


RELAY_PATH = Path(__file__).resolve().parents[1] / "relay" / "herdr_relay.py"


class _ConnectionClosed(Exception):
    pass


def _websockets_stubs():
    websockets = types.ModuleType("websockets")
    websockets.__path__ = []
    websockets_asyncio = types.ModuleType("websockets.asyncio")
    websockets_asyncio.__path__ = []
    websockets_server = types.ModuleType("websockets.asyncio.server")
    websockets_server.serve = object()
    exceptions = types.ModuleType("websockets.exceptions")
    exceptions.ConnectionClosedError = _ConnectionClosed
    exceptions.ConnectionClosedOK = _ConnectionClosed
    return {
        "websockets": websockets,
        "websockets.asyncio": websockets_asyncio,
        "websockets.asyncio.server": websockets_server,
        "websockets.exceptions": exceptions,
    }


@contextmanager
def loaded_relay(*, herdr_bin=None, relay_host=None, relay_token=None, trusted_origins=None):
    module_name = f"herdr_relay_test_{uuid.uuid4().hex}"
    logger = logging.getLogger("herdr-relay")
    original_handlers = tuple(logger.handlers)
    original_level = logger.level
    audit_logger = logging.getLogger("herdr-audit")
    original_audit_handlers = tuple(audit_logger.handlers)
    original_audit_level = audit_logger.level
    original_audit_disabled = audit_logger.disabled
    original_disabled = logger.disabled
    websockets_logger = logging.getLogger("websockets")
    original_websockets_level = websockets_logger.level

    relay_dir = str(RELAY_PATH.parent)
    added_relay_dir = relay_dir not in sys.path
    if added_relay_dir:
        sys.path.insert(0, relay_dir)
    with tempfile.TemporaryDirectory() as log_dir:
        environment = {
            "HERDR_LOG_DIR": log_dir,
            "HERDR_TRUSTED_ORIGINS": "",
        }
        if herdr_bin is not None:
            environment["HERDR_BIN"] = herdr_bin
        if relay_host is not None:
            environment["HERDR_RELAY_HOST"] = relay_host
        if relay_token is not None:
            environment["HERDR_RELAY_TOKEN"] = relay_token
        if trusted_origins is not None:
            environment["HERDR_RELAY_TRUSTED_ORIGINS"] = trusted_origins

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.dict(
            sys.modules, _websockets_stubs(), clear=False
        ):
            for name, value in (
                ("HERDR_BIN", herdr_bin),
                ("HERDR_RELAY_HOST", relay_host),
                ("HERDR_RELAY_TOKEN", relay_token),
                ("HERDR_RELAY_TRUSTED_ORIGINS", trusted_origins),
            ):
                if value is None:
                    os.environ.pop(name, None)

            spec = importlib.util.spec_from_file_location(module_name, RELAY_PATH)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                logger.disabled = True
                yield module
            finally:
                sys.modules.pop(module_name, None)
                if added_relay_dir:
                    sys.path.remove(relay_dir)
                for handler in tuple(logger.handlers):
                    if handler not in original_handlers:
                        logger.removeHandler(handler)
                        handler.close()
                logger.setLevel(original_level)
                for handler in tuple(audit_logger.handlers):
                    if handler not in original_audit_handlers:
                        audit_logger.removeHandler(handler)
                        handler.close()
                audit_logger.setLevel(original_audit_level)
                audit_logger.disabled = original_audit_disabled
                logger.disabled = original_disabled
                websockets_logger.setLevel(original_websockets_level)


class _FakeWebSocket:
    def __init__(self, messages, headers=None):
        self.remote_address = ("127.0.0.1", 12345)
        self.request = types.SimpleNamespace(
            headers={
                "User-Agent": "Python unittest",
                "Origin": "",
                **(headers or {}),
            }
        )
        self._messages = iter(messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send(self, message):
        self.sent.append(message)


class RelayConfigurationTests(unittest.TestCase):
    def test_relay_defaults_to_loopback(self):
        with loaded_relay() as relay:
            self.assertEqual(relay.RELAY_HOST, "127.0.0.1")

    def test_herdr_defaults_to_path_lookup(self):
        with loaded_relay() as relay:
            expected = relay.shutil.which("herdr")
            if expected is None:
                expected = "herdr" if relay.sys.platform == "win32" else "/opt/homebrew/bin/herdr"
            self.assertEqual(relay.HERDR, expected)

    def test_windows_log_directory_uses_local_app_data(self):
        with (
            loaded_relay() as relay,
            mock.patch.object(relay.sys, "platform", "win32"),
            mock.patch.dict(
                relay.os.environ,
                {"LOCALAPPDATA": "C:/Users/test/AppData/Local"},
            ),
        ):
            self.assertEqual(
                relay._get_log_dir(),
                relay.os.path.join(
                    "C:/Users/test/AppData/Local",
                    "herdr-remote",
                    "logs",
                ),
            )

    def test_explicit_trusted_origin_allowlist_is_exact(self):
        with loaded_relay(
            relay_token="relay-token-at-least-16-chars",
            trusted_origins="https://dashboard.example",
        ) as relay:
            trusted = types.SimpleNamespace(headers={
                "Origin": "https://dashboard.example",
                "Host": "relay.example",
            })
            attacker = types.SimpleNamespace(headers={
                "Origin": "https://attacker.example",
                "Host": "relay.example",
            })

            self.assertTrue(relay.websocket_origin_allowed(trusted))
            self.assertFalse(relay.websocket_origin_allowed(attacker))

    def test_herdr_bin_override_is_honored(self):
        configured_path = os.path.join("custom", "bin", "herdr")
        with loaded_relay(herdr_bin=configured_path) as relay:
            self.assertEqual(relay.HERDR, configured_path)

    def test_herdr_output_uses_utf8_decoding(self):
        with loaded_relay() as relay:
            completed = subprocess.CompletedProcess([], 0, stdout="ready\n", stderr="")
            with mock.patch.object(relay.subprocess, "run", return_value=completed) as run:
                for remote in (None, "agent-host"):
                    with self.subTest(remote=remote):
                        self.assertEqual(relay.run_herdr("pane", "list", remote=remote), "ready")
                        kwargs = run.call_args.kwargs
                        self.assertEqual(kwargs["encoding"], "utf-8")
                        self.assertEqual(kwargs["errors"], "replace")

    def test_herdr_output_falls_back_to_empty_string_on_invocation_error(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay.subprocess, "run", side_effect=OSError("unavailable")):
                self.assertEqual(relay.run_herdr("pane", "list"), "")


class RelayPaneStateTests(unittest.TestCase):
    def test_stale_panes_are_removed_from_agent_cache(self):
        with loaded_relay() as relay:
            relay.known_panes.update({"active-pane", "stale-pane"})
            relay.agent_cache.update({
                "active-pane": {"pane_id": "active-pane", "project": "current"},
                "stale-pane": {"pane_id": "stale-pane", "project": "old"},
            })

            relay.update_pane_maps([
                {"pane_id": "active-pane", "remote": None},
            ])

            # Stale panes are dropped; surviving entries are refreshed from the
            # poll rather than keeping whatever was cached previously.
            self.assertNotIn("stale-pane", relay.agent_cache)
            self.assertNotIn("stale-pane", relay.known_panes)
            self.assertEqual(
                relay.agent_cache,
                {"active-pane": {"pane_id": "active-pane", "remote": None}},
            )


class RelayHistoryTests(unittest.TestCase):
    def test_conversation_history_is_bounded_before_websocket_delivery(self):
        with loaded_relay() as relay:
            history = {
                "messages": [
                    {
                        "role": "assistant",
                        "content": f"{index}:" + ("x" * 25_000),
                    }
                    for index in range(205)
                ]
            }

            messages = relay.bounded_history_messages(history)

            self.assertEqual(len(messages), relay.CONVERSATION_HISTORY_MAX_MESSAGES)
            self.assertTrue(messages[0]["content"].startswith("5:"))
            self.assertTrue(all(
                len(message["content"])
                <= relay.CONVERSATION_HISTORY_CONTENT_MAX_CHARS
                for message in messages
            ))


class RelayResponseTests(unittest.TestCase):
    def test_respond_sends_correlated_acknowledgement(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "yes, single permission"
            request_id = "request-123"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "yes",
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=completed):
                asyncio.run(relay.handle_client(ws))

            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "respond",
                    "ok": True,
                    "request_id": request_id,
                },
            )

    def test_respond_strips_crlf_and_sends_canonical_text_before_enter(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            remote = "agent-host"
            relay.known_panes.add(pane_id)
            relay.pane_remote_map[pane_id] = remote
            content = "yes, single permission"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "  yes\r\n",
                })]
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=completed) as run:
                asyncio.run(relay.handle_client(ws))

            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [
                        *relay.ssh_command_prefix(),
                        remote,
                        relay.shlex.join([
                            relay.REMOTE_HERDR_BIN,
                            "pane",
                            "send-text",
                            pane_id,
                            "yes",
                        ]),
                    ],
                    [
                        *relay.ssh_command_prefix(),
                        remote,
                        relay.shlex.join([
                            relay.REMOTE_HERDR_BIN,
                            "pane",
                            "send-keys",
                            pane_id,
                            "Enter",
                        ]),
                    ],
                ],
            )
            for call in run.call_args_list:
                self.assertFalse(any("\r" in arg or "\n" in arg for arg in call.args[0]))

    def test_respond_does_not_send_enter_when_send_text_fails(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "yes, single permission"
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "yes",
                })]
            )
            failed = subprocess.CompletedProcess([], 1, stdout="", stderr="failed")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "pane_is_omp", return_value=False), \
                 mock.patch.object(relay.subprocess, "run", return_value=failed) as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_called_once()
            self.assertEqual(
                run.call_args.args[0],
                [relay.HERDR, "pane", "send-text", pane_id, "yes"],
            )



class RelayQuestionTests(unittest.TestCase):
    ASK_SCREEN = """
╭─ Ask ─╮
│ Which color? │
│   Red │
│    Blue │
│    Green │
│    Other (type your own) │
│ Enter select · ↑/↓ move · Esc cancel │
╰───────╯
"""
    MULTI_SCREEN = """
╭─ Ask ─╮
│ Which capabilities? │
│   Color output │
│    Nerd Font │
│    Mobile layout │
│    Other (type your own) │
╰───────╯
"""

    MULTI_SELECTED_SCREEN = """
╭─ Ask ─╮
│ capabilities    Submit │
│ Which capabilities? │
│    Color output │
│   Nerd Font │
│    Mobile layout │
│    Other (type your own) │
╰───────╯
"""


    def test_detects_live_omp_question_options_and_cursor(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)

            self.assertIsNotNone(question)
            self.assertEqual(
                [option["label"] for option in question["options"]],
                ["Red", "Blue", "Green", relay.QUESTION_OTHER],
            )
            self.assertEqual(question["selected_index"], 0)
            self.assertEqual(relay.detect_options(self.ASK_SCREEN), ["Red", "Blue", "Green"])

    def test_prompt_identity_includes_question_text(self):
        first_prompt = self.ASK_SCREEN.replace("Which color?", "Which environment?")
        second_prompt = self.ASK_SCREEN.replace("Which color?", "Delete all data?")

        with loaded_relay() as relay:
            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first_prompt),
                relay.question_prompt_id("pane-1", second_prompt),
            )

    def test_long_questions_with_identical_options_have_distinct_identity(self):
        first_prompt = "Which deployment target should receive this very long request?\n" + "\n".join(
            f"detail line {index}" for index in range(35)
        ) + "\n  staging\n   production\n   Other (type your own)"
        second_prompt = first_prompt.replace(
            "Which deployment target should receive this very long request?",
            "Which database should receive this very long request?",
        )

        with loaded_relay() as relay:
            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first_prompt),
                relay.question_prompt_id("pane-1", second_prompt),
            )

    def test_read_pane_preserves_long_question_for_prompt_identity(self):
        def pane_output(question):
            return "\n".join([
                question,
                *(f"detail line {index}" for index in range(35)),
                "  staging",
                "   production",
                "   Other (type your own)",
            ])

        with loaded_relay() as relay:
            with mock.patch.object(
                relay,
                "run_herdr",
                side_effect=[
                    pane_output("Which deployment target should receive this request?"),
                    pane_output("Which database should receive this request?"),
                ],
            ):
                first = relay.read_pane("pane-1")
                second = relay.read_pane("pane-1")

            self.assertNotEqual(
                relay.question_prompt_id("pane-1", first),
                relay.question_prompt_id("pane-1", second),
            )

    def test_prompt_identity_ignores_multi_selection_state(self):
        with loaded_relay() as relay:
            self.assertEqual(
                relay.question_prompt_id("pane-1", self.MULTI_SCREEN),
                relay.question_prompt_id("pane-1", self.MULTI_SELECTED_SCREEN),
            )

    def test_unknown_blocked_prompt_has_no_approval_fallback(self):
        with loaded_relay() as relay:
            message = relay.blocked_message("pane-1", "omp", "project", "local", "What name?")

            self.assertEqual(message["options"], [])
            self.assertEqual(message["interaction"], "prompt")

    def test_question_choice_moves_from_live_cursor_before_enter(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)
            with mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                delivered = relay.respond_to_question(
                    "pane-1", "Blue", question, remote="agent-host"
                )

            self.assertTrue(delivered)
            mutate.assert_called_once_with(
                "pane", "send-keys", "pane-1", "Down", "Enter", remote="agent-host"
            )

    def test_custom_question_answer_waits_for_editor_then_submits(self):
        with loaded_relay() as relay:
            question = relay.detect_question(self.ASK_SCREEN)
            with mock.patch.object(
                relay,
                "read_pane",
                return_value="Custom answer: Which color?\n>\nenter or ctrl+q submit",
            ), mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                delivered = relay.respond_to_question(
                    "pane-1", "Purple", question, remote=None
                )

            self.assertTrue(delivered)
            self.assertEqual(
                mutate.call_args_list,
                [
                    mock.call("pane", "send-keys", "pane-1", "Down", "Down", "Down", "Enter", remote=None),
                    mock.call("pane", "send-text", "pane-1", "Purple", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Enter", remote=None),
                ],
            )



    def test_multi_question_toggle_and_done_submission_use_live_cursor(self):
        with loaded_relay() as relay:
            with mock.patch.object(relay, "pane_is_omp", return_value=True), \
                 mock.patch.object(
                     relay,
                     "read_pane",
                     side_effect=[self.MULTI_SCREEN, self.MULTI_SELECTED_SCREEN],
                 ), mock.patch.object(relay, "_mutate_herdr", return_value=True) as mutate:
                toggled = relay.toggle_question_option("pane-1", "Nerd Font")
                submitted = relay.submit_multi_question("pane-1")

            self.assertTrue(toggled)
            self.assertTrue(submitted)
            self.assertEqual(
                mutate.call_args_list,
                [
                    mock.call("pane", "send-keys", "pane-1", "Down", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Enter", remote=None),
                    mock.call("pane", "send-keys", "pane-1", "Tab", "Enter", remote=None),
                ],
            )

    def test_non_omp_checkbox_prompt_never_uses_question_navigation(self):
        with loaded_relay() as relay:
            message = relay.blocked_message("pane-1", "claude", "project", "local", self.MULTI_SCREEN)

            self.assertEqual(message["interaction"], "prompt")
            self.assertEqual(message["options"], [])

    def test_arbitrary_response_is_rejected_for_non_question_prompt(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            relay.known_panes.add(pane_id)
            content = "Approve this tool?"
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "text": "run arbitrary command",
                })
            ])
            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("detected question", json.loads(ws.sent[-1])["message"])

    def test_stale_standard_approval_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Run read-only status command?\nyes, single permission"
            current_content = "Delete production data?\nyes, single permission"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "text": "yes, single permission",
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])

    def test_stale_custom_editor_response_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Enter your response:\nWhich environment?"
            current_content = "Enter your response:\nType the production deletion token"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "respond",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "text": "staging",
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "_mutate_herdr") as mutate:
                asyncio.run(relay.handle_client(ws))

            mutate.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])

    def test_stale_standard_approval_key_is_rejected_before_delivery(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Run read-only status command?\nyes, single permission"
            current_content = "Delete production data?\nyes, single permission"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "keys": ["1"],
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value=current_content), \
                 mock.patch.object(relay, "run_herdr_result") as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])

    def test_stale_approval_key_cannot_type_into_an_unblocked_agent(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            old_content = "Run read-only status command?\nyes, single permission"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket([
                json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, old_content),
                    "keys": ["1"],
                })
            ])

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()), \
                 mock.patch.object(relay, "read_pane", return_value="$ "), \
                 mock.patch.object(relay, "run_herdr_result") as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_not_called()
            self.assertIn("prompt changed", json.loads(ws.sent[-1])["message"])


class RelayCommandTests(unittest.TestCase):
    def test_agent_navigation_keys_do_not_require_prompt_identity(self):
        for key in ("Up", "Down", "Enter", "Escape"):
            with self.subTest(key=key), loaded_relay() as relay:
                pane_id = "pane-1"
                request_id = f"request-{key.lower()}"
                relay.known_panes.add(pane_id)
                ws = _FakeWebSocket(
                    [json.dumps({
                        "type": "send_keys",
                        "pane_id": pane_id,
                        "keys": [key],
                        "request_id": request_id,
                    })],
                    headers={"X-Herdr-Remote-Command": "1"},
                )
                completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

                with mock.patch.object(
                    relay,
                    "run_herdr_result",
                    return_value=completed,
                ) as run:
                    asyncio.run(relay.handle_client(ws))

                run.assert_called_once_with(
                    "pane",
                    "send-keys",
                    pane_id,
                    key,
                    remote=None,
                )
                self.assertEqual(
                    json.loads(ws.sent[-1]),
                    {
                        "type": "command_result",
                        "command": "send_keys",
                        "ok": True,
                        "request_id": request_id,
                    },
                )

    def test_command_connection_skips_snapshot_and_correlates_ack(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            request_id = "request-123"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "keys": ["C-c"],
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "send_current_snapshot", new=mock.AsyncMock()) as snapshot, \
                 mock.patch.object(relay, "read_pane", return_value=""), \
                 mock.patch.object(relay, "run_herdr_result", return_value=completed):
                asyncio.run(relay.handle_client(ws))

            snapshot.assert_not_awaited()
            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "send_keys",
                    "ok": True,
                    "request_id": request_id,
                },
            )

    def test_numeric_approval_key_requires_and_accepts_the_current_prompt(self):
        with loaded_relay() as relay:
            pane_id = "pane-1"
            content = "Run read-only status command?\nyes, single permission"
            request_id = "request-123"
            relay.known_panes.add(pane_id)
            ws = _FakeWebSocket(
                [json.dumps({
                    "type": "send_keys",
                    "pane_id": pane_id,
                    "prompt_id": relay.question_prompt_id(pane_id, content),
                    "keys": ["1"],
                    "request_id": request_id,
                })],
                headers={"X-Herdr-Remote-Command": "1"},
            )
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with mock.patch.object(relay, "read_pane", return_value=content), \
                 mock.patch.object(
                     relay,
                     "run_herdr_result",
                     return_value=completed,
                 ) as run:
                asyncio.run(relay.handle_client(ws))

            run.assert_called_once_with(
                "pane",
                "send-keys",
                pane_id,
                "1",
                remote=None,
            )
            self.assertEqual(
                json.loads(ws.sent[-1]),
                {
                    "type": "command_result",
                    "command": "send_keys",
                    "ok": True,
                    "request_id": request_id,
                },
            )


class RelayEventPushTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_agent_event_broadcasts_snapshot_before_blocked_prompt(self):
        complete_snapshot = [
            {
                "pane_id": "event-pane",
                "raw_pane_id": "event-pane",
                "source_id": "local",
                "agent": "omp",
                "status": "blocked",
                "cwd": "/projects/current",
                "project": "current",
                "host": "local",
            },
            {
                "pane_id": "build::unrelated-pane",
                "raw_pane_id": "unrelated-pane",
                "source_id": "build",
                "agent": "claude",
                "status": "idle",
                "cwd": "/projects/other",
                "project": "other",
                "host": "Build Server",
            },
        ]
        event = {
            "type": "agent_event",
            "pane_id": "event-pane",
            "agent": "omp",
            "status": "blocked",
            "cwd": "/projects/current",
            "project": "current",
            "host": "local",
        }
        fallback_agent = {
            "pane_id": "event-pane",
            "raw_pane_id": "event-pane",
            "source_id": "local",
            "agent": "omp",
            "status": "blocked",
            "cwd": "/projects/current",
            "project": "current",
            "host": "local",
        }

        cases = (
            ("complete", complete_snapshot, complete_snapshot),
            ("empty", [], [fallback_agent]),
        )
        for case, snapshot, expected_agents in cases:
            with self.subTest(case=case), loaded_relay() as relay:
                local_source = relay.local_agent_source()
                remote_source = relay.normalize_ssh_profile({
                    "id": "build",
                    "label": "Build Server",
                    "target": "agent-host",
                    "agent_enabled": True,
                })
                source_map = {
                    "local": local_source,
                    "build": remote_source,
                }
                source_statuses = [
                    relay.source_status(
                        local_source,
                        "online",
                        agent_count=1 if snapshot else 0,
                    ),
                    relay.source_status(
                        remote_source,
                        "online",
                        agent_count=1 if snapshot else 0,
                    ),
                ]
                relay.known_panes.update({"event-pane", "stale-pane"})
                relay.pane_remote_map["event-pane"] = "existing-host"
                relay.pane_remote_map["stale-pane"] = "old-host"
                relay.last_statuses["stale-pane"] = "idle"
                messages = []
                broadcasts_complete = asyncio.Event()

                async def capture_broadcast(message):
                    messages.append(message)
                    if len(messages) == 3:
                        broadcasts_complete.set()

                with mock.patch.object(
                    relay,
                    "collect_all_agents",
                    new=mock.AsyncMock(return_value=(
                        snapshot,
                        source_statuses,
                        source_map,
                    )),
                ), mock.patch.object(
                    relay, "read_pane", return_value="approve all pending"
                ) as read_pane, mock.patch.object(
                    relay, "broadcast", side_effect=capture_broadcast
                ), mock.patch.object(
                    relay, "send_web_push", new=mock.AsyncMock()
                ) as send_web_push:
                    task = asyncio.create_task(relay.event_push())
                    try:
                        await relay.event_queue.put(event)
                        await asyncio.wait_for(broadcasts_complete.wait(), timeout=1)
                    finally:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task

                expected_prompt_id = relay.question_prompt_id("event-pane", "approve all pending")
                self.assertEqual(
                    messages,
                    [
                        {"type": "agent_sources", "sources": source_statuses},
                        {"type": "agents", "agents": expected_agents},
                        {
                            "type": "blocked",
                            "pane_id": "event-pane",
                            "agent": "omp",
                            "project": "current",
                            "host": "local",
                            "source_id": "local",
                            "prompt": "approve all pending",
                            "prompt_id": expected_prompt_id,
                            "options": relay.SUBAGENT_OPTIONS,
                            "multi_options": [],
                            "selected_options": [],
                            "interaction": "prompt",
                            "multi": False,
                            "update": False,
                        },
                    ],
                )
                read_pane.assert_called_once_with(
                    "event-pane", remote=None
                )
                expected_pane_ids = {agent["pane_id"] for agent in expected_agents}
                self.assertEqual(relay.known_panes, expected_pane_ids)
                self.assertIsNone(relay.pane_remote_map["event-pane"])
                if snapshot:
                    self.assertEqual(
                        relay.pane_remote_map["build::unrelated-pane"],
                        remote_source,
                    )
                self.assertNotIn("stale-pane", relay.last_statuses)
                self.assertEqual(relay.last_statuses["event-pane"], "blocked")
                send_web_push.assert_awaited_once_with(
                    title="🐑 current blocked",
                    body="approve all pending",
                    url="/?pane=event-pane",
                )

    async def test_nonblocked_event_clears_prompt_lifecycle_before_reblocking(self):
        with loaded_relay() as relay:
            pane_id = "event-pane"
            cached_agent = {
                "pane_id": pane_id,
                "raw_pane_id": pane_id,
                "source_id": "local",
                "agent": "omp",
                "status": "blocked",
                "cwd": "/projects/current",
                "project": "current",
                "host": "local",
            }
            relay.agent_cache[pane_id] = cached_agent
            relay.last_statuses[pane_id] = "blocked"
            relay.last_blocked_prompts[pane_id] = (
                relay.question_prompt_id(pane_id, "same prompt"),
                (),
                "same prompt",
            )
            cleanup_complete = asyncio.Event()
            event = {
                **cached_agent,
                "type": "agent_event",
                "status": "working",
            }

            async def capture_push(*args, **kwargs):
                cleanup_complete.set()

            with (
                mock.patch.object(
                    relay,
                    "collect_all_agents",
                    new=mock.AsyncMock(return_value=(
                        [cached_agent],
                        [],
                        {"local": relay.local_agent_source()},
                    )),
                ),
                mock.patch.object(relay, "broadcast", new=mock.AsyncMock()),
                mock.patch.object(
                    relay,
                    "send_web_push",
                    new=mock.AsyncMock(side_effect=capture_push),
                ) as send_web_push,
            ):
                task = asyncio.create_task(relay.event_push())
                try:
                    await relay.event_queue.put(event)
                    await asyncio.wait_for(cleanup_complete.wait(), timeout=1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

            self.assertNotIn(pane_id, relay.last_blocked_prompts)
            self.assertEqual(relay.last_statuses[pane_id], "working")
            send_web_push.assert_awaited_once_with("", "", clear=True)


class RelaySubprocessConcurrencyTests(unittest.TestCase):
    def test_calls_to_the_same_remote_are_serialized(self):
        with loaded_relay() as relay:
            first_entered = threading.Event()
            second_started = threading.Event()
            second_entered = threading.Event()
            release_first = threading.Event()
            invocation_count = 0
            count_lock = threading.Lock()

            def fake_subprocess_run(command, **kwargs):
                nonlocal invocation_count
                with count_lock:
                    invocation_count += 1
                    invocation = invocation_count
                if invocation == 1:
                    first_entered.set()
                    if not release_first.wait(5):
                        raise AssertionError("test did not release the first subprocess")
                else:
                    second_entered.set()
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            def run_second_call():
                second_started.set()
                return relay.run_herdr("pane", "read", "second", remote="same-host")

            with mock.patch.object(relay.subprocess, "run", side_effect=fake_subprocess_run):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(
                        relay.run_herdr, "pane", "read", "first", remote="same-host"
                    )
                    self.assertTrue(first_entered.wait(2), "first subprocess did not start")
                    second = executor.submit(run_second_call)
                    self.assertTrue(second_started.wait(2), "second call did not start")
                    try:
                        self.assertFalse(
                            second_entered.wait(0.5),
                            "second subprocess entered before the first completed",
                        )
                    finally:
                        release_first.set()
                    self.assertEqual(first.result(timeout=2), "ok")
                    self.assertEqual(second.result(timeout=2), "ok")

            self.assertTrue(second_entered.is_set())
            self.assertEqual(invocation_count, 2)

    def test_other_remotes_and_local_calls_do_not_share_a_lock(self):
        with loaded_relay() as relay:
            entered = {
                "blocked-host": threading.Event(),
                "other-host": threading.Event(),
                "local": threading.Event(),
            }
            release_blocked = threading.Event()

            def command_target(command):
                if Path(command[0]).name != "ssh":
                    return "local"
                batch_mode_index = command.index("BatchMode=yes")
                return command[batch_mode_index + 1]

            def fake_subprocess_run(command, **kwargs):
                target = command_target(command)
                entered[target].set()
                if target == "blocked-host" and not release_blocked.wait(5):
                    raise AssertionError("test did not release blocked-host")
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            with mock.patch.object(relay.subprocess, "run", side_effect=fake_subprocess_run):
                with ThreadPoolExecutor(max_workers=3) as executor:
                    blocked = executor.submit(
                        relay.run_herdr, "pane", "read", "one", remote="blocked-host"
                    )
                    self.assertTrue(
                        entered["blocked-host"].wait(2),
                        "blocked remote subprocess did not start",
                    )
                    other = executor.submit(
                        relay.run_herdr, "pane", "read", "two", remote="other-host"
                    )
                    local = executor.submit(relay.run_herdr, "pane", "read", "three")
                    try:
                        self.assertTrue(
                            entered["other-host"].wait(2),
                            "different remote was blocked by the first remote",
                        )
                        self.assertTrue(
                            entered["local"].wait(2),
                            "local execution was blocked by a remote",
                        )
                    finally:
                        release_blocked.set()
                    self.assertEqual(blocked.result(timeout=2), "ok")
                    self.assertEqual(other.result(timeout=2), "ok")
                    self.assertEqual(local.result(timeout=2), "ok")


if __name__ == "__main__":
    unittest.main()
