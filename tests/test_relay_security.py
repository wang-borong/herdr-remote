#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=14.0"]
# ///
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
        relay.clients.clear()
        relay.agent_cache.clear()
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

    async def test_agent_prompt_uses_herdr_api_and_redacts_audit_content(self):
        pane_id = "w0:p1"
        prompt = "use secret-token-123 to run tests"
        relay.known_panes.add(pane_id)
        relay.pane_remote_map[pane_id] = None
        ws = FakeWebSocket([{"type": "agent_prompt", "pane_id": pane_id, "text": prompt}])
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with (
            patch.object(relay, "run_herdr_result", return_value=result) as run_herdr,
            patch.object(relay, "audit") as audit,
        ):
            await relay.handle_client(ws)

        run_herdr.assert_called_once_with("agent", "prompt", pane_id, prompt, remote=None)
        self.assertIn(
            {"type": "command_result", "command": "agent_prompt", "ok": True},
            ws.sent,
        )
        detail = audit.call_args.args[4]
        self.assertIn(f"chars={len(prompt)}", detail)
        self.assertNotIn("secret-token-123", detail)

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

    def test_workspace_browsing_is_confined_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "Workspace"
            outside = Path(temp_dir) / "outside"
            repo = root / "repo"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)

            with patch.object(relay, "WORKSPACE_ROOTS", [root.resolve()]):
                listing = relay.workspace_directory_listing(str(root))
                repo_entry = next(entry for entry in listing["entries"] if entry["name"] == "repo")
                self.assertTrue(repo_entry["is_repo"])
                self.assertNotIn("escape", [entry["name"] for entry in listing["entries"]])
                self.assertTrue(relay.workspace_directory_listing(str(repo))["can_start_agent"])
                with self.assertRaisesRegex(ValueError, "outside the configured roots"):
                    relay.resolve_workspace_path(str(outside))

    def test_codex_start_uses_structured_herdr_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
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
            started = subprocess.CompletedProcess(
                [], 0,
                stdout=json.dumps({
                    "result": {
                        "agent": {"pane_id": "w9:p1", "agent_status": "idle"},
                    }
                }),
                stderr="",
            )
            prompted = subprocess.CompletedProcess([], 0, stdout="", stderr="")

            with (
                patch.object(relay, "WORKSPACE_ROOTS", [repo.parent.resolve()]),
                patch.object(relay, "run_herdr_result", side_effect=[created, started, prompted]) as run_herdr,
            ):
                result = relay.start_local_codex(str(repo), "inspect; do not run a shell")

            self.assertEqual(result["pane_id"], "w9:p1")
            self.assertTrue(result["prompted"])
            self.assertEqual(
                run_herdr.call_args_list[0],
                unittest.mock.call(
                    "workspace", "create", "--cwd", str(repo), "--label", "repo", "--no-focus",
                    timeout=20,
                ),
            )
            self.assertEqual(
                run_herdr.call_args_list[1],
                unittest.mock.call(
                    "agent", "start", "codex", "--kind", "codex", "--pane", "w9:p1",
                    "--timeout", "60000", timeout=75,
                ),
            )
            self.assertEqual(
                run_herdr.call_args_list[2],
                unittest.mock.call(
                    "agent", "prompt", "w9:p1", "inspect; do not run a shell", timeout=15,
                ),
            )

    async def test_start_agent_message_redacts_prompt_and_broadcasts_new_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
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
                patch.object(relay, "start_local_codex", return_value=started_agent) as start_codex,
                patch.object(relay, "broadcast", new=AsyncMock()) as broadcast,
                patch.object(relay, "audit") as audit,
            ):
                await relay.handle_client(ws)

            start_codex.assert_called_once_with(str(repo.resolve()), prompt)
            self.assertIn({"type": "agent_started", "ok": True, "agent": started_agent}, ws.sent)
            self.assertEqual(broadcast.await_args.args[0]["agent"]["pane_id"], "w9:p1")
            self.assertIn(f"chars={len(prompt)}", audit.call_args.args[4])
            self.assertNotIn("private-token-456", audit.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
