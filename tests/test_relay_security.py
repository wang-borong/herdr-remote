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
        relay.workspace_downloads.clear()
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

    def test_workspace_roots_default_to_home_and_migrate_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            workspace = home / "Workspace"
            workspace_ai = home / "workspace-ai"
            custom = home / "Projects"
            outside = Path(temporary) / "outside"
            for directory in (workspace, workspace_ai, custom, outside):
                directory.mkdir(parents=True)

            with patch.dict(os.environ, {"HOME": str(home)}, clear=False):
                os.environ.pop("HERDR_WORKSPACE_ROOTS", None)
                self.assertEqual(relay.configured_workspace_roots(), [home.resolve()])

                os.environ["HERDR_WORKSPACE_ROOTS"] = os.pathsep.join((
                    str(workspace),
                    str(workspace_ai),
                ))
                self.assertEqual(relay.configured_workspace_roots(), [home.resolve()])

                os.environ["HERDR_WORKSPACE_ROOTS"] = os.pathsep.join((
                    str(workspace),
                    str(custom),
                ))
                self.assertEqual(
                    relay.configured_workspace_roots(),
                    [workspace.resolve(), custom.resolve()],
                )

                with patch.object(relay, "WORKSPACE_ROOTS", [home.resolve()]):
                    self.assertEqual(relay.display_workspace_path(home.resolve()), "~")
                    self.assertEqual(relay.resolve_workspace_path(str(custom)), custom.resolve())
                    with self.assertRaisesRegex(ValueError, "outside the configured roots"):
                        relay.resolve_workspace_path(str(outside))

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
            preview_request = SimpleNamespace(
                path="/vendor/preview/marked-18.0.10.js",
                headers={"Host": "127.0.0.1:8375", "Cookie": cookie_pair},
            )
            preview_response = await relay.process_request(connection, preview_request)

        self.assertEqual(asset_response.status_code, 200)
        self.assertIn(b"new WebSocket", asset_response.body)
        self.assertIn(
            "img-src 'self' data: blob:",
            asset_response.headers["Content-Security-Policy"],
        )
        self.assertEqual(font_response.status_code, 200)
        self.assertEqual(font_response.headers["Content-Type"], "font/woff2")
        self.assertTrue(font_response.body.startswith(b"wOF2"))
        self.assertEqual(preview_response.status_code, 200)
        self.assertIn(b"marked v18.0.10", preview_response.body)
        self.assertIn("immutable", preview_response.headers["Cache-Control"])

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
                "resize_id": 9,
                "cols": 120,
                "rows": 40,
            },
            {
                "type": "terminal_capture",
                "session_id": "terminal-test",
                "capture_id": 7,
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

            async def capture(self):
                return "older output\nlatest output", True

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
        self.assertIn(
            {
                "type": "terminal_capture",
                "session_id": "terminal-test",
                "capture_id": 7,
                "content": "older output\nlatest output",
                "truncated": True,
            },
            ws.sent,
        )
        self.assertIn(
            {
                "type": "terminal_resized",
                "session_id": "terminal-test",
                "resize_id": 9,
                "cols": 120,
                "rows": 40,
            },
            ws.sent,
        )
        session_message = next(message for message in ws.sent if message["type"] == "session")
        self.assertTrue(session_message["features"]["terminal"])
        self.assertTrue(session_message["features"]["workspace_files"])
        self.assertTrue(session_message["features"]["workspace_upload"])
        self.assertEqual(
            session_message["limits"]["workspace_upload_max_bytes"],
            relay.WORKSPACE_UPLOAD_MAX_BYTES,
        )
        self.assertEqual(
            session_message["limits"]["workspace_upload_chunk_bytes"],
            relay.WORKSPACE_UPLOAD_CHUNK_BYTES,
        )
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
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(side_effect=lambda function, *args: function(*args)),
            ),
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

    async def test_agent_prompt_accepts_long_text_without_an_arbitrary_character_limit(self):
        pane_id = "w0:p1"
        prompt = "x" * 5000
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{"type": "agent_prompt", "pane_id": pane_id, "text": prompt}])

        with (
            patch.object(relay, "submit_agent_prompt") as submit_prompt,
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value="confirmed"),
            ) as to_thread,
            patch.object(relay, "audit"),
        ):
            await relay.handle_client(ws)

        to_thread.assert_awaited_once_with(submit_prompt, pane_id, prompt, None)
        self.assertIn(
            {
                "type": "command_result",
                "command": "agent_prompt",
                "ok": True,
                "delivery": "confirmed",
            },
            ws.sent,
        )

    async def test_agent_prompt_routes_validated_images_to_codex_session_queue(self):
        pane_id = "w0:p1"
        prompt = "Inspect this screenshot"
        png = b"\x89PNG\r\n\x1a\nimage-data"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{
            "type": "agent_prompt",
            "pane_id": pane_id,
            "text": prompt,
            "images": [{
                "name": "screen.png",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii"),
            }],
        }])

        with (
            patch.object(relay, "submit_codex_image_prompt") as submit_images,
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value="confirmed"),
            ) as to_thread,
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        to_thread.assert_awaited_once_with(
            submit_images,
            pane_id,
            prompt,
            [{"media_type": "image/png", "data": png}],
            None,
        )
        self.assertIn(
            {
                "type": "command_result",
                "command": "agent_prompt",
                "ok": True,
                "delivery": "confirmed",
            },
            ws.sent,
        )
        self.assertIn("images=1", audit.call_args.args[4])

    async def test_agent_prompt_rejects_spoofed_image_content_before_submission(self):
        pane_id = "w0:p1"
        relay.known_panes.add(pane_id)
        ws = FakeWebSocket([{
            "type": "agent_prompt",
            "pane_id": pane_id,
            "text": "Inspect this",
            "images": [{
                "media_type": "image/png",
                "data": base64.b64encode(b"not-a-png").decode("ascii"),
            }],
        }])

        with patch.object(relay.asyncio, "to_thread", new=AsyncMock()) as to_thread:
            await relay.handle_client(ws)

        to_thread.assert_not_awaited()
        self.assertTrue(any("does not match" in message.get("message", "") for message in ws.sent))

    def test_decode_prompt_images_limits_count_and_accepts_supported_magic(self):
        png = b"\x89PNG\r\n\x1a\nimage-data"
        payload = {"media_type": "image/png", "data": base64.b64encode(png).decode("ascii")}

        self.assertEqual(
            relay.decode_prompt_images([payload]),
            [{"media_type": "image/png", "data": png}],
        )
        with self.assertRaisesRegex(ValueError, "at most"):
            relay.decode_prompt_images([payload] * (relay.PROMPT_IMAGE_MAX_COUNT + 1))

    def test_local_codex_image_prompt_uses_session_queue_and_cleans_temporary_files(self):
        png = b"\x89PNG\r\n\x1a\nimage-data"
        agent = {
            "agent": "codex",
            "agent_status": "idle",
            "agent_session": {
                "agent": "codex",
                "kind": "id",
                "source": "herdr:codex",
                "value": "01a0467e-11a9-7963-a359-c169979eaffd",
            },
        }
        observed_paths = []

        def run_codex(arguments, **kwargs):
            image_paths = [
                arguments[index + 1]
                for index, value in enumerate(arguments)
                if value == "--image"
            ]
            self.assertEqual(len(image_paths), 1)
            self.assertEqual(Path(image_paths[0]).read_bytes(), png)
            observed_paths.extend(image_paths)
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        with (
            patch.object(relay, "CODEX", "/usr/bin/codex"),
            patch.object(relay, "get_agent_info", return_value=agent),
            patch.object(relay.subprocess, "run", side_effect=run_codex) as run,
        ):
            delivery = relay.submit_codex_image_prompt(
                "w0:p1",
                "Inspect this screenshot",
                [{"media_type": "image/png", "data": png}],
            )

        self.assertEqual(delivery, "confirmed")
        self.assertEqual(run.call_count, 1)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:6], [
            "/usr/bin/codex",
            "queue",
            "--thread",
            agent["agent_session"]["value"],
            "--message",
            "Inspect this screenshot",
        ])
        self.assertTrue(observed_paths)
        self.assertFalse(Path(observed_paths[0]).exists())

    def test_remote_codex_image_prompt_uploads_queues_and_cleans_private_files(self):
        png = b"\x89PNG\r\n\x1a\nimage-data"
        source = {
            "kind": "ssh",
            "target": "builder@example",
            "port": 22,
            "codex_bin": "/home/dev/bin/codex",
        }
        agent = {
            "agent": "codex",
            "agent_status": "working",
            "agent_session": {"value": "01a0467e-11a9-7963-a359-c169979eaffd"},
        }
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with (
            patch.object(relay, "get_agent_info", return_value=agent),
            patch.object(relay.secrets, "token_hex", return_value="abc123"),
            patch.object(relay, "run_remote_result", return_value=completed) as run_remote,
        ):
            delivery = relay.submit_codex_image_prompt(
                "w0:p1",
                "Compare this image",
                [{"media_type": "image/png", "data": png}],
                remote=source,
            )

        self.assertEqual(delivery, "queued")
        remote_path = "/tmp/herdr-prompt-images-abc123/image-1.png"
        self.assertEqual(
            run_remote.call_args_list[1],
            unittest.mock.call(
                source,
                ["sh", "-c", 'umask 077; cat > "$1"', "herdr-image-upload", remote_path],
                timeout=20,
                input_data=png,
            ),
        )
        queue_arguments = run_remote.call_args_list[2].args[1]
        self.assertEqual(queue_arguments[:6], [
            "/home/dev/bin/codex",
            "queue",
            "--thread",
            agent["agent_session"]["value"],
            "--message",
            "Compare this image",
        ])
        self.assertIn(["--image", remote_path], [queue_arguments[-2:]])
        self.assertEqual(run_remote.call_args_list[3].args[1], ["rm", "-f", remote_path])
        self.assertEqual(
            run_remote.call_args_list[4].args[1],
            ["rmdir", "/tmp/herdr-prompt-images-abc123"],
        )

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

    def test_mark_agent_seen_focuses_an_authoritative_done_agent(self):
        done = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"result": {"agent": {"agent_status": "done"}}}),
            stderr="",
        )
        focused = subprocess.CompletedProcess([], 0, stdout='{"result":{}}', stderr="")
        idle = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"result": {"agent": {"agent_status": "idle"}}}),
            stderr="",
        )

        with patch.object(
            relay,
            "run_herdr_result",
            side_effect=[done, focused, idle],
        ) as run_herdr:
            agent, changed = relay.mark_agent_seen("w0:p1")

        self.assertTrue(changed)
        self.assertEqual(agent["agent_status"], "idle")
        self.assertEqual(
            run_herdr.call_args_list,
            [
                unittest.mock.call("agent", "get", "w0:p1", remote=None, timeout=5),
                unittest.mock.call("agent", "focus", "w0:p1", remote=None, timeout=5),
                unittest.mock.call("agent", "get", "w0:p1", remote=None, timeout=5),
            ],
        )

    def test_mark_agent_seen_does_not_focus_a_non_done_agent(self):
        working = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({"result": {"agent": {"agent_status": "working"}}}),
            stderr="",
        )

        with patch.object(relay, "run_herdr_result", return_value=working) as run_herdr:
            agent, changed = relay.mark_agent_seen("w0:p1")

        self.assertFalse(changed)
        self.assertEqual(agent["agent_status"], "working")
        run_herdr.assert_called_once_with("agent", "get", "w0:p1", remote=None, timeout=5)

    async def test_agent_seen_routes_remote_pane_and_broadcasts_authoritative_status(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
        })
        pane_id = "build::w17:p1"
        relay.known_panes.add(pane_id)
        relay.pane_raw_map[pane_id] = "w17:p1"
        relay.pane_remote_map[pane_id] = source
        relay.agent_cache[pane_id] = {
            "pane_id": pane_id,
            "raw_pane_id": "w17:p1",
            "source_id": "build",
            "agent": "codex",
            "status": "done",
            "project": "remote-project",
            "host": "Build Server",
        }
        ws = FakeWebSocket([{"type": "agent_seen", "pane_id": pane_id}])

        with (
            patch.object(relay, "mark_agent_seen") as mark_seen,
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value=({"agent_status": "idle"}, True)),
            ) as to_thread,
            patch.object(relay, "broadcast", new=AsyncMock()) as broadcast,
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        to_thread.assert_awaited_once_with(mark_seen, "w17:p1", source)
        mark_seen.assert_not_called()
        expected_agent = {
            **relay.agent_cache[pane_id],
            "status": "idle",
        }
        broadcast.assert_awaited_once_with({"type": "agent_update", "agent": expected_agent})
        audit.assert_called_once_with("agent_seen", "127.0.0.1", "unknown", pane_id)
        self.assertIn(
            {
                "type": "command_result",
                "command": "agent_seen",
                "ok": True,
                "changed": True,
                "status": "idle",
            },
            ws.sent,
        )

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
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout="\x1b[31moutput\x1b[0m",
            stderr="",
        )

        with patch.object(relay, "run_herdr_result", return_value=completed) as run_herdr:
            await relay.handle_client(ws)

        run_herdr.assert_called_once_with(
            "pane",
            "read",
            pane_id,
            "--lines",
            "1000",
            "--source",
            "recent-unwrapped",
            "--format",
            "ansi",
            remote=None,
        )
        self.assertIn(
            {
                "type": "pane_content",
                "pane_id": pane_id,
                "content": "output",
                "ansi_content": "\x1b[31moutput\x1b[0m",
            },
            ws.sent,
        )

    async def test_pane_read_falls_back_for_herdr_without_ansi_snapshots(self):
        pane_id = "w0:p1"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{"type": "read_pane", "pane_id": pane_id, "lines": 60}])
        unsupported = subprocess.CompletedProcess([], 2, stdout="", stderr="unknown option")

        with (
            patch.object(relay, "run_herdr_result", return_value=unsupported) as run_result,
            patch.object(relay, "run_herdr", return_value="plain output") as run_herdr,
        ):
            await relay.handle_client(ws)

        self.assertEqual(run_result.call_count, 2)
        self.assertEqual(
            [call.args[6] for call in run_result.call_args_list],
            ["recent-unwrapped", "recent"],
        )
        run_herdr.assert_called_once_with(
            "pane", "read", pane_id, "--lines", "60", "--source", "recent", remote=None
        )
        self.assertIn(
            {
                "type": "pane_content",
                "pane_id": pane_id,
                "content": "plain output",
                "ansi_content": "",
            },
            ws.sent,
        )

    def test_plain_terminal_output_removes_ansi_and_control_bytes(self):
        content = "\x1b[1;31mred\x1b[0m\r\n\x1b]8;;https://example.com\x07link\x1b]8;;\x07\x00"

        self.assertEqual(relay.plain_terminal_output(content), "red\nlink")

    def test_codex_snapshot_replaces_trailing_prompt_box_with_compact_metadata(self):
        background = "\x1b[48;2;55;64;68m"
        content = "\r\n".join([
            "\x1b[32mAgent response\x1b[0m",
            "",
            "\x1b[2m─ Worked for 38m 40s ───────────────────\x1b[0m",
            "1 background terminal running · /ps to view · /stop to close",
            "",
            f"{background}                                      \x1b[0m",
            f"{background}› Use /skills to list available skills\x1b[0m",
            f"{background}                                      \x1b[0m",
            "  \x1b[33mgpt-5.6-sol max\x1b[0m\x1b[2m · \x1b[0m\x1b[32m~/Workspace/project\x1b[0m",
        ])

        simplified = relay.simplify_codex_terminal_snapshot(content)

        self.assertEqual(
            relay.plain_terminal_output(simplified),
            "Agent response\n\nWorked for 38m 40s\n"
            "1 background terminal running\n"
            "gpt-5.6-sol max · ~/Workspace/project",
        )
        self.assertNotIn("Use /skills", simplified)
        self.assertNotIn("/ps to view", simplified)

    def test_codex_snapshot_preserves_earlier_user_message_background(self):
        background = "\x1b[48;2;55;64;68m"
        content = "\r\n".join([
            f"{background}› Earlier user prompt\x1b[0m",
            "",
            "\x1b[2m• Agent response\x1b[0m",
            "",
            "\x1b[1m• Working \x1b[0m\x1b[2m(13s • esc to interrupt) "
            "· 2 background terminals running · /ps to view · /stop to close\x1b[0m",
            "",
            f"{background}                                      \x1b[0m",
            f"{background}› Find and fix a bug in @filename\x1b[0m",
            f"{background}                                      \x1b[0m",
            "  gpt-5.6-sol max · ~/Workspace/project",
        ])

        simplified = relay.simplify_codex_terminal_snapshot(content)
        plain = relay.plain_terminal_output(simplified)

        self.assertIn("Earlier user prompt", plain)
        self.assertIn("Agent response", plain)
        self.assertIn("Working\ngpt-5.6-sol max · ~/Workspace/project", plain)
        self.assertNotIn("Find and fix a bug", plain)
        self.assertNotIn("esc to interrupt", plain)
        self.assertNotIn("background terminals running", plain)
        self.assertNotIn("/ps to view", plain)
        self.assertNotIn("/stop to close", plain)

    def test_codex_snapshot_uses_agent_status_when_working_line_is_transiently_missing(self):
        background = "\x1b[48;2;55;64;68m"
        content = "\r\n".join([
            "\x1b[2m• Agent response\x1b[0m",
            "",
            f"{background}                                      \x1b[0m",
            f"{background}› Find and fix a bug in @filename\x1b[0m",
            f"{background}                                      \x1b[0m",
            "  gpt-5.6-sol max · ~/Workspace/project",
        ])

        simplified = relay.simplify_codex_terminal_snapshot(content, agent_status="working")
        plain = relay.plain_terminal_output(simplified)

        self.assertIn("Agent response", plain)
        self.assertIn("Working\ngpt-5.6-sol max · ~/Workspace/project", plain)
        self.assertNotIn("Find and fix a bug", plain)

    def test_remote_herdr_arguments_are_shell_quoted(self):
        prompt = "hello; touch /tmp/should-not-run"
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(relay.subprocess, "run", return_value=completed) as run:
            relay.run_herdr_result("agent", "prompt", "w0:p1", prompt, remote="user@example")

        command = run.call_args.args[0]
        self.assertEqual(
            command[-1],
            shlex.join([
                relay.REMOTE_HERDR_BIN,
                "agent",
                "prompt",
                "w0:p1",
                prompt,
            ]),
        )
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
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout="\x1b[36mremote output\x1b[0m",
            stderr="",
        )

        with patch.object(relay, "run_herdr_result", return_value=completed) as run_herdr:
            await relay.handle_client(ws)

        run_herdr.assert_called_once_with(
            "pane",
            "read",
            "w0:p1",
            "--lines",
            "40",
            "--source",
            "recent-unwrapped",
            "--format",
            "ansi",
            remote=source,
        )
        self.assertIn(
            {
                "type": "pane_content",
                "pane_id": pane_id,
                "content": "remote output",
                "ansi_content": "\x1b[36mremote output\x1b[0m",
            },
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

    def test_workspace_file_browser_reads_code_markdown_and_html_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            outside = Path(temp_dir) / "outside.py"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Project\n\nHello **world**.\n")
            (project / "app.py").write_text("print('hello')\n")
            (project / "guide.html").write_text(
                "<!doctype html><title>Guide</title><h1>Reference</h1>"
                "<script>window.top.location = 'https://example.com'</script>\n"
            )
            (project / "image.bin").write_bytes(b"\x00\x01\x02")
            (project / ".env").write_text("SECRET=value\n")
            hidden = project / ".private"
            hidden.mkdir()
            (hidden / "notes.md").write_text("secret\n")
            outside.write_text("outside\n")
            (project / "linked.py").symlink_to(outside)

            with patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]):
                listing = relay.workspace_file_listing(str(project))
                entries = {entry["name"]: entry for entry in listing["entries"]}

                self.assertIn("README.md", entries)
                self.assertIn("app.py", entries)
                self.assertIn("image.bin", entries)
                self.assertNotIn(".env", entries)
                self.assertNotIn(".private", entries)
                self.assertNotIn("linked.py", entries)
                self.assertTrue(entries["README.md"]["previewable"])
                self.assertEqual(entries["README.md"]["preview_kind"], "markdown")
                self.assertEqual(entries["app.py"]["language"], "python")
                self.assertTrue(entries["guide.html"]["previewable"])
                self.assertEqual(entries["guide.html"]["preview_kind"], "html")
                self.assertEqual(entries["guide.html"]["language"], "xml")
                self.assertFalse(entries["image.bin"]["previewable"])
                self.assertTrue(entries["image.bin"]["downloadable"])

                markdown = relay.workspace_file_read(str(project / "README.md"))
                code = relay.workspace_file_read(str(project / "app.py"))
                html = relay.workspace_file_read(str(project / "guide.html"))
                self.assertEqual(markdown["kind"], "markdown")
                self.assertIn("Hello **world**", markdown["content"])
                self.assertEqual(code["language"], "python")
                self.assertEqual(code["line_count"], 2)
                self.assertEqual(html["kind"], "html")
                self.assertEqual(html["language"], "xml")
                self.assertIn("<h1>Reference</h1>", html["content"])

                with self.assertRaisesRegex(ValueError, "limited to code, Markdown, and HTML"):
                    relay.workspace_file_read(str(project / "image.bin"))
                with self.assertRaisesRegex(ValueError, "Hidden workspace"):
                    relay.workspace_file_read(str(project / ".private" / "notes.md"))
                with self.assertRaisesRegex(ValueError, "symbolic links"):
                    relay.workspace_file_read(str(project / "linked.py"))
                with self.assertRaisesRegex(ValueError, "outside the configured roots"):
                    relay.workspace_file_read(str(outside))

    def test_workspace_file_preview_rejects_binary_and_oversized_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            root.mkdir()
            binary_code = root / "binary.py"
            binary_code.write_bytes(b"x\x00y")
            large_code = root / "large.py"
            large_code.write_text("x" * 32)

            with (
                patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]),
                patch.object(relay, "WORKSPACE_FILE_PREVIEW_MAX_BYTES", 16),
            ):
                with self.assertRaisesRegex(ValueError, "Binary files"):
                    relay.workspace_file_read(str(binary_code))
                with self.assertRaisesRegex(ValueError, "too large"):
                    relay.workspace_file_read(str(large_code))

    def test_local_workspace_upload_is_atomic_and_handles_name_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            project.mkdir(parents=True)
            existing = project / "report.txt"
            existing.write_bytes(b"old")
            source = relay.local_agent_source()

            with patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]):
                payload = b"new report"
                upload = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "report.txt",
                    len(payload),
                    False,
                )
                staging_path = upload.staging_path
                self.assertEqual(upload.write(0, payload[:4]), 4)
                self.assertEqual(upload.write(4, payload[4:]), len(payload))
                result = upload.finish()

                self.assertEqual(result["name"], "report (1).txt")
                self.assertEqual((project / result["name"]).read_bytes(), payload)
                self.assertEqual(existing.read_bytes(), b"old")
                self.assertFalse(staging_path.exists())

                replacement = b"replacement"
                overwrite = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "report.txt",
                    len(replacement),
                    True,
                )
                overwrite.write(0, replacement)
                overwritten = overwrite.finish()
                self.assertEqual(overwritten["name"], "report.txt")
                self.assertEqual(existing.read_bytes(), replacement)

                empty = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "empty.bin",
                    0,
                    False,
                )
                empty.finish()
                self.assertEqual((project / "empty.bin").read_bytes(), b"")

    def test_workspace_upload_rejects_invalid_input_and_cleans_staging_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            project.mkdir(parents=True)
            source = relay.local_agent_source()

            self.assertEqual(
                relay.validated_workspace_upload_name(" report .txt "),
                " report .txt ",
            )
            for name in ("", "   ", ".hidden", "..", "../escape", "dir/file", "dir\\file", "bad\x00name"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    relay.validated_workspace_upload_name(name)

            with (
                patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]),
                patch.object(relay, "WORKSPACE_UPLOAD_MAX_BYTES", 8),
            ):
                for size in (-1, 9, True, "4"):
                    with self.subTest(size=size), self.assertRaisesRegex(ValueError, "file size"):
                        relay.WorkspaceUpload(
                            source,
                            str(project),
                            "invalid.bin",
                            size,
                            False,
                        )

                cancelled = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "cancelled.bin",
                    4,
                    False,
                )
                cancelled_path = cancelled.staging_path
                with self.assertRaisesRegex(ValueError, "offset"):
                    cancelled.write(1, b"ab")
                cancelled.cancel()
                self.assertTrue(cancelled.stream.closed)
                self.assertFalse(cancelled_path.exists())

                oversized_chunk = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "oversized.bin",
                    1,
                    False,
                )
                with self.assertRaisesRegex(ValueError, "declared size"):
                    oversized_chunk.write(0, b"ab")
                oversized_chunk.cancel()

                with patch.object(relay, "WORKSPACE_UPLOAD_CHUNK_BYTES", 1):
                    bounded_chunk = relay.WorkspaceUpload(
                        source,
                        str(project),
                        "bounded.bin",
                        2,
                        False,
                    )
                    with self.assertRaisesRegex(ValueError, "chunk is invalid"):
                        bounded_chunk.write(0, b"ab")
                    bounded_chunk.cancel()

                incomplete = relay.WorkspaceUpload(
                    source,
                    str(project),
                    "incomplete.bin",
                    4,
                    False,
                )
                incomplete_path = incomplete.staging_path
                incomplete.write(0, b"ab")
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    incomplete.finish()
                self.assertTrue(incomplete.stream.closed)
                self.assertFalse(incomplete_path.exists())
                self.assertFalse((project / "incomplete.bin").exists())

    def test_remote_workspace_upload_streams_structured_ssh_arguments(self):
        source = relay.normalize_ssh_profile({
            "id": "files-only",
            "label": "Files Server",
            "target": "builder@build-host",
            "port": 2222,
            "agent_enabled": False,
            "workspace_roots": ["~/Workspace", "/srv/models"],
        })
        payload = b"remote payload"
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["payload"] = kwargs["stdin"].read()
            response = {
                "path": "/home/builder/Workspace/project/name;safe.txt",
                "display_path": "~/Workspace/project/name;safe.txt",
                "name": "name;safe.txt",
                "size": len(payload),
            }
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(response).encode(),
                stderr=b"",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir) / "upload.bin"
            staging.write_bytes(payload)
            with patch.object(relay.subprocess, "run", side_effect=fake_run):
                result = relay.remote_workspace_upload_file(
                    source,
                    staging,
                    "/home/builder/Workspace/project",
                    "name;safe.txt",
                    len(payload),
                    False,
                )

        command = captured["command"]
        self.assertEqual(captured["payload"], payload)
        self.assertEqual(command[-2], "builder@build-host")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("2222", command)
        remote_arguments = shlex.split(command[-1])
        self.assertEqual(remote_arguments[:2], ["python3", "-c"])
        self.assertEqual(json.loads(remote_arguments[3]), ["~/Workspace", "/srv/models"])
        self.assertEqual(remote_arguments[4], "/home/builder/Workspace/project")
        self.assertEqual(remote_arguments[5], "name;safe.txt")
        self.assertEqual(remote_arguments[6:], ["0", str(len(payload))])
        self.assertEqual(result["name"], "name;safe.txt")

    def test_remote_workspace_upload_script_revalidates_roots_and_commits_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            outside = Path(temp_dir) / "outside"
            project.mkdir(parents=True)
            outside.mkdir()
            (project / "model.bin").write_bytes(b"old")
            linked = root / "linked"
            linked.symlink_to(outside, target_is_directory=True)
            payload = b"remote model"
            command = [
                sys.executable,
                "-c",
                relay.REMOTE_WORKSPACE_UPLOAD_SCRIPT,
                json.dumps([str(root)]),
                str(project),
                "model.bin",
                "0",
                str(len(payload)),
            ]

            completed = subprocess.run(
                command,
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            result = json.loads(completed.stdout)
            self.assertEqual(result["name"], "model (1).bin")
            self.assertEqual((project / result["name"]).read_bytes(), payload)
            self.assertEqual((project / "model.bin").read_bytes(), b"old")
            self.assertEqual(list(project.glob(".herdr-upload-*")), [])

            rejected = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    relay.REMOTE_WORKSPACE_UPLOAD_SCRIPT,
                    json.dumps([str(root)]),
                    str(linked),
                    "escape.bin",
                    "0",
                    "1",
                ],
                input=b"x",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("symbolic links", json.loads(rejected.stdout)["error"])
            self.assertFalse((outside / "escape.bin").exists())

    def test_ssh_only_profile_is_a_files_source_only_for_terminal_authorized_scope(self):
        source = relay.normalize_ssh_profile({
            "id": "files-only",
            "label": "Files Server",
            "target": "builder@build-host",
            "agent_enabled": False,
            "workspace_roots": ["~/Workspace"],
        })
        remote_listing = {
            "path": "/home/builder/Workspace",
            "display_path": "~/Workspace",
            "parent": "",
            "entries": [],
            "truncated": False,
        }
        with (
            patch.object(relay, "agent_source", side_effect=ValueError("not an Agent source")),
            patch.object(relay, "terminal_profile", return_value=source) as terminal_profile,
            patch.object(
                relay,
                "remote_workspace_file_listing",
                return_value=remote_listing,
            ) as remote_listing_request,
        ):
            with self.assertRaises(ValueError):
                relay.workspace_file_listing_for_source(
                    source["id"],
                    remote_listing["path"],
                    False,
                )
            listing = relay.workspace_file_listing_for_source(
                source["id"],
                remote_listing["path"],
                True,
            )

        terminal_profile.assert_called_once_with(source["id"])
        remote_listing_request.assert_called_once_with(source, remote_listing["path"])
        self.assertEqual(listing["source_id"], source["id"])
        self.assertEqual(listing["source_label"], source["label"])

    async def test_authorized_workspace_upload_websocket_supports_cancel_and_finish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            project.mkdir(parents=True)
            payload = b"uploaded over websocket"
            ws = FakeWebSocket([
                {
                    "type": "workspace_upload_start",
                    "upload_id": 7,
                    "source_id": "local",
                    "directory": str(project),
                    "name": "cancelled.bin",
                    "size": 4,
                    "overwrite": False,
                },
                {
                    "type": "workspace_upload_chunk",
                    "upload_id": 7,
                    "offset": 0,
                    "data": base64.b64encode(b"ab").decode(),
                },
                {"type": "workspace_upload_cancel", "upload_id": 7},
                {
                    "type": "workspace_upload_start",
                    "upload_id": 8,
                    "source_id": "local",
                    "directory": str(project),
                    "name": "finished.bin",
                    "size": len(payload),
                    "overwrite": False,
                },
                {
                    "type": "workspace_upload_chunk",
                    "upload_id": 8,
                    "offset": 0,
                    "data": base64.b64encode(payload[:8]).decode(),
                },
                {
                    "type": "workspace_upload_chunk",
                    "upload_id": 8,
                    "offset": 8,
                    "data": base64.b64encode(payload[8:]).decode(),
                },
                {"type": "workspace_upload_finish", "upload_id": 8},
            ])
            relay.client_auth[id(ws)] = {
                "mode": "tailscale",
                "login": "owner@example.com",
                "name": "Owner",
            }
            local_source = relay.local_agent_source()

            with (
                patch.object(relay, "WEB_TERMINAL_ENABLED", True),
                patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
                patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]),
                patch.object(relay, "configured_agent_sources", return_value=[local_source]),
                patch.object(relay, "configured_terminal_profiles", return_value=[]),
                patch.object(relay, "machine_access_info", return_value={}),
                patch.object(relay, "audit") as audit,
            ):
                await relay.handle_client(ws)

            session = next(message for message in ws.sent if message["type"] == "session")
            self.assertTrue(session["features"]["workspace_upload"])
            self.assertIn(
                {"type": "workspace_upload_cancelled", "upload_id": 7},
                ws.sent,
            )
            completed = next(
                message
                for message in ws.sent
                if message["type"] == "workspace_upload_complete"
            )
            self.assertEqual(completed["upload_id"], 8)
            self.assertEqual(completed["name"], "finished.bin")
            self.assertEqual((project / "finished.bin").read_bytes(), payload)
            self.assertFalse((project / "cancelled.bin").exists())
            self.assertEqual(list(project.glob(".herdr-upload-*")), [])
            upload_audits = [
                call for call in audit.call_args_list if call.args[0] == "workspace_upload"
            ]
            self.assertEqual(len(upload_audits), 1)
            self.assertIn("name='finished.bin'", upload_audits[0].args[4])

    async def test_token_client_cannot_upload_workspace_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            project = root / "project"
            project.mkdir(parents=True)
            ws = FakeWebSocket([{
                "type": "workspace_upload_start",
                "upload_id": 9,
                "source_id": "local",
                "directory": str(project),
                "name": "denied.bin",
                "size": 0,
                "overwrite": False,
            }])
            relay.client_auth[id(ws)] = {"mode": "token", "login": "", "name": ""}

            with (
                patch.object(relay, "WEB_TERMINAL_ENABLED", True),
                patch.object(relay, "TERMINAL_ALLOWED_USERS", {"owner@example.com"}),
                patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]),
                patch.object(relay, "configured_terminal_profiles", return_value=[]),
                patch.object(relay, "machine_access_info", return_value={}),
            ):
                await relay.handle_client(ws)

            session = next(message for message in ws.sent if message["type"] == "session")
            self.assertFalse(session["features"]["workspace_upload"])
            self.assertIn(
                {
                    "type": "workspace_upload_error",
                    "upload_id": 9,
                    "message": "Workspace upload requires authorized Remote Shell access",
                },
                ws.sent,
            )
            self.assertFalse((project / "denied.bin").exists())

    def test_remote_workspace_file_requests_keep_paths_in_structured_arguments(self):
        source = relay.normalize_ssh_profile({
            "id": "build",
            "label": "Build Server",
            "target": "build-host",
            "agent_enabled": True,
            "workspace_roots": ["~/Workspace", "/srv/models"],
        })
        requested = "/home/dev/Workspace/project/a file.py; touch /tmp/nope"
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps({
                "path": "/home/dev/Workspace/project",
                "display_path": "~/Workspace/project",
                "parent": "/home/dev/Workspace",
                "entries": [{
                    "name": "app.py",
                    "path": "/home/dev/Workspace/project/app.py",
                    "display_path": "~/Workspace/project/app.py",
                    "kind": "file",
                    "size": 12,
                    "is_repo": False,
                }],
                "truncated": False,
            }),
            stderr="",
        )
        with patch.object(relay, "run_remote_result", return_value=completed) as run_remote:
            listing = relay.remote_workspace_file_listing(source, requested)

        arguments = run_remote.call_args.args[1]
        self.assertEqual(arguments[0:2], ["python3", "-c"])
        self.assertEqual(json.loads(arguments[3]), ["~/Workspace", "/srv/models"])
        self.assertEqual(arguments[4], "list")
        self.assertEqual(arguments[5], requested)
        self.assertEqual(arguments[6], "0")
        self.assertTrue(listing["entries"][0]["previewable"])
        self.assertEqual(listing["entries"][0]["language"], "python")

    async def test_workspace_file_websocket_protocol_preserves_request_scope(self):
        path = "/workspace/project/README.md"
        listing = {
            "type": "workspace_listing",
            "source_id": "local",
            "source_label": "本机",
            "path": "/workspace/project",
            "display_path": "~/Workspace/project",
            "parent": "/workspace",
            "entries": [],
            "truncated": False,
        }
        workspace_file = {
            "type": "workspace_file",
            "source_id": "local",
            "source_label": "本机",
            "path": path,
            "display_path": "~/Workspace/project/README.md",
            "name": "README.md",
            "size": 10,
            "kind": "markdown",
            "language": "markdown",
            "content": "# Project\n",
            "line_count": 2,
        }
        metadata = {
            "source_id": "local",
            "source_label": "本机",
            "path": path,
            "display_path": "~/Workspace/project/README.md",
            "name": "README.md",
            "size": 10,
        }
        ws = FakeWebSocket([
            {
                "type": "list_workspace_files",
                "source_id": "local",
                "path": "/workspace/project",
                "request_id": 11,
            },
            {
                "type": "read_workspace_file",
                "source_id": "local",
                "path": path,
                "request_id": 12,
            },
            {
                "type": "prepare_workspace_download",
                "source_id": "local",
                "path": path,
                "request_id": 13,
            },
        ])
        relay.client_auth[id(ws)] = {"mode": "token", "login": "", "name": ""}

        with (
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(side_effect=[listing, workspace_file, metadata]),
            ) as to_thread,
            patch.object(relay, "machine_access_info", return_value={}),
        ):
            await relay.handle_client(ws)

        self.assertEqual(
            to_thread.await_args_list,
            [
                unittest.mock.call(
                    relay.workspace_file_listing_for_source,
                    "local",
                    "/workspace/project",
                    False,
                ),
                unittest.mock.call(
                    relay.workspace_file_read_for_source,
                    "local",
                    path,
                    False,
                ),
                unittest.mock.call(
                    relay.workspace_file_metadata_for_source,
                    "local",
                    path,
                    False,
                ),
            ],
        )
        self.assertIn({**listing, "request_id": 11}, ws.sent)
        self.assertIn({**workspace_file, "request_id": 12}, ws.sent)
        prepared = next(message for message in ws.sent if message["type"] == "workspace_download_ready")
        self.assertEqual(prepared["request_id"], 13)
        self.assertEqual(prepared["source_id"], "local")
        self.assertTrue(prepared["url"].startswith("/api/workspace-download?token="))

    def test_workspace_download_tokens_are_short_lived_one_time_and_identity_bound(self):
        metadata = {
            "source_id": "local",
            "source_label": "本机",
            "path": "/workspace/project/readme.md",
            "name": "readme.md",
            "size": 12,
        }
        owner = {"mode": "tailscale", "login": "Owner@Example.com"}
        other = {"mode": "tailscale", "login": "other@example.com"}
        prepared = relay.create_workspace_download(metadata, owner, now=1000)
        token = prepared["token"]

        self.assertIsNone(relay.consume_workspace_download(token, other, now=1001))
        grant = relay.consume_workspace_download(token, owner, now=1001)
        self.assertEqual(grant["path"], metadata["path"])
        self.assertIsNone(relay.consume_workspace_download(token, owner, now=1001))

        expired = relay.create_workspace_download(metadata, owner, now=2000)["token"]
        self.assertIsNone(
            relay.consume_workspace_download(
                expired,
                owner,
                now=2000 + relay.WORKSPACE_DOWNLOAD_TOKEN_TTL_SECONDS + 1,
            )
        )

        session_token = relay.create_workspace_download(
            metadata,
            {"mode": "token-query"},
            now=3000,
        )["token"]
        self.assertIsNotNone(
            relay.consume_workspace_download(
                session_token,
                {"mode": "web-session"},
                now=3001,
            )
        )

    async def test_workspace_download_http_route_consumes_token_once(self):
        data = b"# Project\n"
        metadata = {
            "source_id": "local",
            "source_label": "本机",
            "path": "/workspace/project/\u9879\u76ee.md",
            "name": "\u9879\u76ee.md",
            "size": len(data),
        }
        auth = {"mode": "token", "login": "", "name": ""}
        prepared = relay.create_workspace_download(metadata, auth)
        request = SimpleNamespace(
            path=prepared["url"],
            headers={
                "Authorization": f"Bearer {relay.AUTH_TOKEN}",
                "Host": "127.0.0.1:8375",
                "User-Agent": "test-browser",
            },
        )
        connection = SimpleNamespace(remote_address=("127.0.0.1", 43123))

        with (
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value=(metadata, data)),
            ) as to_thread,
            patch.object(relay, "audit") as audit,
        ):
            response = await relay.process_request(connection, request)
            replay = await relay.process_request(connection, request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, data)
        self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])
        self.assertEqual(replay.status_code, 404)
        to_thread.assert_awaited_once_with(
            relay.workspace_file_download_for_source,
            "local",
            metadata["path"],
        )
        audit.assert_called_once()

    async def test_workspace_download_http_route_preserves_terminal_profile_scope(self):
        data = b"remote file\n"
        metadata = {
            "source_id": "files-only",
            "source_label": "Files Server",
            "path": "/home/builder/Workspace/project/remote.txt",
            "name": "remote.txt",
            "size": len(data),
            "include_terminal_profiles": True,
        }
        auth = {"mode": "token", "login": "", "name": ""}
        prepared = relay.create_workspace_download(metadata, auth)
        request = SimpleNamespace(
            path=prepared["url"],
            headers={
                "Authorization": f"Bearer {relay.AUTH_TOKEN}",
                "Host": "127.0.0.1:8375",
                "User-Agent": "test-browser",
            },
        )
        connection = SimpleNamespace(remote_address=("127.0.0.1", 43123))

        with (
            patch.object(
                relay.asyncio,
                "to_thread",
                new=AsyncMock(return_value=(metadata, data)),
            ) as to_thread,
            patch.object(relay, "audit"),
        ):
            response = await relay.process_request(connection, request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, data)
        to_thread.assert_awaited_once_with(
            relay.workspace_file_download_for_source,
            "files-only",
            metadata["path"],
            True,
        )

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
            prompt = "use private-token-456 to inspect the project " + ("x" * 1500)
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
