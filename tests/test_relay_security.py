#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0"]
# ///
import base64
import importlib
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))
os.environ["HERDR_LOG_DIR"] = tempfile.mkdtemp(prefix="herdr-relay-test-")
os.environ["HERDR_RELAY_TOKEN"] = "a" * 64
os.environ.pop("HERDR_RELAY_HOST", None)
os.environ.pop("HERDR_ALLOW_REMOTE_BIND", None)
os.environ.pop("HERDR_ALLOW_INSECURE_NO_AUTH", None)

relay = importlib.import_module("herdr_relay")


class FakeWebSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.remote_address = ("127.0.0.1", 12345)
        self.request = SimpleNamespace(headers={"User-Agent": "test-client", "Origin": ""})

    def __aiter__(self):
        async def messages():
            for message in self.messages:
                yield json.dumps(message)
        return messages()

    async def send(self, message):
        self.sent.append(json.loads(message))


class RelaySecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        relay.known_panes.clear()
        relay.pane_remote_map.clear()
        relay.pane_raw_map.clear()
        relay.clients.clear()
        relay.agent_cache.clear()
        relay.agent_source_cache.clear()
        relay.last_statuses.clear()
        relay.client_auth.clear()
        relay.active_terminal_sessions.clear()
        relay.machine_access_cache = None
        relay.agent_start_in_progress = False

    def test_secure_runtime_defaults_to_loopback_and_requires_auth(self):
        self.assertEqual(relay.RELAY_HOST, "127.0.0.1")
        self.assertFalse(relay.ALLOW_REMOTE_BIND)
        self.assertFalse(relay.ALLOW_INSECURE_NO_AUTH)
        relay.validate_runtime_config()

        with patch.object(relay, "AUTH_TOKEN", ""):
            with self.assertRaisesRegex(RuntimeError, "HERDR_RELAY_TOKEN is required"):
                relay.validate_runtime_config()

        self.assertEqual(stat.S_IMODE(os.stat(relay.LOG_FILE).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(relay.AUDIT_FILE).st_mode), 0o600)

        with (
            patch.object(relay, "RELAY_HOST", "0.0.0.0"),
            patch.object(relay, "ALLOW_REMOTE_BIND", False),
        ):
            with self.assertRaisesRegex(RuntimeError, "Remote relay binding is disabled"):
                relay.validate_runtime_config()

    def test_tailscale_runtime_requires_loopback_and_an_explicit_user_allowlist(self):
        with (
            patch.object(relay, "AUTH_TOKEN", ""),
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
            patch.object(relay, "RELAY_HOST", "127.0.0.1"),
        ):
            relay.validate_runtime_config()

        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", set()),
        ):
            with self.assertRaisesRegex(RuntimeError, "HERDR_TAILSCALE_ALLOWED_USERS"):
                relay.validate_runtime_config()

        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
            patch.object(relay, "RELAY_HOST", "0.0.0.0"),
            patch.object(relay, "ALLOW_REMOTE_BIND", True),
        ):
            with self.assertRaisesRegex(RuntimeError, "requires HERDR_RELAY_HOST to be loopback"):
                relay.validate_runtime_config()

    async def test_tailscale_websocket_auth_checks_user_proxy_and_origin(self):
        headers = {
            "Upgrade": "websocket",
            "Host": "herdr.tailnet.ts.net",
            "Origin": "https://herdr.tailnet.ts.net",
            "Tailscale-User-Login": "owner@example.com",
            "Tailscale-User-Name": "Owner",
        }
        request = SimpleNamespace(path="/ws", headers=headers)
        connection = SimpleNamespace(remote_address=("127.0.0.1", 43123))

        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
        ):
            response = await relay.process_request(connection, request)

        self.assertIsNone(response)
        self.assertEqual(relay.client_auth.pop(id(connection))["login"], "owner@example.com")

        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"someone-else@example.com"}),
        ):
            response = await relay.process_request(connection, request)
        self.assertEqual(response.status_code, 403)

        evil_request = SimpleNamespace(
            path="/ws",
            headers={**headers, "Origin": "https://attacker.example"},
        )
        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
        ):
            response = await relay.process_request(connection, evil_request)
        self.assertEqual(response.status_code, 403)

        wrong_port_request = SimpleNamespace(
            path="/ws",
            headers={**headers, "Origin": "https://herdr.tailnet.ts.net:444"},
        )
        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
        ):
            response = await relay.process_request(connection, wrong_port_request)
        self.assertEqual(response.status_code, 403)

        untrusted_connection = SimpleNamespace(remote_address=("100.64.0.2", 43123))
        with (
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"owner@example.com"}),
        ):
            response = await relay.process_request(untrusted_connection, request)
        self.assertEqual(response.status_code, 401)

    def test_token_query_can_be_exchanged_for_a_short_lived_http_only_session(self):
        token = "b" * 64
        request = SimpleNamespace(
            path=f"/?token={token}",
            headers={"Host": "relay.example.com"},
        )
        connection = SimpleNamespace(remote_address=("127.0.0.1", 43123))
        with patch.object(relay, "AUTH_TOKEN", token):
            auth = relay.authenticate_request(connection, request)
            self.assertTrue(auth["set_cookie"])
            cookie_header = relay.web_session_cookie(request)
            self.assertIn("HttpOnly", cookie_header)
            self.assertIn("SameSite=Strict", cookie_header)
            self.assertIn("; Secure", cookie_header)

            session = relay.create_web_session(now=1000)
            self.assertTrue(relay.valid_web_session(session, now=1001))
            self.assertFalse(
                relay.valid_web_session(session, now=1000 + relay.WEB_SESSION_TTL_SECONDS + 1)
            )

    async def test_token_bootstrap_serves_split_assets_through_session_cookie(self):
        token = "c" * 64
        connection = SimpleNamespace(remote_address=("127.0.0.1", 43123))
        initial_request = SimpleNamespace(
            path=f"/?token={token}",
            headers={"Host": "127.0.0.1:8375"},
        )
        with patch.object(relay, "AUTH_TOKEN", token):
            response = await relay.process_request(connection, initial_request)
            self.assertEqual(response.status_code, 200)
            cookie = response.headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            cookie_pair = cookie.split(";", 1)[0]

            asset_request = SimpleNamespace(
                path="/app.js",
                headers={"Host": "127.0.0.1:8375", "Cookie": cookie_pair},
            )
            asset_response = await relay.process_request(connection, asset_request)
            font_request = SimpleNamespace(
                path="/vendor/fonts/firacode-nerd-mono-v3.3.0.woff2",
                headers={"Host": "127.0.0.1:8375", "Cookie": cookie_pair},
            )
            font_response = await relay.process_request(connection, font_request)

        self.assertEqual(asset_response.status_code, 200)
        self.assertIn(b"new WebSocket", asset_response.body)
        self.assertEqual(font_response.status_code, 200)
        self.assertEqual(font_response.headers["Content-Type"], "font/woff2")
        self.assertTrue(font_response.body.startswith(b"wOF2"))

    def test_web_terminal_requires_a_separate_tailscale_user_allowlist(self):
        tailscale_auth = {
            "mode": "tailscale",
            "login": "owner@example.com",
            "name": "Owner",
        }
        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
        ):
            self.assertTrue(relay.terminal_access_allowed(tailscale_auth))
            self.assertFalse(relay.terminal_access_allowed({"mode": "token", "login": ""}))

        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"someone@example.com"}),
        ):
            self.assertFalse(relay.terminal_access_allowed(tailscale_auth))

        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"*"}),
        ):
            self.assertFalse(relay.terminal_access_allowed(tailscale_auth))

    def test_web_terminal_runtime_rejects_a_wildcard_user_allowlist(self):
        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TAILSCALE_WEB_ENABLED", True),
            patch.object(relay, "TAILSCALE_ALLOWED_USERS", {"*"}),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"*"}),
            self.assertRaisesRegex(RuntimeError, "wildcard terminal access"),
        ):
            relay.validate_runtime_config()

    async def test_authorized_web_terminal_stream_uses_scoped_session_messages(self):
        profile = {
            "id": "local",
            "kind": "local",
            "label": "本机终端",
            "target": "workstation",
            "port": 0,
            "description": "",
            "color": "violet",
        }
        typed = b"git status\n"
        ws = FakeWebSocket([
            {"type": "terminal_open", "profile_id": "local", "cols": 100, "rows": 30},
            {
                "type": "terminal_input",
                "session_id": "terminal-test",
                "data": base64.b64encode(typed).decode(),
            },
            {
                "type": "terminal_resize",
                "session_id": "terminal-test",
                "cols": 120,
                "rows": 40,
            },
            {"type": "terminal_close", "session_id": "terminal-test"},
        ])
        relay.client_auth[id(ws)] = {
            "mode": "tailscale",
            "login": "owner@example.com",
            "name": "Owner",
        }
        instances = []

        class FakeTerminalSession:
            def __init__(self, selected_profile, event_handler, **options):
                self.profile = selected_profile
                self.event_handler = event_handler
                self.session_id = "terminal-test"
                self.persistent = True
                self.cols = int(options["cols"])
                self.rows = int(options["rows"])
                self.writes = []
                self.closed = False
                instances.append(self)

            async def spawn(self):
                return None

            def start_reader(self):
                return None

            async def write(self, data):
                self.writes.append(data)

            async def resize(self, cols, rows):
                self.cols = int(cols)
                self.rows = int(rows)
                return self.cols, self.rows

            async def close(self):
                self.closed = True

        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
            patch.object(relay, "TAILSCALE_SSH_ENABLED", True),
            patch.object(relay, "TerminalSession", FakeTerminalSession),
            patch.object(relay, "configured_terminal_profiles", return_value=[profile]),
            patch.object(relay, "machine_access_info", return_value={"hostname": "workstation"}),
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        self.assertEqual(instances[0].writes, [typed])
        self.assertTrue(instances[0].closed)
        self.assertIn(
            {
                "type": "terminal_opened",
                "session_id": "terminal-test",
                "profile": profile,
                "persistent": True,
                "cols": 100,
                "rows": 30,
            },
            ws.sent,
        )
        session_message = next(message for message in ws.sent if message["type"] == "session")
        self.assertTrue(session_message["features"]["terminal"])
        self.assertEqual(session_message["terminal_profiles"], [profile])
        audit_details = [call.args[4] for call in audit.call_args_list]
        self.assertFalse(any("git status" in detail for detail in audit_details))

    async def test_token_client_cannot_open_a_web_terminal(self):
        ws = FakeWebSocket([{"type": "terminal_open", "profile_id": "local"}])
        relay.client_auth[id(ws)] = {"mode": "token", "login": ""}
        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
            patch.object(relay, "machine_access_info", return_value={}),
        ):
            await relay.handle_client(ws)

        self.assertIn(
            {
                "type": "terminal_error",
                "operation": "terminal",
                "message": "Web terminal access is not authorized",
            },
            ws.sent,
        )

    async def test_editing_an_ssh_endpoint_retires_the_previous_tmux_session(self):
        previous = {
            "id": "build",
            "kind": "ssh",
            "label": "Build",
            "target": "builder@192.168.1.20",
            "port": 22,
            "description": "",
            "color": "cyan",
        }
        saved = {**previous, "target": "builder@192.168.1.21"}
        ws = FakeWebSocket([{"type": "ssh_profile_save", "profile": saved}])
        relay.client_auth[id(ws)] = {
            "mode": "tailscale",
            "login": "owner@example.com",
            "name": "Owner",
        }

        with (
            patch.object(relay, "WEB_TERMINAL_ENABLED", True),
            patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
            patch.object(relay, "TMUX_BINARY", "/usr/bin/tmux"),
            patch.object(relay, "terminal_profile", return_value=previous),
            patch.object(relay, "save_ssh_profile", return_value=saved),
            patch.object(relay, "terminate_persistent_session") as terminate,
            patch.object(relay, "configured_terminal_profiles", return_value=[saved]),
            patch.object(relay, "machine_access_info", return_value={}),
            patch.object(relay, "audit"),
        ):
            await relay.handle_client(ws)

        terminate.assert_called_once_with(previous, "/usr/bin/tmux")
        self.assertIn(
            {
                "type": "command_result",
                "command": "ssh_profile_save",
                "ok": True,
                "profile_id": "build",
            },
            ws.sent,
        )

    async def test_agent_prompt_uses_herdr_api_and_redacts_audit_content(self):
        pane_id = "w0:p1"
        prompt = "use secret-token-123 to run tests"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{"type": "agent_prompt", "pane_id": pane_id, "text": prompt}])

        with (
            patch.object(relay, "submit_agent_prompt") as submit_prompt,
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value="queued"),
            ) as to_thread,
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        to_thread.assert_awaited_once_with(submit_prompt, pane_id, prompt, None)
        submit_prompt.assert_not_called()
        self.assertIn(
            {
                "type": "command_result",
                "command": "agent_prompt",
                "ok": True,
                "delivery": "queued",
            },
            ws.sent,
        )
        detail = audit.call_args.args[4]
        self.assertIn(f"chars={len(prompt)}", detail)
        self.assertNotIn("secret-token-123", detail)

    async def test_agent_prompt_queue_uses_tab_cache_and_redacts_audit_content(self):
        pane_id = "w0:p1"
        prompt = "queue secret-token-789 after the current task"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{
            "type": "agent_prompt_queue",
            "pane_id": pane_id,
            "text": prompt,
        }])

        with (
            patch.object(relay, "cache_agent_prompt_with_tab") as cache_prompt,
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value="cached"),
            ) as to_thread,
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        to_thread.assert_awaited_once_with(cache_prompt, pane_id, prompt, None)
        cache_prompt.assert_not_called()
        self.assertIn(
            {
                "type": "command_result",
                "command": "agent_prompt_queue",
                "ok": True,
                "delivery": "cached",
            },
            ws.sent,
        )
        detail = audit.call_args.args[4]
        self.assertIn(f"chars={len(prompt)}", detail)
        self.assertNotIn("secret-token-789", detail)

    def test_submit_agent_prompt_waits_for_observed_state_change(self):
        prompt = "run the tests"
        idle = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "idle",
                        "interactive_ready": True,
                        "state_change_seq": 41,
                    }
                }
            }),
            stderr="",
        )
        accepted = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({"result": {"agent": {"agent_status": "working"}}}),
            stderr="",
        )

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[idle, accepted],
        ) as run_herdr:
            self.assertEqual(relay.submit_agent_prompt("w0:p1", prompt), "confirmed")

        self.assertEqual(
            run_herdr.call_args_list[1],
            unittest.mock.call(
                "agent", "prompt", "w0:p1", prompt,
                "--wait",
                "--until", "working",
                "--until", "blocked",
                "--until", "done",
                "--timeout", "8000",
                remote=None,
                timeout=12,
            ),
        )

    def test_submit_agent_prompt_safely_retries_enter_after_stall(self):
        stalled = subprocess.CompletedProcess(
            [], 1,
            stdout="",
            stderr=json.dumps({
                "error": {
                    "code": "agent_prompt_stalled",
                    "message": "no observed state change",
                }
            }),
        )
        idle = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "idle",
                        "interactive_ready": True,
                        "state_change_seq": 41,
                    }
                }
            }),
            stderr="",
        )
        entered = subprocess.CompletedProcess([], 0, stdout='{"result":{}}', stderr="")
        working = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "working",
                        "interactive_ready": True,
                        "state_change_seq": 42,
                    }
                }
            }),
            stderr="",
        )

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[idle, stalled, idle, entered, working],
        ) as run_herdr:
            self.assertEqual(relay.submit_agent_prompt("w0:p1", "continue"), "confirmed")

        self.assertEqual(
            run_herdr.call_args_list[3],
            unittest.mock.call(
                "agent", "send-keys", "w0:p1", "Enter", remote=None, timeout=5,
            ),
        )

    def test_submit_agent_prompt_queues_without_waiting_if_agent_is_working(self):
        working = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "working",
                        "interactive_ready": True,
                        "state_change_seq": 42,
                    }
                }
            }),
            stderr="",
        )
        accepted = subprocess.CompletedProcess([], 0, stdout='{"result":{}}', stderr="")

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[working, accepted],
        ) as run_herdr:
            self.assertEqual(relay.submit_agent_prompt("w0:p1", "continue"), "queued")

        self.assertEqual(len(run_herdr.call_args_list), 2)
        self.assertEqual(
            run_herdr.call_args_list[1],
            unittest.mock.call(
                "agent", "prompt", "w0:p1", "continue", remote=None, timeout=5,
            ),
        )

    def test_cache_agent_prompt_appends_literal_tab_in_one_terminal_write(self):
        working = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent": "codex",
                        "agent_status": "working",
                        "state_change_seq": 42,
                    }
                }
            }),
            stderr="",
        )
        accepted = subprocess.CompletedProcess([], 0, stdout='{"result":{}}', stderr="")

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[working, accepted],
        ) as run_herdr:
            self.assertEqual(
                relay.cache_agent_prompt_with_tab("w0:p1", "continue"),
                "cached",
            )

        self.assertEqual(
            run_herdr.call_args_list[1],
            unittest.mock.call(
                "pane", "send-text", "w0:p1", "continue\t", remote=None, timeout=5,
            ),
        )

    def test_cache_agent_prompt_rejects_an_agent_that_is_no_longer_working(self):
        idle = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent": "codex",
                        "agent_status": "idle",
                        "state_change_seq": 43,
                    }
                }
            }),
            stderr="",
        )

        with patch.object(relay, "run_herdr_result", return_value=idle) as run_herdr:
            with self.assertRaisesRegex(ValueError, "no longer working"):
                relay.cache_agent_prompt_with_tab("w0:p1", "continue")

        run_herdr.assert_called_once_with("agent", "get", "w0:p1", remote=None, timeout=5)

    def test_submit_agent_prompt_accepts_timeout_after_state_advanced(self):
        idle = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "idle",
                        "interactive_ready": True,
                        "state_change_seq": 41,
                    }
                }
            }),
            stderr="",
        )
        timed_out = subprocess.CompletedProcess(
            [], 1,
            stdout="",
            stderr=json.dumps({"error": {"code": "timeout"}}),
        )
        working = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "working",
                        "state_change_seq": 42,
                    }
                }
            }),
            stderr="",
        )

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[idle, timed_out, working],
        ) as run_herdr:
            self.assertEqual(relay.submit_agent_prompt("w0:p1", "continue"), "confirmed")

        self.assertEqual(len(run_herdr.call_args_list), 3)
        self.assertFalse(any("send-keys" in call.args for call in run_herdr.call_args_list))

    def test_submit_agent_prompt_fails_if_enter_has_no_observed_effect(self):
        stalled = subprocess.CompletedProcess(
            [], 1,
            stdout="",
            stderr=json.dumps({"error": {"code": "agent_prompt_stalled"}}),
        )
        idle = subprocess.CompletedProcess(
            [], 0,
            stdout=json.dumps({
                "result": {
                    "agent": {
                        "agent_status": "idle",
                        "interactive_ready": True,
                        "state_change_seq": 41,
                    }
                }
            }),
            stderr="",
        )
        entered = subprocess.CompletedProcess([], 0, stdout='{"result":{}}', stderr="")

        with (
            patch.object(relay, "AGENT_PROMPT_CONFIRM_ATTEMPTS", 2),
            patch.object(relay.time, "sleep") as sleep,
            patch.object(
                relay,
                "run_herdr_result",
                side_effect=[idle, stalled, idle, entered, idle, idle],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "did not start after Enter"):
                relay.submit_agent_prompt("w0:p1", "continue")

        sleep.assert_called_once_with(relay.AGENT_PROMPT_CONFIRM_INTERVAL_SECONDS)

    async def test_pane_read_line_count_is_bounded(self):
        pane_id = "w0:p1"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{"type": "read_pane", "pane_id": pane_id, "lines": 999999}])

        with patch.object(relay, "run_herdr", return_value="output") as run_herdr:
            await relay.handle_client(ws)

        run_herdr.assert_called_once_with(
            "pane", "read", pane_id, "--lines", "200", "--source", "recent", remote=None
        )

    def test_remote_herdr_arguments_are_shell_quoted(self):
        prompt = "hello; touch /tmp/should-not-run"
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(relay.subprocess, "run", return_value=completed) as run:
            relay.run_herdr_result("agent", "prompt", "w0:p1", prompt, remote="user@example")

        command = run.call_args.args[0]
        self.assertEqual(command[-1], shlex.join([relay.HERDR, "agent", "prompt", "w0:p1", prompt]))
        self.assertNotIn(prompt, command[:-1])

    def test_remote_agents_receive_source_scoped_global_pane_ids(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
            "herdr_bin": "/home/dev/.local/bin/herdr",
        })
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({
                "result": {
                    "panes": [{
                        "pane_id": "w0:p1",
                        "agent": "codex",
                        "agent_status": "working",
                        "cwd": "/home/dev/Workspace/project",
                    }],
                },
            }),
            stderr="",
        )
        with patch.object(relay, "run_herdr_result", return_value=completed):
            agents, status = relay.get_agents_from_source(source)

        self.assertEqual(agents[0]["pane_id"], "build::w0:p1")
        self.assertEqual(agents[0]["raw_pane_id"], "w0:p1")
        self.assertEqual(agents[0]["source_id"], "build")
        self.assertEqual(agents[0]["host"], "Build Server")
        self.assertEqual(status["status"], "online")

    async def test_namespaced_pane_routes_commands_to_the_raw_remote_pane(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
        })
        pane_id = "build::w0:p1"
        relay.known_panes.add(pane_id)
        relay.pane_raw_map[pane_id] = "w0:p1"
        relay.pane_remote_map[pane_id] = source
        ws = FakeWebSocket([{"type": "read_pane", "pane_id": pane_id, "lines": 40}])

        with patch.object(relay, "run_herdr", return_value="remote output") as run_herdr:
            await relay.handle_client(ws)

        run_herdr.assert_called_once_with(
            "pane", "read", "w0:p1", "--lines", "40", "--source", "recent", remote=source
        )
        self.assertIn(
            {"type": "pane_content", "pane_id": pane_id, "content": "remote output"},
            ws.sent,
        )

    async def test_poll_keeps_identical_local_and_remote_raw_pane_ids_distinct(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
        })
        local_agent = {
            "pane_id": "w0:p1",
            "raw_pane_id": "w0:p1",
            "source_id": "local",
            "agent": "codex",
            "status": "idle",
            "cwd": "/tmp/local",
            "project": "local",
            "host": "local",
        }
        remote_agent = {
            **local_agent,
            "pane_id": "build::w0:p1",
            "source_id": "build",
            "cwd": "/tmp/remote",
            "project": "remote",
            "host": "Build Server",
        }
        statuses = [
            relay.source_status(relay.local_agent_source(), "online", agent_count=1),
            relay.source_status(source, "online", agent_count=1),
        ]
        with (
            patch.object(
                relay,
                "collect_all_agents",
                new=AsyncMock(return_value=(
                    [local_agent, remote_agent],
                    statuses,
                    {"local": relay.local_agent_source(), "build": source},
                )),
            ),
            patch.object(relay, "broadcast", new=AsyncMock()),
        ):
            await relay._poll_once()

        self.assertEqual(set(relay.agent_cache), {"w0:p1", "build::w0:p1"})
        self.assertIsNone(relay.pane_remote_map["w0:p1"])
        self.assertEqual(relay.pane_remote_map["build::w0:p1"], source)

    def test_remote_workspace_listing_uses_profile_roots(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
            "workspace_roots": ["~/Workspace", "/srv/models"],
        })
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({
                "path": "/home/dev/Workspace/project",
                "display_path": "~/Workspace/project",
                "parent": "/home/dev/Workspace",
                "entries": [],
                "can_start_agent": True,
                "git_root": "/home/dev/Workspace/project",
                "truncated": False,
            }),
            stderr="",
        )
        with patch.object(relay, "run_remote_result", return_value=completed) as run_remote:
            listing = relay.remote_workspace_directory_listing(
                source,
                "/home/dev/Workspace/project",
            )

        arguments = run_remote.call_args.args[1]
        self.assertEqual(arguments[0:2], ["python3", "-c"])
        self.assertEqual(json.loads(arguments[3]), ["~/Workspace", "/srv/models"])
        self.assertEqual(arguments[4], "/home/dev/Workspace/project")
        self.assertTrue(listing["can_start_agent"])

    def test_remote_codex_start_returns_a_namespaced_agent(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
            "herdr_bin": "/home/dev/.local/bin/herdr",
        })
        created = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({
                "result": {
                    "workspace": {"workspace_id": "w9"},
                    "root_pane": {"pane_id": "w9:p1"},
                },
            }),
            stderr="",
        )
        started = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({
                "result": {
                    "agent": {"pane_id": "w9:p1", "agent_status": "idle"},
                },
            }),
            stderr="",
        )
        listing = {
            "path": "/home/dev/Workspace/project",
            "display_path": "~/Workspace/project",
            "can_start_agent": True,
        }
        with (
            patch.object(relay, "remote_workspace_directory_listing", return_value=listing),
            patch.object(relay, "run_herdr_result", side_effect=[created, started]) as run_herdr,
        ):
            result = relay.start_codex_on_source(listing["path"], "", source)

        self.assertEqual(result["pane_id"], "build::w9:p1")
        self.assertEqual(result["raw_pane_id"], "w9:p1")
        self.assertEqual(result["source_id"], "build")
        self.assertEqual(result["host"], "Build Server")
        self.assertEqual(run_herdr.call_args_list[0].kwargs["remote"], source)
        self.assertEqual(run_herdr.call_args_list[1].kwargs["remote"], source)

    def test_workspace_browsing_is_confined_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            outside = Path(temp_dir) / "outside"
            repo = root / "repo"
            ordinary = root / "ordinary"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            ordinary.mkdir()
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)

            with patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]):
                listing = relay.workspace_directory_listing(str(root))
                repo_entry = next(entry for entry in listing["entries"] if entry["name"] == "repo")
                self.assertTrue(repo_entry["is_repo"])
                self.assertNotIn("escape", [entry["name"] for entry in listing["entries"]])
                self.assertTrue(listing["can_start_agent"])
                self.assertTrue(relay.workspace_directory_listing(str(repo))["can_start_agent"])
                ordinary_listing = relay.workspace_directory_listing(str(ordinary))
                self.assertTrue(ordinary_listing["can_start_agent"])
                self.assertEqual(ordinary_listing["git_root"], "")
                with self.assertRaisesRegex(ValueError, "outside the configured roots"):
                    relay.resolve_workspace_path(str(outside))

    def test_codex_start_allows_an_ordinary_directory_and_waits_for_the_new_shell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "ordinary"
            directory.mkdir()
            created = subprocess.CompletedProcess(
                [], 0,
                stdout=json.dumps({
                    "result": {
                        "workspace": {"workspace_id": "w9"},
                        "root_pane": {"pane_id": "w9:p1"},
                    }
                }),
                stderr="",
            )
            pane_busy = subprocess.CompletedProcess(
                [], 1,
                stdout=json.dumps({
                    "error": {
                        "code": "agent_pane_busy",
                        "message": "agent target pane w9:p1 is not an available shell",
                    }
                }),
                stderr="",
            )
            started = subprocess.CompletedProcess(
                [], 0,
                stdout=json.dumps({
                    "result": {
                        "agent": {"pane_id": "w9:p1", "agent_status": "idle"},
                    }
                }),
                stderr="",
            )

            with (
                patch.object(relay, "WORKSPACE_ROOTS", [directory.parent.resolve()]),
                patch.object(
                    relay,
                    "run_herdr_result",
                    side_effect=[created, pane_busy, started],
                ) as run_herdr,
                patch.object(relay, "submit_agent_prompt", return_value=True) as submit_prompt,
                patch.object(relay.time, "sleep") as sleep,
            ):
                result = relay.start_local_codex(str(directory), "inspect; do not run a shell")

            self.assertEqual(result["pane_id"], "w9:p1")
            self.assertTrue(result["prompted"])
            self.assertEqual(
                run_herdr.call_args_list[0],
                unittest.mock.call(
                    "workspace", "create", "--cwd", str(directory), "--label", "ordinary", "--no-focus",
                    timeout=20,
                ),
            )
            self.assertEqual(
                run_herdr.call_args_list[1],
                unittest.mock.call(
                    "agent", "start", "codex-w9", "--kind", "codex", "--pane", "w9:p1",
                    "--timeout", "60000", timeout=75,
                ),
            )
            self.assertEqual(run_herdr.call_args_list[2], run_herdr.call_args_list[1])
            sleep.assert_called_once_with(relay.AGENT_START_PANE_READY_INTERVAL_SECONDS)
            submit_prompt.assert_called_once_with("w9:p1", "inspect; do not run a shell")

    async def test_start_agent_message_redacts_prompt_and_broadcasts_new_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            prompt = "use private-token-456 to inspect the project"
            ws = FakeWebSocket([{
                "type": "start_agent",
                "kind": "codex",
                "cwd": str(repo),
                "prompt": prompt,
            }])
            started_agent = {
                "pane_id": "w9:p1",
                "workspace_id": "w9",
                "cwd": str(repo),
                "display_path": str(repo),
                "project": "repo",
                "agent": "codex",
                "status": "idle",
                "prompted": True,
                "warning": "",
            }

            with (
                patch.object(relay, "WORKSPACE_ROOTS", [repo.parent.resolve()]),
                patch.object(relay, "start_local_codex") as start_codex,
                patch.object(
                    relay.asyncio,
                    "to_thread",
                    new=AsyncMock(return_value=started_agent),
                ) as to_thread,
                patch.object(relay, "broadcast", new=AsyncMock()) as broadcast,
                patch.object(relay, "audit") as audit,
            ):
                await relay.handle_client(ws)

            to_thread.assert_awaited_once_with(start_codex, str(repo.resolve()), prompt)
            start_codex.assert_not_called()
            self.assertIn({"type": "agent_started", "ok": True, "agent": started_agent}, ws.sent)
            self.assertEqual(broadcast.await_args.args[0]["agent"]["pane_id"], "w9:p1")
            self.assertIn(f"chars={len(prompt)}", audit.call_args.args[4])
            self.assertNotIn("private-token-456", audit.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
