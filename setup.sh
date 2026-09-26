#!/usr/bin/env bash
# One-command interactive setup for inky-dash.
#
# Run this from your normal computer (not the Pi). It will:
#   1. check/install the tools this repo needs locally (uv, node, ssh, rsync)
#   2. connect to your Raspberry Pi and set it up (hostname, SPI/I2C, Bluetooth, uv)
#   3. build and deploy the dashboard, and install it as a service that starts on boot
#   4. let you pick which panels to show
#   5. optionally walk you through Google Calendar access
#
# Prerequisite: a Raspberry Pi already running Raspberry Pi OS with SSH enabled
# (the Raspberry Pi Imager can set username, password, Wi-Fi and SSH when flashing),
# with the Inky Impression display seated on its GPIO header.
#
# Safe to re-run at any time: completed steps are detected and skipped.
set -euo pipefail
cd "$(dirname "$0")"

bold=$(tput bold 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)

say()  { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\nerror: %s\n' "$*" >&2; exit 1; }

ask() { # ask <question> <default> -> $REPLY
  local q=$1 d=${2-}
  if [ -n "$d" ]; then
    read -r -p "$q [$d]: " REPLY </dev/tty; REPLY=${REPLY:-$d}
  else
    read -r -p "$q: " REPLY </dev/tty
  fi
}

confirm() { # confirm <question> [y|n]  (default answer)
  local q=$1 d=${2:-y} yn hint
  [ "$d" = y ] && hint="Y/n" || hint="y/N"
  read -r -p "$q [$hint]: " yn </dev/tty
  yn=${yn:-$d}
  case "$yn" in [yY]*) return 0 ;; *) return 1 ;; esac
}

pi()      { ssh -o ConnectTimeout=10 "$PI_HOST" "$@"; }
pi_sudo() { ssh -t -o ConnectTimeout=10 "$PI_HOST" "$@"; }  # -t so sudo can prompt

# ---------------------------------------------------------------- local tools
say "Checking tools on this computer"

command -v ssh   >/dev/null || die "ssh is required"
command -v rsync >/dev/null || die "rsync is required (macOS/Linux ship it by default)"

if ! command -v uv >/dev/null; then
  note "uv (Python package manager) is not installed."
  if confirm "Install uv now via the official installer?"; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null || die "uv installed but not on PATH — open a new terminal and re-run"
  else
    die "install uv from https://docs.astral.sh/uv/ and re-run"
  fi
fi

command -v npm >/dev/null || die "Node.js (with npm) is required — install from https://nodejs.org (or 'brew install node') and re-run"

note "installing project dependencies (first run takes a minute)…"
(cd backend && uv sync --quiet)
(cd frontend && npm install --silent --no-fund --no-audit)
note "local tools ready"

# ------------------------------------------------------------- reach the Pi
say "Connecting to your Raspberry Pi"

DEFAULT_HOST="pi@raspberrypi.local"
if [ -f .deploy.env ]; then
  # shellcheck disable=SC1091
  . ./.deploy.env
  DEFAULT_HOST="$PI_HOST"
fi
ask "Pi SSH address (user@host, as used with ssh)" "$DEFAULT_HOST"
PI_HOST=$REPLY

