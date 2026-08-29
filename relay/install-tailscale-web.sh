#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HERDR_CONFIG_DIR:-$HOME/.config/herdr-remote}"
CONFIG_FILE="$CONFIG_DIR/config.env"
WS_PORT="${HERDR_RELAY_PORT:-8375}"
ALLOWED_USERS="${HERDR_TAILSCALE_ALLOWED_USERS:-}"
TAILSCALE_PROXY="${HERDR_TAILSCALE_PROXY:-}"
TAILSCALE_LOGIN_TIMEOUT="${HERDR_TAILSCALE_LOGIN_TIMEOUT:-3m}"
TAILSCALE_PROXY_DROPIN="/etc/systemd/system/tailscaled.service.d/herdr-remote-proxy.conf"
SSH_HOSTS_FILE="${HERDR_SSH_HOSTS_FILE:-$CONFIG_DIR/ssh-hosts.json}"
SSH_CONFIG_FILE="${HERDR_SSH_CONFIG_FILE:-$HOME/.ssh/config}"
CONFIGURE_ONLY=false
RESET_FUNNEL=false
REMOTE_SHELL=false
ADVERTISE_ROUTES=""
INSTALL_TEMP_DIR=""

usage() {
    cat <<'EOF'
Usage: ./install-tailscale-web.sh [options]

Install Tailscale on Arch Linux, Arch-compatible derivatives (including
CachyOS), or Debian 13; authorize the Herdr web UI; and privately expose the
localhost relay with Tailscale Serve.

Options:
  --allowed-users LOGIN[,LOGIN...]  Tailscale login allowlist. Defaults to
                                    the currently logged-in Tailscale user.
  --tailscale-proxy URL             Route tailscaled control traffic through a
                                    loopback HTTP proxy. Clash ports 7897 and
                                    7890 are auto-detected for fake-IP DNS.
  --remote-shell                    Enable official Tailscale SSH and the
                                    allowlisted Herdr Web Terminal. Installs
                                    OpenSSH client and tmux when needed.
  --ssh-hosts-file PATH             SSH profile JSON used by the Web Terminal.
                                    Defaults to ~/.config/herdr-remote/ssh-hosts.json.
  --advertise-routes CIDR[,CIDR...] Optionally make this machine a Tailscale
                                    subnet router for its LAN. Requires admin
                                    approval in the Tailscale console.
  --configure-only                  Skip package installation.
  --reset-funnel                    Reset every Funnel route on this machine
                                    before configuring private Serve.
  -h, --help                        Show this help.

The script never enables Tailscale Funnel. --reset-funnel is intentionally
explicit because it can remove Funnel routes used by other applications.
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$INSTALL_TEMP_DIR" && -d "$INSTALL_TEMP_DIR" && "$INSTALL_TEMP_DIR" == /tmp/herdr-tailscale.* ]]; then
        rm -rf -- "$INSTALL_TEMP_DIR"
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --allowed-users)
            [[ $# -ge 2 ]] || die "--allowed-users requires a value"
            ALLOWED_USERS="$2"
            shift 2
            ;;
        --configure-only)
            CONFIGURE_ONLY=true
            shift
            ;;
        --tailscale-proxy)
            [[ $# -ge 2 ]] || die "--tailscale-proxy requires a value"
            TAILSCALE_PROXY="$2"
            shift 2
            ;;
        --remote-shell)
            REMOTE_SHELL=true
            shift
            ;;
        --ssh-hosts-file)
            [[ $# -ge 2 ]] || die "--ssh-hosts-file requires a value"
            SSH_HOSTS_FILE="$2"
            shift 2
            ;;
        --advertise-routes)
            [[ $# -ge 2 ]] || die "--advertise-routes requires a value"
            ADVERTISE_ROUTES="$2"
            shift 2
            ;;
        --reset-funnel)
            RESET_FUNNEL=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

[[ "$(uname -s)" == "Linux" ]] || die "this installer currently supports Linux only"
[[ -r /etc/os-release ]] || die "/etc/os-release is missing"

# shellcheck disable=SC1091
source /etc/os-release
DISTRO_ID="${HERDR_INSTALL_DISTRO:-${ID:-unknown}}"
DISTRO_ID_LIKE="${HERDR_INSTALL_ID_LIKE:-${ID_LIKE:-}}"
DISTRO_VERSION="${HERDR_INSTALL_VERSION:-${VERSION_ID:-}}"

is_arch=false
is_debian=false
if [[ "$DISTRO_ID" == "arch" || "$DISTRO_ID" == "cachyos" || " $DISTRO_ID_LIKE " == *" arch "* ]]; then
    is_arch=true
elif [[ "$DISTRO_ID" == "debian" ]]; then
    is_debian=true
fi

if [[ "$is_debian" == true && "$DISTRO_VERSION" != "13" ]]; then
    die "Debian $DISTRO_VERSION is not covered by this script; Debian 13 (trixie) is supported"
fi
if [[ "$is_arch" == false && "$is_debian" == false ]]; then
    die "unsupported distribution: $DISTRO_ID (supported: Arch Linux and compatible derivatives such as CachyOS, Debian 13)"
fi

ensure_install_temp_dir() {
    if [[ -z "$INSTALL_TEMP_DIR" ]]; then
        INSTALL_TEMP_DIR="$(mktemp -d -t herdr-tailscale.XXXXXX)"
    fi
}

install_dependencies() {
    if [[ "$is_arch" == true ]]; then
        command -v pacman >/dev/null 2>&1 || die "pacman is required on Arch Linux-compatible systems"
        echo "Installing Tailscale and helper packages with pacman ($DISTRO_ID)..."
        local packages=(tailscale curl python)
        if [[ "$REMOTE_SHELL" == true ]]; then
            packages+=(openssh tmux)
        fi
        sudo pacman -S --needed "${packages[@]}"
        return
    fi

    command -v apt-get >/dev/null 2>&1 || die "apt-get is required on Debian"
    echo "Installing Debian prerequisites..."
    sudo apt-get update
    local packages=(ca-certificates curl python3)
    if [[ "$REMOTE_SHELL" == true ]]; then
        packages+=(openssh-client tmux)
    fi
    sudo apt-get install -y "${packages[@]}"

    ensure_install_temp_dir
    curl --fail --location --silent --show-error \
        --output "$INSTALL_TEMP_DIR/tailscale-archive-keyring.gpg" \
        https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg
    curl --fail --location --silent --show-error \
        --output "$INSTALL_TEMP_DIR/tailscale.list" \
        https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list

    sudo install -m 0755 -d /usr/share/keyrings /etc/apt/sources.list.d
    sudo install -m 0644 "$INSTALL_TEMP_DIR/tailscale-archive-keyring.gpg" \
        /usr/share/keyrings/tailscale-archive-keyring.gpg
    sudo install -m 0644 "$INSTALL_TEMP_DIR/tailscale.list" \
        /etc/apt/sources.list.d/tailscale.list
    sudo apt-get update
    sudo apt-get install -y tailscale
}

if [[ "$CONFIGURE_ONLY" == false ]]; then
    install_dependencies
fi

command -v tailscale >/dev/null 2>&1 || die "tailscale is not installed; rerun without --configure-only"
command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
if [[ "$REMOTE_SHELL" == true ]]; then
    command -v ssh >/dev/null 2>&1 \
        || die "OpenSSH client is required; rerun without --configure-only"
    command -v tmux >/dev/null 2>&1 \
        || die "tmux is required for persistent web terminal sessions; rerun without --configure-only"
fi

if [[ "$SSH_HOSTS_FILE" == "~/"* ]]; then
    SSH_HOSTS_FILE="$HOME/${SSH_HOSTS_FILE#\~/}"
fi
if [[ "$SSH_HOSTS_FILE" != /* ]]; then
    SSH_HOSTS_FILE="$PWD/${SSH_HOSTS_FILE#./}"
fi
[[ "$SSH_HOSTS_FILE" != *$'\n'* && "$SSH_HOSTS_FILE" != *$'\r'* ]] \
    || die "--ssh-hosts-file contains control characters"
[[ "$SSH_HOSTS_FILE" != *[[:space:]]* ]] \
    || die "--ssh-hosts-file must not contain whitespace"

proxy_reaches_controlplane() {
    curl --proxy "$1" --noproxy "" \
        --connect-timeout 3 --max-time 10 --output /dev/null --silent \
        https://controlplane.tailscale.com/ \
        2>/dev/null
}

tailscale_control_ip() {
    getent ahostsv4 controlplane.tailscale.com 2>/dev/null \
        | awk 'NR == 1 { print $1; exit }'
}

is_fake_proxy_ip() {
    [[ "$1" == 198.18.* || "$1" == 198.19.* ]]
}

CONTROL_IP="$(tailscale_control_ip || true)"
if [[ -z "$TAILSCALE_PROXY" ]] && is_fake_proxy_ip "$CONTROL_IP"; then
    for proxy_candidate in http://127.0.0.1:7897 http://127.0.0.1:7890; do
        if proxy_reaches_controlplane "$proxy_candidate"; then
            TAILSCALE_PROXY="$proxy_candidate"
            echo "Detected Clash/Mihomo fake-IP DNS and a working local proxy at $TAILSCALE_PROXY."
            break
        fi
    done
fi

configure_tailscale_proxy() {
    [[ -n "$TAILSCALE_PROXY" ]] || return
    TAILSCALE_PROXY="${TAILSCALE_PROXY%/}"
    if [[ ! "$TAILSCALE_PROXY" =~ ^http://(127\.0\.0\.1|localhost):([0-9]{1,5})$ ]]; then
        die "--tailscale-proxy must be a loopback HTTP URL such as http://127.0.0.1:7897"
    fi
    local proxy_port="${BASH_REMATCH[2]}"
    if (( 10#$proxy_port < 1 || 10#$proxy_port > 65535 )); then
        die "--tailscale-proxy contains an invalid port"
    fi

    echo "Checking the local Tailscale proxy..."
    proxy_reaches_controlplane "$TAILSCALE_PROXY" \
        || die "the proxy $TAILSCALE_PROXY cannot reach controlplane.tailscale.com"

    ensure_install_temp_dir
    local proxy_config="$INSTALL_TEMP_DIR/herdr-remote-proxy.conf"
    printf '%s\n' \
        '[Service]' \
        "Environment=\"HTTP_PROXY=$TAILSCALE_PROXY\"" \
        "Environment=\"HTTPS_PROXY=$TAILSCALE_PROXY\"" \
        'Environment="NO_PROXY=localhost,127.0.0.1,::1"' \
        > "$proxy_config"
    echo "Configuring tailscaled to use $TAILSCALE_PROXY..."
    sudo install -m 0755 -d "$(dirname "$TAILSCALE_PROXY_DROPIN")"
    sudo install -m 0644 "$proxy_config" "$TAILSCALE_PROXY_DROPIN"
    sudo systemctl daemon-reload
}

tailscale_backend_state() {
    local status_json
    status_json="$(tailscale status --json 2>/dev/null || true)"
    [[ -n "$status_json" ]] || return
    printf '%s' "$status_json" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("BackendState", ""))
except Exception:
    pass
'
}

configure_tailscale_proxy

echo "Enabling tailscaled..."
sudo systemctl enable --now tailscaled.service
if [[ -n "$TAILSCALE_PROXY" ]]; then
    sudo systemctl restart tailscaled.service
fi

if [[ "$(tailscale_backend_state)" != "Running" ]]; then
    if [[ -z "$TAILSCALE_PROXY" && ! -f "$TAILSCALE_PROXY_DROPIN" ]] \
        && is_fake_proxy_ip "$CONTROL_IP"; then
        echo "Error: controlplane.tailscale.com resolves to $CONTROL_IP, which is in" >&2
        echo "Clash/Mihomo's common fake-IP range. tailscaled cannot reach that fake" >&2
        echo "address on this host, so it cannot generate a login URL." >&2
        echo "" >&2
        echo "Rerun with Clash's HTTP/mixed port, for example:" >&2
        echo "  $0 --tailscale-proxy http://127.0.0.1:7897" >&2
        exit 1
    fi
    echo "Tailscale needs to authenticate this machine."
    echo "Open the login URL printed below. This command exits automatically after"
    echo "authentication and will stop after $TAILSCALE_LOGIN_TIMEOUT instead of waiting forever."
    if ! sudo tailscale up --timeout="$TAILSCALE_LOGIN_TIMEOUT"; then
        echo "Tailscale did not reach the Running state within $TAILSCALE_LOGIN_TIMEOUT." >&2
        echo "Check: sudo journalctl -u tailscaled.service -n 50 --no-pager" >&2
        [[ -n "$TAILSCALE_PROXY" ]] \
            && echo "Also confirm that the local proxy remains available at $TAILSCALE_PROXY." >&2
        exit 1
    fi
fi
[[ "$(tailscale_backend_state)" == "Running" ]] || die "Tailscale is not connected"

tailscale_identity() {
    tailscale status --json | python3 -c '
import json, sys
status = json.load(sys.stdin)
self_node = status.get("Self") or {}
user_id = str(self_node.get("UserID", ""))
users = status.get("User") or {}
user = users.get(user_id) or users.get(int(user_id) if user_id.isdigit() else user_id) or {}
print(user.get("LoginName", ""))
'
}

funnel_state() {
    local serve_json="$1"
    if [[ -z "$serve_json" ]]; then
        printf 'unknown\n'
        return
    fi
    printf '%s' "$serve_json" | python3 -c '
import json, sys
try:
    config = json.load(sys.stdin)
except Exception:
    print("unknown")
    raise SystemExit
allow = config.get("AllowFunnel") or {}
if isinstance(allow, dict):
    print("yes" if any(value is True for value in allow.values()) else "no")
else:
    print("unknown")
' 2>/dev/null || printf 'unknown\n'
}

if [[ -z "$ALLOWED_USERS" ]]; then
    ALLOWED_USERS="$(tailscale_identity 2>/dev/null || true)"
fi
if [[ -z "$ALLOWED_USERS" ]]; then
    if [[ -t 0 ]]; then
        read -r -p "Authorized Tailscale login (usually your email): " ALLOWED_USERS
    else
        die "could not detect a Tailscale login; pass --allowed-users LOGIN"
    fi
fi

IFS=',' read -r -a allowed_user_items <<< "$ALLOWED_USERS"
WILDCARD_ALLOWED=false
for allowed_user in "${allowed_user_items[@]}"; do
    [[ -n "$allowed_user" ]] || die "the Tailscale user allowlist contains an empty item"
    if [[ "$allowed_user" != "*" && ! "$allowed_user" =~ ^[A-Za-z0-9._+@:%-]+$ ]]; then
        die "invalid Tailscale login in allowlist: $allowed_user"
    fi
    [[ "$allowed_user" == "*" ]] && WILDCARD_ALLOWED=true
done
if [[ "$WILDCARD_ALLOWED" == true ]]; then
    echo "Warning: '*' authorizes every authenticated user who can reach this machine in the tailnet."
fi
if [[ "$REMOTE_SHELL" == true && "$WILDCARD_ALLOWED" == true ]]; then
    die "--remote-shell requires explicit Tailscale login names; wildcard terminal access is not allowed by the installer"
fi

if [[ "$REMOTE_SHELL" == true ]]; then
    echo "Enabling the official Tailscale SSH server..."
    sudo tailscale set --ssh
fi

if [[ -n "$ADVERTISE_ROUTES" ]]; then
    python3 -c '
import ipaddress, sys
routes = [value.strip() for value in sys.argv[1].split(",")]
if not routes or any(not value for value in routes):
    raise SystemExit("route list contains an empty item")
for value in routes:
    network = ipaddress.ip_network(value, strict=False)
    if network.is_multicast or network.is_unspecified or network.is_loopback:
        raise SystemExit(f"unsafe subnet route: {value}")
' "$ADVERTISE_ROUTES" || die "--advertise-routes must contain safe CIDR networks"

    ensure_install_temp_dir
    subnet_sysctl="$INSTALL_TEMP_DIR/99-herdr-tailscale-subnet.conf"
    printf '%s\n' 'net.ipv4.ip_forward = 1' > "$subnet_sysctl"
    if [[ "$ADVERTISE_ROUTES" == *:* ]]; then
        printf '%s\n' 'net.ipv6.conf.all.forwarding = 1' >> "$subnet_sysctl"
    fi
    echo "Enabling kernel forwarding for the requested subnet routes..."
    sudo install -m 0644 "$subnet_sysctl" /etc/sysctl.d/99-herdr-tailscale-subnet.conf
    sudo sysctl -p /etc/sysctl.d/99-herdr-tailscale-subnet.conf
    echo "Advertising LAN routes through Tailscale: $ADVERTISE_ROUTES"
    sudo tailscale set --advertise-routes="$ADVERTISE_ROUTES"
fi

[[ -f "$CONFIG_FILE" ]] || die "Herdr Relay is not installed. Run $SCRIPT_DIR/install-telegram-only.sh first."

CURRENT_SERVE_JSON="$(sudo tailscale serve status --json 2>/dev/null || true)"
CURRENT_FUNNEL_STATE="$(funnel_state "$CURRENT_SERVE_JSON")"
if [[ "$CURRENT_FUNNEL_STATE" == "yes" && "$RESET_FUNNEL" == false ]]; then
    die "an existing Tailscale Funnel route is active; inspect 'sudo tailscale funnel status', then rerun with --reset-funnel only if removing every Funnel route is safe"
fi
if [[ "$CURRENT_FUNNEL_STATE" == "unknown" && "$RESET_FUNNEL" == false ]]; then
    die "could not verify the existing Funnel state; inspect 'sudo tailscale funnel status' before exposing the Herdr web console"
fi
if [[ "$RESET_FUNNEL" == true ]]; then
    echo "Resetting existing Funnel routes as explicitly requested..."
    sudo tailscale funnel reset
fi

upsert_config() {
    local key="$1"
    local value="$2"
    local temporary="$CONFIG_FILE.tmp.$$"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ "^" key "=" {
            if (!found) print key "=" value
            found = 1
            next
        }
        { print }
        END { if (!found) print key "=" value }
    ' "$CONFIG_FILE" > "$temporary"
    chmod --reference="$CONFIG_FILE" "$temporary"
    mv "$temporary" "$CONFIG_FILE"
}

echo "Configuring Relay for trusted Tailscale Serve identity headers..."
upsert_config HERDR_RELAY_HOST 127.0.0.1
upsert_config HERDR_ALLOW_REMOTE_BIND 0
upsert_config HERDR_ALLOW_INSECURE_NO_AUTH 0
upsert_config HERDR_TAILSCALE_WEB 1
upsert_config HERDR_TAILSCALE_ALLOWED_USERS "$ALLOWED_USERS"
if [[ "$REMOTE_SHELL" == true ]]; then
    mkdir -p "$(dirname "$SSH_HOSTS_FILE")"
    if [[ ! -f "$SSH_HOSTS_FILE" ]]; then
        printf '%s\n' '{' '  "version": 1,' '  "hosts": []' '}' > "$SSH_HOSTS_FILE"
    fi
    chmod 0600 "$SSH_HOSTS_FILE"
    PYTHONPATH="$SCRIPT_DIR" python3 -c '
import sys
from pathlib import Path
from terminal_sessions import load_ssh_profiles
load_ssh_profiles(Path(sys.argv[1]))
' "$SSH_HOSTS_FILE" || die "the SSH profile file is invalid: $SSH_HOSTS_FILE"

    upsert_config HERDR_TAILSCALE_SSH 1
    upsert_config HERDR_WEB_TERMINAL 1
    upsert_config HERDR_TERMINAL_ALLOWED_USERS "$ALLOWED_USERS"
    upsert_config HERDR_SSH_HOSTS_FILE "$SSH_HOSTS_FILE"
    upsert_config HERDR_SSH_CONFIG_FILE "$SSH_CONFIG_FILE"
    upsert_config HERDR_TERMINAL_MAX_SESSIONS 6
fi

if ! systemctl --user cat herdr-relay.service >/dev/null 2>&1; then
    die "herdr-relay.service is missing; rerun install-telegram-only.sh before this script"
fi
systemctl --user daemon-reload
systemctl --user restart herdr-relay.service
if ! systemctl --user is-active --quiet herdr-relay.service; then
    echo "Relay failed to restart. Recent log output:" >&2
    journalctl --user -u herdr-relay.service -n 30 --no-pager >&2 || true
    exit 1
fi

echo "Publishing the localhost Relay privately with Tailscale Serve..."
sudo tailscale serve --bg "http://127.0.0.1:$WS_PORT"

SERVE_JSON="$(sudo tailscale serve status --json 2>/dev/null || true)"
FUNNEL_ACTIVE="$(funnel_state "$SERVE_JSON")"

DNS_NAME="$(tailscale status --json | python3 -c '
import json, sys
name = (json.load(sys.stdin).get("Self") or {}).get("DNSName", "")
print(name.rstrip("."))
')"

echo ""
echo "Tailscale Serve status"
echo "----------------------"
sudo tailscale serve status
echo ""
if [[ "$FUNNEL_ACTIVE" == "yes" ]]; then
    echo "Error: a Funnel route is active after configuration." >&2
    echo "Disable public access immediately and inspect: sudo tailscale funnel status" >&2
    exit 1
elif [[ "$FUNNEL_ACTIVE" == "unknown" ]]; then
    echo "Error: Funnel state could not be verified after configuration." >&2
    echo "Inspect public exposure immediately: sudo tailscale funnel status" >&2
    exit 1
else
    echo "[ok] No Funnel route is enabled in the current Serve configuration."
fi

echo ""
echo "Herdr Tailscale web control is ready."
[[ -n "$DNS_NAME" ]] && echo "Open on your phone or another tailnet device: https://$DNS_NAME"
echo "Authorized user(s): $ALLOWED_USERS"
echo "Relay:              127.0.0.1:$WS_PORT"
echo "Config:             $CONFIG_FILE"
if [[ "$REMOTE_SHELL" == true ]]; then
    LOCAL_USER="$(id -un)"
    echo "Tailscale SSH:       enabled"
    [[ -n "$DNS_NAME" ]] && echo "Native SSH command:  tailscale ssh $LOCAL_USER@$DNS_NAME"
    echo "Web Terminal:        enabled for $ALLOWED_USERS"
    echo "SSH profiles:        $SSH_HOSTS_FILE"
    echo ""
    echo "Tailscale SSH access still follows your tailnet SSH/grants policy."
fi
if [[ -n "$ADVERTISE_ROUTES" ]]; then
    echo "Subnet routes:       $ADVERTISE_ROUTES (advertised)"
    echo "Approve these routes in the Tailscale admin console before clients can use them."
fi
echo ""
echo "Useful checks:"
echo "  systemctl --user status herdr-relay"
echo "  sudo tailscale serve status"
echo "  sudo tailscale funnel status"
[[ "$REMOTE_SHELL" == true ]] && echo "  tailscale debug prefs | grep -i RunSSH"
