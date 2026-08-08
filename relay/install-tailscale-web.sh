#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${HERDR_CONFIG_DIR:-$HOME/.config/herdr-remote}"
CONFIG_FILE="$CONFIG_DIR/config.env"
WS_PORT="${HERDR_RELAY_PORT:-8375}"
ALLOWED_USERS="${HERDR_TAILSCALE_ALLOWED_USERS:-}"
CONFIGURE_ONLY=false
RESET_FUNNEL=false
INSTALL_TEMP_DIR=""

usage() {
    cat <<'EOF'
Usage: ./install-tailscale-web.sh [options]

Install Tailscale on Arch Linux or Debian 13, authorize the Herdr web UI,
and privately expose the localhost relay with Tailscale Serve.

Options:
  --allowed-users LOGIN[,LOGIN...]  Tailscale login allowlist. Defaults to
                                    the currently logged-in Tailscale user.
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
DISTRO_VERSION="${HERDR_INSTALL_VERSION:-${VERSION_ID:-}}"

is_arch=false
is_debian=false
if [[ "$DISTRO_ID" == "arch" ]]; then
    is_arch=true
elif [[ "$DISTRO_ID" == "debian" ]]; then
    is_debian=true
fi

if [[ "$is_debian" == true && "$DISTRO_VERSION" != "13" ]]; then
    die "Debian $DISTRO_VERSION is not covered by this script; Debian 13 (trixie) is supported"
fi
if [[ "$is_arch" == false && "$is_debian" == false ]]; then
    die "unsupported distribution: $DISTRO_ID (supported: Arch Linux, Debian 13)"
fi

install_dependencies() {
    if [[ "$is_arch" == true ]]; then
        command -v pacman >/dev/null 2>&1 || die "pacman is required on Arch Linux"
        echo "Installing Tailscale and helper packages with pacman..."
        sudo pacman -S --needed tailscale curl python
        return
    fi

    command -v apt-get >/dev/null 2>&1 || die "apt-get is required on Debian"
    echo "Installing Debian prerequisites..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl python3

    INSTALL_TEMP_DIR="$(mktemp -d -t herdr-tailscale.XXXXXX)"
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

echo "Enabling tailscaled..."
sudo systemctl enable --now tailscaled.service

if ! tailscale status >/dev/null 2>&1; then
    echo "Tailscale needs to authenticate this machine."
    echo "Follow the login URL printed by the next command, then return here."
    sudo tailscale up
fi
tailscale status >/dev/null 2>&1 || die "Tailscale is not connected"

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
for allowed_user in "${allowed_user_items[@]}"; do
    [[ -n "$allowed_user" ]] || die "the Tailscale user allowlist contains an empty item"
    if [[ "$allowed_user" != "*" && ! "$allowed_user" =~ ^[A-Za-z0-9._+@:%-]+$ ]]; then
        die "invalid Tailscale login in allowlist: $allowed_user"
    fi
done
if [[ "$ALLOWED_USERS" == "*" ]]; then
    echo "Warning: '*' authorizes every authenticated user who can reach this machine in the tailnet."
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
echo ""
echo "Useful checks:"
echo "  systemctl --user status herdr-relay"
echo "  sudo tailscale serve status"
echo "  sudo tailscale funnel status"
