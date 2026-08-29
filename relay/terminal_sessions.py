"""Validated SSH profiles and PTY-backed terminal sessions for the web UI."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import struct
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

try:
    import fcntl
    import pty
    import termios
except ImportError:  # Windows supports relay clients, but not the Unix PTY terminal.
    fcntl = None
    pty = None
    termios = None

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9._@:%+\-\[\]]{1,255}$")
REMOTE_EXECUTABLE_RE = re.compile(r"^(?:/[A-Za-z0-9._+@%/:=\-]+|[A-Za-z0-9._+\-]+)$")
PROFILE_COLORS = {"violet", "cyan", "green", "amber", "rose"}
MAX_SSH_PROFILES = 32
MAX_TERMINAL_INPUT_BYTES = 16 * 1024
MAX_TERMINAL_CAPTURE_BYTES = 1024 * 1024
MAX_TERMINAL_CAPTURE_LINES = 5000
TMUX_SOCKET_NAME = "herdr-web"
TMUX_CONFIGURATION_ATTEMPTS = 40
TMUX_CONFIGURATION_RETRY_SECONDS = 0.05
TMUX_WEB_PREFIX = "C-b"
TMUX_WEB_BINDINGS = (
    ("C-b", "send-prefix"),
    ("c", "new-window", "-c", "#{pane_current_path}"),
    ("p", "previous-window"),
    ("n", "next-window"),
    ("w", "choose-tree", "-Zw"),
    ("|", "split-window", "-h", "-c", "#{pane_current_path}"),
    ("_", "split-window", "-v", "-c", "#{pane_current_path}"),
    ("z", "resize-pane", "-Z"),
)


class TerminalConfigError(ValueError):
    """Raised when a terminal or SSH profile configuration is invalid."""


def _clean_text(value, *, field: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise TerminalConfigError(f"{field} must be text")
    cleaned = "".join(character for character in value.strip() if ord(character) >= 32)
    if required and not cleaned:
        raise TerminalConfigError(f"{field} is required")
    if len(cleaned) > maximum:
        raise TerminalConfigError(f"{field} is too long")
    return cleaned


def _clean_bool(value, *, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise TerminalConfigError(f"{field} must be true or false")


def _profile_slug(label: str, target: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")
    if not slug:
        slug = f"host-{hashlib.sha256(target.encode()).hexdigest()[:8]}"
    return slug[:32]


def normalize_ssh_profile(value: dict) -> dict:
    if not isinstance(value, dict):
        raise TerminalConfigError("SSH profile must be an object")

    label = _clean_text(value.get("label", ""), field="Profile label", maximum=64)
    target = _clean_text(value.get("target", ""), field="SSH target", maximum=255)
    if target.startswith("-") or not SSH_TARGET_RE.fullmatch(target):
        raise TerminalConfigError(
            "SSH target may only contain a user, hostname, IP address, or SSH config alias"
        )
    if target.count("@") > 1:
        raise TerminalConfigError("SSH target contains too many @ characters")

    raw_id = value.get("id")
    profile_id = _profile_slug(label, target) if raw_id in (None, "") else str(raw_id).casefold()
    if profile_id == "local" or not PROFILE_ID_RE.fullmatch(profile_id):
        raise TerminalConfigError("Profile id must use 1-32 lowercase letters, numbers, - or _")

    raw_port = value.get("port", 22)
    if isinstance(raw_port, bool):
        raise TerminalConfigError("SSH port must be an integer")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as error:
        raise TerminalConfigError("SSH port must be an integer") from error
    if not 1 <= port <= 65535:
        raise TerminalConfigError("SSH port must be between 1 and 65535")

    description = _clean_text(
        value.get("description", ""),
        field="Profile description",
        maximum=160,
        required=False,
    )
    color = str(value.get("color", "cyan")).casefold()
    if color not in PROFILE_COLORS:
        color = "cyan"

    agent_enabled = _clean_bool(
        value.get("agent_enabled"),
        field="Agent discovery",
        default=False,
    )
    herdr_bin = _clean_text(
        value.get("herdr_bin", "herdr"),
        field="Remote herdr executable",
        maximum=255,
    )
    if herdr_bin.startswith("-") or not REMOTE_EXECUTABLE_RE.fullmatch(herdr_bin):
        raise TerminalConfigError(
            "Remote herdr executable must be a command name or absolute path"
        )
    raw_workspace_roots = value.get("workspace_roots")
    if raw_workspace_roots is None:
        raw_workspace_roots = [value.get("workspace_root", "~/Workspace")]
    elif isinstance(raw_workspace_roots, str):
        raw_workspace_roots = raw_workspace_roots.splitlines()
    if not isinstance(raw_workspace_roots, list) or not 1 <= len(raw_workspace_roots) <= 8:
        raise TerminalConfigError("Remote workspace roots must contain 1-8 paths")
    workspace_roots = []
    for raw_workspace_root in raw_workspace_roots:
        workspace_root = _clean_text(
            raw_workspace_root,
            field="Remote workspace root",
            maximum=1024,
        )
        if not (
            workspace_root == "~"
            or workspace_root.startswith(("~/", "/"))
        ):
            raise TerminalConfigError(
                "Remote workspace root must be absolute or start with ~/"
            )
        if workspace_root not in workspace_roots:
            workspace_roots.append(workspace_root)

    return {
        "id": profile_id,
        "kind": "ssh",
        "label": label,
        "target": target,
        "port": port,
        "description": description,
        "color": color,
        "agent_enabled": agent_enabled,
        "herdr_bin": herdr_bin,
        "workspace_root": workspace_roots[0],
        "workspace_roots": workspace_roots,
    }


def local_terminal_profile(hostname: str) -> dict:
    return {
        "id": "local",
        "kind": "local",
        "label": "本机终端",
        "target": hostname,
        "port": 0,
        "description": "完整 Shell · tmux 持久会话",
        "color": "violet",
    }


def _read_profile_document(config_file: Path) -> dict:
    if not config_file.exists():
        return {"version": 1, "hosts": []}
    try:
        document = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TerminalConfigError(f"Could not read SSH profile file: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("hosts", []), list):
        raise TerminalConfigError("SSH profile file must contain a hosts array")
    return document


def load_ssh_profiles(config_file: Path) -> list[dict]:
    document = _read_profile_document(config_file)
    hosts = document.get("hosts", [])
    if len(hosts) > MAX_SSH_PROFILES:
        raise TerminalConfigError(f"At most {MAX_SSH_PROFILES} SSH profiles are allowed")

    profiles = []
    seen = set()
    for raw_profile in hosts:
        profile = normalize_ssh_profile(raw_profile)
        if profile["id"] in seen:
            raise TerminalConfigError(f"Duplicate SSH profile id: {profile['id']}")
        seen.add(profile["id"])
        profiles.append(profile)
    return profiles


def terminal_profiles(config_file: Path, hostname: str) -> list[dict]:
    return [local_terminal_profile(hostname), *load_ssh_profiles(config_file)]


def _write_profiles(config_file: Path, profiles: list[dict]) -> None:
    config_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(config_file.parent, 0o700)

    temporary = config_file.with_name(
        f".{config_file.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    payload = json.dumps(
        {"version": 1, "hosts": profiles},
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_file)
        os.chmod(config_file, 0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def save_ssh_profile(config_file: Path, value: dict) -> dict:
    profile = normalize_ssh_profile(value)
    profiles = load_ssh_profiles(config_file)
    for index, existing in enumerate(profiles):
        if existing["id"] == profile["id"]:
            profiles[index] = profile
            break
    else:
        if len(profiles) >= MAX_SSH_PROFILES:
            raise TerminalConfigError(f"At most {MAX_SSH_PROFILES} SSH profiles are allowed")
        profiles.append(profile)
    _write_profiles(config_file, profiles)
    return profile


def delete_ssh_profile(config_file: Path, profile_id: str) -> None:
    if profile_id == "local" or not PROFILE_ID_RE.fullmatch(str(profile_id)):
        raise TerminalConfigError("Invalid SSH profile id")
    profiles = load_ssh_profiles(config_file)
    remaining = [profile for profile in profiles if profile["id"] != profile_id]
    if len(remaining) == len(profiles):
        raise TerminalConfigError("SSH profile was not found")
    _write_profiles(config_file, remaining)


def _terminal_direct_command(
    profile: dict,
    *,
    shell_binary: str,
    ssh_binary: str,
    ssh_config_file: Path | None = None,
) -> list[str]:
    """Build the shell or SSH command used inside a terminal pane."""
    if profile.get("kind") == "local":
        return [shell_binary, "-l"]

    direct_command = [ssh_binary]
    if ssh_config_file:
        direct_command.extend(["-F", str(ssh_config_file)])
    direct_command.extend([
        "-tt",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
    ])
    if profile.get("port", 22) != 22:
        direct_command.extend(["-p", str(profile["port"])])
    direct_command.append(profile["target"])
    return direct_command


def terminal_profile_command(
    profile: dict,
    *,
    shell_binary: str,
    ssh_binary: str,
    ssh_config_file: Path | None = None,
    tmux_binary: str | None,
    cwd: Path,
) -> tuple[list[str], Path, bool]:
    """Return argv, process cwd, and whether the session survives detach."""
    direct_command = _terminal_direct_command(
        profile,
        shell_binary=shell_binary,
        ssh_binary=ssh_binary,
        ssh_config_file=ssh_config_file,
    )

    if not tmux_binary:
        return direct_command, cwd, False

    session_name = persistent_session_name(profile)
    command = [
        tmux_binary,
        "-L", TMUX_SOCKET_NAME,
        "new-session", "-A", "-D",
        "-s", session_name,
    ]
    if profile.get("kind") == "local":
        command.extend(["-c", str(cwd)])
    else:
        command.append(f"exec {shlex.join(direct_command)}")
    return command, cwd, True


def tmux_server_configuration_command(tmux_binary: str) -> list[str]:
    """Build the dedicated web tmux server configuration command."""
    command = [
        tmux_binary,
        "-L", TMUX_SOCKET_NAME,
        "set-option", "-g", "mouse", "on",
        ";", "set-option", "-g", "prefix", TMUX_WEB_PREFIX,
        ";", "set-option", "-g", "prefix2", "None",
        ";", "set-window-option", "-g", "window-size", "latest",
    ]
    for key, *binding in TMUX_WEB_BINDINGS:
        command.extend([";", "bind-key", "-T", "prefix", key, *binding])
    return command


def tmux_session_configuration_command(
    profile: dict,
    *,
    tmux_binary: str,
    shell_binary: str,
    ssh_binary: str,
    ssh_config_file: Path | None = None,
) -> list[str] | None:
    """Build the SSH default command for new panes in one remote session."""
    if profile.get("kind") == "local":
        return None
    direct_command = _terminal_direct_command(
        profile,
        shell_binary=shell_binary,
        ssh_binary=ssh_binary,
        ssh_config_file=ssh_config_file,
    )
    return [
        tmux_binary,
        "-L", TMUX_SOCKET_NAME,
        "set-option", "-t", persistent_session_name(profile),
        "default-command", f"exec {shlex.join(direct_command)}",
    ]


def _run_tmux_configuration(command: list[str]) -> None:
    for attempt in range(TMUX_CONFIGURATION_ATTEMPTS):
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise TerminalConfigError("Could not configure the web tmux session") from error
        if result.returncode == 0:
            return
        if attempt + 1 < TMUX_CONFIGURATION_ATTEMPTS:
            time.sleep(TMUX_CONFIGURATION_RETRY_SECONDS)
    raise TerminalConfigError("Could not configure the web tmux session")


def configure_tmux_server(tmux_binary: str) -> None:
    """Apply deterministic mouse and key bindings to the web-only tmux server."""
    _run_tmux_configuration(tmux_server_configuration_command(tmux_binary))


def configure_tmux_session(
    profile: dict,
    *,
    tmux_binary: str,
    shell_binary: str,
    ssh_binary: str,
    ssh_config_file: Path | None = None,
) -> None:
    """Keep new windows and panes inside the selected remote SSH host."""
    command = tmux_session_configuration_command(
        profile,
        tmux_binary=tmux_binary,
        shell_binary=shell_binary,
        ssh_binary=ssh_binary,
        ssh_config_file=ssh_config_file,
    )
    if command is None:
        return
    _run_tmux_configuration(command)


def persistent_session_name(profile: dict) -> str:
    if profile.get("kind") == "local":
        return "herdr-local"
    endpoint = f"{profile['target']}:{profile.get('port', 22)}"
    endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()[:8]
    return f"herdr-ssh-{profile['id']}-{endpoint_hash}"


def capture_tmux_pane(
    profile: dict,
    tmux_binary: str | None,
    *,
    maximum_bytes: int = MAX_TERMINAL_CAPTURE_BYTES,
) -> tuple[str, bool]:
    """Capture the active pane history from the dedicated web tmux server."""
    if not tmux_binary:
        raise TerminalConfigError("tmux history is unavailable for this terminal")
    try:
        result = subprocess.run(
            [
                tmux_binary,
                "-L", TMUX_SOCKET_NAME,
                "capture-pane", "-p", "-J", "-S", f"-{MAX_TERMINAL_CAPTURE_LINES}",
                "-t", persistent_session_name(profile),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TerminalConfigError("Could not capture tmux pane history") from error
    if result.returncode != 0:
        raise TerminalConfigError("Could not capture tmux pane history")

    output = bytes(result.stdout or b"")
    truncated = len(output) > maximum_bytes
    if truncated:
        output = output[-maximum_bytes:]
        first_newline = output.find(b"\n")
        if first_newline >= 0:
            output = output[first_newline + 1:]
    return output.decode("utf-8", errors="replace").rstrip("\n"), truncated


def terminate_persistent_session(profile: dict, tmux_binary: str | None) -> bool:
    """Stop a saved tmux session when its endpoint changes or is deleted."""
    if not tmux_binary:
        return False
    try:
        result = subprocess.run(
            [
                tmux_binary,
                "-L", TMUX_SOCKET_NAME,
                "kill-session", "-t", persistent_session_name(profile),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def terminal_environment() -> dict[str, str]:
    environment = dict(os.environ)
    virtual_environment = environment.pop("VIRTUAL_ENV", "")
    secret_names = {
        "HERDR_RELAY_TOKEN",
        "HERDR_TG_TOKEN",
        "HERDR_VAPID_PRIVATE",
        "CLOUDFLARE_TUNNEL_TOKEN",
    }
    for name in secret_names:
        environment.pop(name, None)
    for name in list(environment):
        if name.startswith("UV_") or name in {"PYTHONHOME", "PYTHONPATH"}:
            environment.pop(name, None)
    if virtual_environment and environment.get("PATH"):
        virtual_bin = str(Path(virtual_environment) / "bin")
        environment["PATH"] = os.pathsep.join(
            item for item in environment["PATH"].split(os.pathsep) if item != virtual_bin
        )
    environment["TERM"] = "xterm-256color"
    environment["COLORTERM"] = "truecolor"
    return environment


def validated_terminal_dimensions(cols, rows) -> tuple[int, int]:
    try:
        columns = int(cols)
        lines = int(rows)
    except (TypeError, ValueError) as error:
        raise TerminalConfigError("Terminal size must use integer columns and rows") from error
    return max(20, min(columns, 400)), max(5, min(lines, 200))


def _set_window_size(descriptor: int, cols: int, rows: int) -> None:
    if fcntl is None or termios is None:
        raise TerminalConfigError("PTY-backed web terminals are unavailable on this platform")
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, packed)


TerminalEventHandler = Callable[[dict], Awaitable[None]]


class TerminalSession:
    """One browser attachment to a local or SSH terminal."""

    def __init__(
        self,
        profile: dict,
        event_handler: TerminalEventHandler,
        *,
        shell_binary: str,
        ssh_binary: str,
        ssh_config_file: Path | None = None,
        tmux_binary: str | None,
        cwd: Path,
        cols: int,
        rows: int,
    ):
        self.profile = profile
        self.event_handler = event_handler
        self.shell_binary = shell_binary
        self.ssh_binary = ssh_binary
        self.ssh_config_file = ssh_config_file
        self.tmux_binary = tmux_binary
        self.cwd = cwd
        self.cols, self.rows = validated_terminal_dimensions(cols, rows)
        self.session_id = secrets.token_urlsafe(12)
        self.persistent = bool(tmux_binary)
        self.process: asyncio.subprocess.Process | None = None
        self.master_fd: int | None = None
        self.reader_task: asyncio.Task | None = None
        self.closing = False

    async def spawn(self) -> None:
        if pty is None:
            raise TerminalConfigError(
                "PTY-backed web terminals are unavailable on this platform"
            )
        command, process_cwd, persistent = terminal_profile_command(
            self.profile,
            shell_binary=self.shell_binary,
            ssh_binary=self.ssh_binary,
            ssh_config_file=self.ssh_config_file,
            tmux_binary=self.tmux_binary,
            cwd=self.cwd,
        )
        self.persistent = persistent
        master_fd, slave_fd = pty.openpty()
        try:
            _set_window_size(slave_fd, self.cols, self.rows)
            self.process = await asyncio.create_subprocess_exec(
                *command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=str(process_cwd),
                env=terminal_environment(),
                close_fds=True,
                start_new_session=True,
            )
        except Exception:
            os.close(master_fd)
            raise
        finally:
            os.close(slave_fd)
        os.set_blocking(master_fd, False)
        self.master_fd = master_fd
        if self.persistent and self.tmux_binary:
            try:
                await asyncio.to_thread(configure_tmux_server, self.tmux_binary)
                await asyncio.to_thread(
                    configure_tmux_session,
                    self.profile,
                    tmux_binary=self.tmux_binary,
                    shell_binary=self.shell_binary,
                    ssh_binary=self.ssh_binary,
                    ssh_config_file=self.ssh_config_file,
                )
            except TerminalConfigError:
                await self.close()
                raise

    def start_reader(self) -> None:
        if not self.process or self.master_fd is None or self.reader_task:
            return
        self.reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        exit_code = None
        try:
            while not self.closing and self.master_fd is not None:
                try:
                    chunk = os.read(self.master_fd, 16 * 1024)
                except BlockingIOError:
                    if self.process and self.process.returncode is not None:
                        break
                    await asyncio.sleep(0.015)
                    continue
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                await self.event_handler({
                    "type": "terminal_output",
                    "session_id": self.session_id,
                    "data": base64.b64encode(chunk).decode("ascii"),
                })
            if self.process:
                exit_code = await self.process.wait()
        except (asyncio.CancelledError, ConnectionError):
            raise
        except Exception:  # noqa: BLE001 - PTY reader failures become a terminal_exit event.
            exit_code = self.process.returncode if self.process else 1
        finally:
            if self.master_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self.master_fd)
                self.master_fd = None
            if not self.closing:
                with contextlib.suppress(Exception):
                    await self.event_handler({
                        "type": "terminal_exit",
                        "session_id": self.session_id,
                        "exit_code": exit_code,
                    })

    async def write(self, data: bytes) -> None:
        if not data or len(data) > MAX_TERMINAL_INPUT_BYTES:
            raise TerminalConfigError("Terminal input is empty or too large")
        if self.master_fd is None or not self.process or self.process.returncode is not None:
            raise TerminalConfigError("Terminal session is not running")
        descriptor = self.master_fd
        remaining = memoryview(data)
        deadline = asyncio.get_running_loop().time() + 2
        while remaining:
            if self.master_fd != descriptor or self.process.returncode is not None:
                raise TerminalConfigError("Terminal session is not running")
            try:
                written = os.write(descriptor, remaining)
            except BlockingIOError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise TerminalConfigError("Terminal input could not be delivered")
                await asyncio.sleep(0.01)
                continue
            except OSError as error:
                raise TerminalConfigError("Terminal input could not be delivered") from error
            if written <= 0:
                raise TerminalConfigError("Terminal input could not be delivered")
            remaining = remaining[written:]

    async def resize(self, cols, rows) -> tuple[int, int]:
        columns, lines = validated_terminal_dimensions(cols, rows)
        if (
            self.master_fd is None
            or not self.process
            or self.process.returncode is not None
        ):
            raise TerminalConfigError("Terminal session is not running")
        if (columns, lines) == (self.cols, self.rows):
            return self.cols, self.rows
        self.cols, self.rows = columns, lines
        _set_window_size(self.master_fd, self.cols, self.rows)
        try:
            # The PTY slave is opened before the child starts its own session,
            # so it is not the child's controlling terminal. TIOCSWINSZ still
            # updates the kernel dimensions, but no foreground process group
            # receives SIGWINCH automatically. Notify the isolated child
            # process group explicitly so tmux, SSH and shell line editors
            # re-read the new width before redrawing wrapped input.
            os.killpg(self.process.pid, signal.SIGWINCH)
        except ProcessLookupError as error:
            raise TerminalConfigError("Terminal session is not running") from error
        except OSError as error:
            raise TerminalConfigError("Terminal size could not be applied") from error
        return self.cols, self.rows

    async def capture(self) -> tuple[str, bool]:
        if not self.persistent or not self.tmux_binary:
            raise TerminalConfigError("tmux history is unavailable for this terminal")
        return await asyncio.to_thread(
            capture_tmux_pane,
            self.profile,
            self.tmux_binary,
        )

    async def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        current_task = asyncio.current_task()
        if self.reader_task and self.reader_task is not current_task:
            self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader_task
        if self.master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.master_fd)
            self.master_fd = None
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGHUP)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.process.wait(), timeout=1)
