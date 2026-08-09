"""Validated SSH profiles and PTY-backed terminal sessions for the web UI."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import pty
import re
import secrets
import shlex
import signal
import struct
import subprocess
import termios
from collections.abc import Awaitable, Callable
from pathlib import Path

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9._@:%+\-\[\]]{1,255}$")
PROFILE_COLORS = {"violet", "cyan", "green", "amber", "rose"}
MAX_SSH_PROFILES = 32
MAX_TERMINAL_INPUT_BYTES = 16 * 1024
TMUX_SOCKET_NAME = "herdr-web"


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

    return {
        "id": profile_id,
        "kind": "ssh",
        "label": label,
        "target": target,
        "port": port,
        "description": description,
        "color": color,
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


def terminal_profile_command(
    profile: dict,
    *,
    shell_binary: str,
    ssh_binary: str,
    tmux_binary: str | None,
    cwd: Path,
) -> tuple[list[str], Path, bool]:
    """Return argv, process cwd, and whether the session survives detach."""
    if profile.get("kind") == "local":
        direct_command = [shell_binary, "-l"]
    else:
        direct_command = [
            ssh_binary,
            "-tt",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
        ]
        if profile.get("port", 22) != 22:
            direct_command.extend(["-p", str(profile["port"])])
        direct_command.append(profile["target"])

    if not tmux_binary:
        return direct_command, cwd, False

    session_name = persistent_session_name(profile)
    command = [
        tmux_binary,
        "-L", TMUX_SOCKET_NAME,
        "new-session", "-A",
        "-s", session_name,
    ]
    if profile.get("kind") == "local":
        command.extend(["-c", str(cwd)])
    else:
        command.append(f"exec {shlex.join(direct_command)}")
    return command, cwd, True


def persistent_session_name(profile: dict) -> str:
    if profile.get("kind") == "local":
        return "herdr-local"
    endpoint = f"{profile['target']}:{profile.get('port', 22)}"
    endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()[:8]
    return f"herdr-ssh-{profile['id']}-{endpoint_hash}"


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
        tmux_binary: str | None,
        cwd: Path,
        cols: int,
        rows: int,
    ):
        self.profile = profile
        self.event_handler = event_handler
        self.shell_binary = shell_binary
        self.ssh_binary = ssh_binary
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
        command, process_cwd, persistent = terminal_profile_command(
            self.profile,
            shell_binary=self.shell_binary,
            ssh_binary=self.ssh_binary,
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
        self.cols, self.rows = validated_terminal_dimensions(cols, rows)
        if self.master_fd is None:
            raise TerminalConfigError("Terminal session is not running")
        _set_window_size(self.master_fd, self.cols, self.rows)
        return self.cols, self.rows

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
