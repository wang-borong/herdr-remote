import asyncio
import base64
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "relay"))

from terminal_sessions import (
    TerminalConfigError,
    TerminalSession,
    capture_tmux_pane,
    configure_tmux_server,
    delete_ssh_profile,
    load_ssh_profiles,
    normalize_ssh_profile,
    persistent_session_name,
    save_ssh_profile,
    terminal_environment,
    terminal_profile_command,
    terminate_persistent_session,
    tmux_server_configuration_command,
    validated_terminal_dimensions,
)


class TerminalProfileTests(unittest.TestCase):
    def test_profiles_are_validated_and_saved_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "config" / "ssh-hosts.json"
            saved = save_ssh_profile(config_file, {
                "label": "Build Server",
                "target": "builder@192.168.50.20",
                "port": 2222,
                "description": "LAN build machine",
                "color": "green",
            })

            self.assertEqual(saved["id"], "build-server")
            self.assertFalse(saved["agent_enabled"])
            self.assertEqual(saved["herdr_bin"], "herdr")
            self.assertEqual(saved["workspace_roots"], ["~/Workspace"])
            self.assertEqual(load_ssh_profiles(config_file), [saved])
            self.assertEqual(stat.S_IMODE(config_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config_file.parent.stat().st_mode), 0o700)

            delete_ssh_profile(config_file, saved["id"])
            self.assertEqual(load_ssh_profiles(config_file), [])

    def test_profile_rejects_option_injection_and_invalid_ports(self):
        with self.assertRaisesRegex(TerminalConfigError, "SSH target"):
            normalize_ssh_profile({"label": "Unsafe", "target": "-oProxyCommand=bad"})
        with self.assertRaisesRegex(TerminalConfigError, "SSH port"):
            normalize_ssh_profile({"label": "Server", "target": "server", "port": 70000})
        with self.assertRaisesRegex(TerminalConfigError, "Profile id"):
            normalize_ssh_profile({"id": "local", "label": "Server", "target": "server"})
        with self.assertRaisesRegex(TerminalConfigError, "herdr executable"):
            normalize_ssh_profile({
                "label": "Server",
                "target": "server",
                "agent_enabled": True,
                "herdr_bin": "herdr; touch /tmp/unsafe",
            })
        with self.assertRaisesRegex(TerminalConfigError, "workspace root"):
            normalize_ssh_profile({
                "label": "Server",
                "target": "server",
                "agent_enabled": True,
                "workspace_roots": ["relative/path"],
            })

    def test_profile_keeps_agent_discovery_configuration(self):
        profile = normalize_ssh_profile({
            "id": "gpu",
            "label": "GPU Server",
            "target": "gpu-host",
            "agent_enabled": True,
            "herdr_bin": "/home/dev/.local/bin/herdr",
            "workspace_roots": ["~/Workspace", "/srv/models", "~/Workspace"],
        })

        self.assertTrue(profile["agent_enabled"])
        self.assertEqual(profile["herdr_bin"], "/home/dev/.local/bin/herdr")
        self.assertEqual(profile["workspace_roots"], ["~/Workspace", "/srv/models"])

    def test_tmux_commands_use_a_dedicated_persistent_socket(self):
        profile = normalize_ssh_profile({
            "id": "build",
            "label": "Build",
            "target": "builder@10.10.0.5",
            "port": 2222,
        })
        command, cwd, persistent = terminal_profile_command(
            profile,
            shell_binary="/bin/zsh",
            ssh_binary="/usr/bin/ssh",
            tmux_binary="/usr/bin/tmux",
            cwd=Path("/tmp"),
        )

        self.assertTrue(persistent)
        self.assertEqual(command[:4], ["/usr/bin/tmux", "-L", "herdr-web", "new-session"])
        self.assertEqual(command[4:6], ["-A", "-D"])
        session_name = command[command.index("-s") + 1]
        self.assertTrue(session_name.startswith("herdr-ssh-build-"))
        self.assertIn("/usr/bin/ssh -tt", command[-1])
        self.assertIn("-p 2222", command[-1])
        self.assertEqual(cwd, Path("/tmp"))

    def test_terminal_command_can_bypass_broken_system_ssh_config(self):
        profile = normalize_ssh_profile({
            "id": "build",
            "label": "Build",
            "target": "build-host",
        })
        command, _, persistent = terminal_profile_command(
            profile,
            shell_binary="/bin/zsh",
            ssh_binary="/usr/bin/ssh",
            ssh_config_file=Path("/home/dev/.ssh/config"),
            tmux_binary=None,
            cwd=Path("/tmp"),
        )

        self.assertFalse(persistent)
        self.assertEqual(command[:3], ["/usr/bin/ssh", "-F", "/home/dev/.ssh/config"])

    def test_web_tmux_configuration_enables_mouse_and_scoped_prefix_bindings(self):
        command = tmux_server_configuration_command("/usr/bin/tmux")
        commands = []
        current_command = []
        for argument in command[3:]:
            if argument == ";":
                commands.append(current_command)
                current_command = []
            else:
                current_command.append(argument)
        commands.append(current_command)

        self.assertEqual(command[:4], ["/usr/bin/tmux", "-L", "herdr-web", "set-option"])
        self.assertIn(["set-option", "-g", "mouse", "on"], commands)
        self.assertIn(["set-option", "-g", "prefix", "C-b"], commands)
        self.assertIn(["set-option", "-g", "prefix2", "None"], commands)
        self.assertIn(
            ["set-window-option", "-g", "window-size", "latest"],
            commands,
        )
        self.assertIn(["bind-key", "-T", "prefix", "C-b", "send-prefix"], commands)
        self.assertIn(["bind-key", "-T", "prefix", "p", "previous-window"], commands)
        self.assertIn(["bind-key", "-T", "prefix", "n", "next-window"], commands)

        with patch("terminal_sessions.subprocess.run") as run:
            run.return_value.returncode = 0
            configure_tmux_server("/usr/bin/tmux")

        run.assert_called_once_with(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )

    def test_web_tmux_configuration_retries_until_the_server_is_ready(self):
        failed = subprocess.CompletedProcess([], 1)
        configured = subprocess.CompletedProcess([], 0)
        with (
            patch("terminal_sessions.subprocess.run", side_effect=[failed, configured]) as run,
            patch("terminal_sessions.time.sleep") as sleep,
        ):
            configure_tmux_server("/usr/bin/tmux")

        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(0.05)

    def test_persistent_ssh_session_tracks_endpoint_and_can_be_terminated(self):
        profile = normalize_ssh_profile({
            "id": "build",
            "label": "Build",
            "target": "builder@10.10.0.5",
            "port": 22,
        })
        moved_profile = {**profile, "target": "builder@10.10.0.6"}

        self.assertEqual(persistent_session_name({"kind": "local"}), "herdr-local")
        self.assertNotEqual(
            persistent_session_name(profile),
            persistent_session_name(moved_profile),
        )

        with patch("terminal_sessions.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(terminate_persistent_session(profile, "/usr/bin/tmux"))

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/tmux", "-L", "herdr-web", "kill-session"])
        self.assertEqual(command[-1], persistent_session_name(profile))

    def test_tmux_capture_returns_recent_history_and_bounds_payload(self):
        profile = normalize_ssh_profile({
            "id": "build",
            "label": "Build",
            "target": "builder@10.10.0.5",
        })
        with patch("terminal_sessions.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                [],
                0,
                stdout=b"old line\nrecent line\nlatest line\n",
            )
            content, truncated = capture_tmux_pane(
                profile,
                "/usr/bin/tmux",
                maximum_bytes=25,
            )

        self.assertTrue(truncated)
        self.assertEqual(content, "recent line\nlatest line")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["/usr/bin/tmux", "-L", "herdr-web", "capture-pane"])
        self.assertIn("-J", command)
        self.assertIn("-5000", command)
        self.assertEqual(command[-1], persistent_session_name(profile))
        run.assert_called_once_with(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )

    def test_terminal_size_is_bounded_and_secret_environment_is_removed(self):
        self.assertEqual(validated_terminal_dimensions(9999, 1), (400, 5))
        with patch.dict(os.environ, {
            "HERDR_RELAY_TOKEN": "relay-secret",
            "HERDR_TG_TOKEN": "telegram-secret",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "VIRTUAL_ENV": "/tmp/relay-venv",
            "UV_RUN_RECURSION_DEPTH": "1",
            "PATH": "/tmp/relay-venv/bin:/usr/bin",
        }):
            environment = terminal_environment()
        self.assertNotIn("HERDR_RELAY_TOKEN", environment)
        self.assertNotIn("HERDR_TG_TOKEN", environment)
        self.assertEqual(environment["SSH_AUTH_SOCK"], "/tmp/agent.sock")
        self.assertNotIn("VIRTUAL_ENV", environment)
        self.assertNotIn("UV_RUN_RECURSION_DEPTH", environment)
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["TERM"], "xterm-256color")


class TerminalSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_pty_streams_output_and_accepts_input(self):
        events = []
        finished = asyncio.Event()

        async def event_handler(event):
            events.append(event)
            if event["type"] == "terminal_exit":
                finished.set()

        with tempfile.TemporaryDirectory() as temporary:
            session = TerminalSession(
                {
                    "id": "local",
                    "kind": "local",
                    "label": "Local",
                    "target": "localhost",
                    "port": 0,
                    "description": "",
                    "color": "violet",
                },
                event_handler,
                shell_binary="/bin/sh",
                ssh_binary="/usr/bin/ssh",
                tmux_binary=None,
                cwd=Path(temporary),
                cols=80,
                rows=24,
            )
            await session.spawn()
            session.start_reader()
            await session.write(b"printf '__HERDR_PTY_OK__\\n'; exit\n")
            await asyncio.wait_for(finished.wait(), timeout=5)
            await session.close()

        encoded_output = "".join(
            event["data"] for event in events if event["type"] == "terminal_output"
        )
        self.assertTrue(encoded_output)
        output = b"".join(
            base64.b64decode(event["data"])
            for event in events
            if event["type"] == "terminal_output"
        )
        self.assertIn(b"__HERDR_PTY_OK__", output)

    async def test_pty_retries_partial_writes(self):
        events = []
        finished = asyncio.Event()

        async def event_handler(event):
            events.append(event)
            if event["type"] == "terminal_exit":
                finished.set()

        with tempfile.TemporaryDirectory() as temporary:
            session = TerminalSession(
                {
                    "id": "local",
                    "kind": "local",
                    "label": "Local",
                    "target": "localhost",
                    "port": 0,
                    "description": "",
                    "color": "violet",
                },
                event_handler,
                shell_binary="/bin/sh",
                ssh_binary="/usr/bin/ssh",
                tmux_binary=None,
                cwd=Path(temporary),
                cols=80,
                rows=24,
            )
            await session.spawn()
            session.start_reader()
            original_write = os.write

            def short_write(descriptor, data):
                return original_write(descriptor, data[:7])

            with patch("terminal_sessions.os.write", side_effect=short_write) as mocked_write:
                await session.write(b"printf '__HERDR_PARTIAL_WRITE_OK__\\n'; exit\n")
            await asyncio.wait_for(finished.wait(), timeout=5)
            await session.close()

        output = b"".join(
            base64.b64decode(event["data"])
            for event in events
            if event["type"] == "terminal_output"
        )
        self.assertIn(b"__HERDR_PARTIAL_WRITE_OK__", output)
        self.assertGreater(mocked_write.call_count, 1)
        self.assertIsNone(session.master_fd)

    async def test_resize_notifies_shell_of_new_dimensions(self):
        output = bytearray()
        resized_output = bytearray()
        ready = asyncio.Event()
        resized = asyncio.Event()
        finished = asyncio.Event()
        collect_resized_output = False

        async def event_handler(event):
            if event["type"] == "terminal_output":
                chunk = base64.b64decode(event["data"])
                output.extend(chunk)
                if b"__HERDR_RESIZE_READY__" in output:
                    ready.set()
                if collect_resized_output:
                    resized_output.extend(chunk)
                    normalized = bytes(resized_output).replace(b"\r", b"")
                    if b"__HERDR_WINCH_SIGNAL__17 63\n" in normalized:
                        resized.set()
            elif event["type"] == "terminal_exit":
                finished.set()

        with tempfile.TemporaryDirectory() as temporary:
            session = TerminalSession(
                {
                    "id": "local",
                    "kind": "local",
                    "label": "Local",
                    "target": "localhost",
                    "port": 0,
                    "description": "",
                    "color": "violet",
                },
                event_handler,
                shell_binary="/bin/sh",
                ssh_binary="/usr/bin/ssh",
                tmux_binary=None,
                cwd=Path(temporary),
                cols=80,
                rows=24,
            )
            try:
                await session.spawn()
                session.start_reader()
                await session.write(
                    b"trap 'printf \"__HERDR_WINCH_%s__\" SIGNAL; stty size' WINCH; "
                    b"printf '__HERDR_RESIZE_%s__\\n' READY\n"
                )
                await asyncio.wait_for(ready.wait(), timeout=5)
                collect_resized_output = True
                self.assertEqual(await session.resize(63, 17), (63, 17))
                await asyncio.wait_for(resized.wait(), timeout=5)
                with (
                    patch("terminal_sessions._set_window_size") as set_window_size,
                    patch("terminal_sessions.os.killpg") as kill_process_group,
                ):
                    self.assertEqual(await session.resize(63, 17), (63, 17))
                set_window_size.assert_not_called()
                kill_process_group.assert_not_called()
                await session.write(b"exit\n")
                await asyncio.wait_for(finished.wait(), timeout=5)
            finally:
                await session.close()

        self.assertIn(
            b"__HERDR_WINCH_SIGNAL__17 63\n",
            bytes(resized_output).replace(b"\r", b""),
        )


if __name__ == "__main__":
    unittest.main()