HAVE_KEYS=true
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$PI_HOST" true 2>/dev/null; then
  note "No passwordless SSH to $PI_HOST yet."
  if confirm "Set up SSH keys now (recommended — you'll type the Pi's password once)?"; then
    [ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519"
    ssh-copy-id "$PI_HOST" || die "could not copy SSH key — check the address and password"
  else
    HAVE_KEYS=false
    note "Continuing with password prompts; you'll be asked several times."
  fi
fi
pi true || die "cannot reach $PI_HOST over SSH"
REMOTE_USER=$(pi 'echo "$USER"' | tr -d '\r\n')
REMOTE_HOME=$(pi 'echo "$HOME"' | tr -d '\r\n')
note "connected as $REMOTE_USER"

# ------------------------------------------------------------ prepare the Pi
say "Preparing the Pi"
NEED_REBOOT=false

CURRENT_HOSTNAME=$(pi hostname | tr -d '\r\n')
ask "Hostname for the Pi on your network (the dashboard URL becomes http://<hostname>)" "$CURRENT_HOSTNAME"
NEW_HOSTNAME=$REPLY
if [ "$NEW_HOSTNAME" != "$CURRENT_HOSTNAME" ]; then
  pi_sudo "sudo raspi-config nonint do_hostname '$NEW_HOSTNAME'"
  NEED_REBOOT=true
  note "hostname will change to $NEW_HOSTNAME after reboot"
fi

if [ "$(pi 'raspi-config nonint get_spi' | tr -d '\r\n')" != "0" ]; then
  note "enabling SPI (display data bus)…"
  pi_sudo "sudo raspi-config nonint do_spi 0"
  NEED_REBOOT=true
fi
if [ "$(pi 'raspi-config nonint get_i2c' | tr -d '\r\n')" != "0" ]; then
  note "enabling I2C (display auto-detection)…"
  pi_sudo "sudo raspi-config nonint do_i2c 0"
  NEED_REBOOT=true
fi

if ! pi 'id -nG' | tr -d '\r' | grep -qw bluetooth; then
  note "allowing $REMOTE_USER to use Bluetooth (for the Govee sensor)…"
  pi_sudo "sudo usermod -aG bluetooth '$REMOTE_USER'"
fi

if ! pi "[ -x '$REMOTE_HOME/.local/bin/uv' ]"; then
  note "installing uv on the Pi…"
  pi 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

if $NEED_REBOOT; then
  say "Rebooting the Pi to apply hardware settings"
  pi_sudo "sudo reboot" || true
  # Prefer the new hostname; keep fallbacks in case mDNS naming differs.
  CANDIDATES="$REMOTE_USER@$NEW_HOSTNAME $REMOTE_USER@$NEW_HOSTNAME.local $PI_HOST"
  sleep 15
  FOUND=""
  for _ in $(seq 1 24); do
    for c in $CANDIDATES; do
      if $HAVE_KEYS && ssh -o BatchMode=yes -o ConnectTimeout=3 "$c" true 2>/dev/null; then
        FOUND=$c
        break 2
      fi
    done
    if ! $HAVE_KEYS; then
      read -r -p "Press Enter once the Pi has finished rebooting… " _ </dev/tty
      FOUND=$PI_HOST
      break
    fi
    printf '.'
    sleep 5
  done
  echo
  [ -n "$FOUND" ] || die "Pi did not come back — check power/network, then re-run this script"
  PI_HOST=$FOUND
  note "Pi is back at $PI_HOST"
fi

printf 'PI_HOST=%q\n' "$PI_HOST" > .deploy.env
note "saved target to .deploy.env — future ./deploy.sh runs will use it"

# ---------------------------------------------------------------- deploy
say "Building and deploying the dashboard"
PI_HOST="$PI_HOST" ./deploy.sh --skip-tests

# ---------------------------------------------------------------- panels
say "Choosing dashboard panels"
note "All of this can be changed later in the web UI."

if pi "[ -f ~/inky-dash/backend/config.json ]"; then
  note "existing configuration found on the Pi — leaving it untouched"
  WANT_CALENDAR=$(pi "grep -q '\"calendar\"' ~/inky-dash/backend/config.json" && echo true || echo false)
else
  confirm "Show weather (needs nothing — free service, no account)?" && WANT_WEATHER=true || WANT_WEATHER=false
  confirm "Show a Govee H5075 temperature/humidity sensor (needs the sensor in Bluetooth range)?" && WANT_GOVEE=true || WANT_GOVEE=false
  confirm "Show Google Calendar (needs a one-time Google sign-in, offered below)?" && WANT_CALENDAR=true || WANT_CALENDAR=false
  confirm "Show headlines from The Economist (needs nothing — public feeds)?" && WANT_ECONOMIST=true || WANT_ECONOMIST=false

  PANELS='{"slot": 0, "module": "clock"}'
  slot=1
  for choice in "weather:$WANT_WEATHER" "govee:$WANT_GOVEE" "calendar:$WANT_CALENDAR" "economist:$WANT_ECONOMIST"; do
    if [ "${choice#*:}" = true ]; then
      PANELS="$PANELS, {\"slot\": $slot, \"module\": \"${choice%%:*}\"}"
      slot=$((slot + 1))
    fi
  done
  CONFIG_JSON="{\"layout\": {\"template\": \"dashboard_clock\", \"panels\": [$PANELS]}}"
  printf '%s' "$CONFIG_JSON" | python3 -m json.tool >/dev/null || die "internal error: generated invalid config"
  printf '%s\n' "$CONFIG_JSON" | pi "cat > ~/inky-dash/backend/config.json"
  note "initial configuration written"
fi

# ---------------------------------------------------------------- service
say "Installing the inky-dash service (starts on boot)"
pi "cat > /tmp/inky-dash.service" <<EOF
[Unit]
Description=inky-dash e-ink dashboard
After=network-online.target bluetooth.service
Wants=network-online.target

[Service]
User=$REMOTE_USER
Group=$REMOTE_USER
SupplementaryGroups=bluetooth gpio spi i2c
WorkingDirectory=$REMOTE_HOME/inky-dash/backend
ExecStart=$REMOTE_HOME/.local/bin/uv run uvicorn inky_dash.main:app --host 0.0.0.0 --port 80
# Port 80 is privileged; grant just the bind capability rather than running as root.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
pi_sudo "sudo install -m 644 /tmp/inky-dash.service /etc/systemd/system/inky-dash.service && rm /tmp/inky-dash.service && sudo systemctl daemon-reload && sudo systemctl enable --now inky-dash && sudo systemctl restart inky-dash"

# ---------------------------------------------------------------- firewall
# Only touched when ufw is installed and active. The dashboard has no auth, so
# port 80 is opened to the local subnet only, never to the whole internet.
# (ufw lives in /usr/sbin, which is not on a normal user's PATH — always sudo it.)
if pi "[ -x /usr/sbin/ufw ]" && pi_sudo "sudo ufw status" | grep -q 'Status: active'; then
  say "Opening port 80 in the firewall"
  LAN_CIDR=$(pi "ip -4 route show default | grep -oE 'src [0-9.]+' | awk '{print \$2}'" | tr -d '\r\n')
  LAN_CIDR="${LAN_CIDR%.*}.0/24"
  pi_sudo "sudo ufw allow proto tcp from $LAN_CIDR to any port 80 comment 'inky-dash LAN only'"
  # Retire the old port now that nothing listens on it.
  pi_sudo "sudo ufw --force delete allow proto tcp from $LAN_CIDR to any port 8000" >/dev/null 2>&1 || true
  note "port 80 allowed from $LAN_CIDR"
fi

# ---------------------------------------------------------- google calendar
if [ "${WANT_CALENDAR:-false}" = true ] && ! pi "[ -f ~/inky-dash/backend/data/google_token.json ]"; then
  say "Google Calendar access (one-time)"
  note "This needs a free Google Cloud OAuth client. In your browser:"
  note "  1. Go to https://console.cloud.google.com/ and create (or pick) a project"
  note "  2. 'APIs & Services' -> 'Library' -> enable the 'Google Calendar API'"
  note "     (for the photo frame panel also enable the 'Photos Picker API', and the"
  note "      'Google Drive API' if you want it to watch a Drive folder — one OAuth"
  note "      client serves all of them)"
  note "  3. 'APIs & Services' -> 'Credentials' -> 'Create credentials' -> 'OAuth client ID'"
  note "     -> application type 'Desktop app'"
  note "     (if asked to configure a consent screen first: choose 'External', fill in"
  note "      only the required fields, and add your own Google account as a test user."
  note "      Note: while the app stays in 'Testing', Google expires the sign-in after"
  note "      7 days — set the publishing status to 'In production' to avoid that.)"
  note "  4. Download the client JSON and save it as:"
  note "     $(pwd)/backend/data/google_credentials.json"
  while [ ! -f backend/data/google_credentials.json ]; do
    ask "Press Enter when the file is in place (or type 'skip' to do this later)" ""
    if [ "$REPLY" = skip ]; then break; fi
  done
  if [ -f backend/data/google_credentials.json ]; then
    note "opening your browser to sign in…"
    (cd backend && uv run python -m inky_dash.google_auth)
    scp -q backend/data/google_credentials.json backend/data/google_token.json "$PI_HOST:~/inky-dash/backend/data/"
    pi "chmod 600 ~/inky-dash/backend/data/google_*.json"
    pi_sudo "sudo systemctl restart inky-dash"
    note "calendar connected"
    note "for the photo frame, add the Photos panel in the web UI and press"
    note "'Connect' there — no need to come back to this script"
  else
    note "skipped — re-run ./setup.sh any time to add it"
  fi
fi

# ---------------------------------------------------------------- verify
say "Waiting for the first frame (a colour e-ink refresh takes ~30 seconds)"
OK=false
for _ in $(seq 1 30); do
  if pi "curl -sf http://127.0.0.1/api/status 2>/dev/null" | grep -qv '"last_render":null'; then
    OK=true
    break
  fi
  printf '.'
  sleep 5
done
echo
$OK || die "the service did not render in time — check: ssh $PI_HOST journalctl -u inky-dash -n 50"

HOSTNAME_NOW=$(pi hostname | tr -d '\r\n')
say "Done — your dashboard is live"
note "Display: refreshing on the panel now"
note "Web UI:  http://$HOSTNAME_NOW  (or http://$HOSTNAME_NOW.local)"
note "Use the web UI to set your weather location and tweak panels."
note "To ship code changes later: ./deploy.sh"
