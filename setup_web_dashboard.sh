#!/usr/bin/env bash
# setup_web_dashboard.sh — TempBot Web Dashboard via ttyd
# Streams the bot_dashboard tmux session to http://<EC2_IP>:8080
# Read-only: viewers cannot type, kill, or interact with the terminal.
set -euo pipefail

PORT=8080
SESSION="bot_dashboard"
TTYD_BIN="/usr/local/bin/ttyd"
TTYD_VER="1.7.7"
LOG="/tmp/ttyd.log"
PID_FILE="/tmp/ttyd.pid"

# ── Colours for script output ─────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
err()  { echo -e "${RED}  ✗${NC} $*"; }
hdr()  { echo -e "\n${CYAN}${BOLD}$*${NC}"; }

# ── Arch detection ────────────────────────────────────────────────────────────
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  TTYD_ASSET="ttyd.x86_64"  ;;
  aarch64) TTYD_ASSET="ttyd.aarch64" ;;
  armv7l)  TTYD_ASSET="ttyd.arm"     ;;
  *)       err "Unsupported arch: $ARCH"; exit 1 ;;
esac
TTYD_URL="https://github.com/tsl0922/ttyd/releases/download/${TTYD_VER}/${TTYD_ASSET}"

hdr "━━━  TempBot Web Dashboard Setup  ━━━"
echo -e "    ttyd ${TTYD_VER} · port ${PORT} · session '${SESSION}'"

# ── Step 1: install ttyd ──────────────────────────────────────────────────────
hdr "[1/4] ttyd binary"
if [[ -x "$TTYD_BIN" ]] && "$TTYD_BIN" --version 2>&1 | grep -qF "$TTYD_VER"; then
  ok "ttyd ${TTYD_VER} already installed at ${TTYD_BIN}"
else
  echo "    Downloading ${TTYD_ASSET} from GitHub..."
  if curl -fsSL --progress-bar -o "${TTYD_BIN}.tmp" "${TTYD_URL}"; then
    mv "${TTYD_BIN}.tmp" "${TTYD_BIN}"
    chmod +x "${TTYD_BIN}"
    VER_OUT=$("${TTYD_BIN}" --version 2>&1 || true)
    ok "Installed: ${VER_OUT}"
  else
    err "Download failed. Trying wget..."
    wget -q --show-progress -O "${TTYD_BIN}.tmp" "${TTYD_URL}"
    mv "${TTYD_BIN}.tmp" "${TTYD_BIN}"
    chmod +x "${TTYD_BIN}"
    ok "Installed via wget: $("${TTYD_BIN}" --version 2>&1 || true)"
  fi
fi

# ── Step 2: verify tmux session ───────────────────────────────────────────────
hdr "[2/4] tmux session"
if ! tmux has-session -t "${SESSION}" 2>/dev/null; then
  err "Session '${SESSION}' not found."
  echo "    Start it first:"
  echo "      tmux new-session -d -s ${SESSION} -x 220 -y 50 \\"
  echo "        \"cd ~/polymarket-temp-bot && python3 dashboard.py\""
  exit 1
fi
PANE_DIMS=$(tmux display-message -t "${SESSION}" -p '#{pane_width}x#{pane_height}' 2>/dev/null || echo "?")
ok "Session '${SESSION}' alive (${PANE_DIMS})"

# ── Step 3: stop existing ttyd ────────────────────────────────────────────────
hdr "[3/4] cleanup"
if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null && ok "Stopped old ttyd PID ${OLD_PID}"
    sleep 0.5
  fi
  rm -f "$PID_FILE"
fi
# Belt-and-braces: kill any stray ttyd on this port
pkill -f "ttyd.*${PORT}" 2>/dev/null && { sleep 0.3; ok "Killed stray ttyd on :${PORT}"; } || ok "No stray ttyd to kill"

# ── Step 4: launch ttyd ───────────────────────────────────────────────────────
hdr "[4/4] launching ttyd"

# Build launch command.
# --readonly      : keyboard completely disabled in browser (WebSocket level)
# -t titleFixed   : lock the browser tab title
# tmux attach -r  : read-only at tmux level (belt & suspenders)
nohup "${TTYD_BIN}" \
  --port "${PORT}" \
  --readonly \
  --ping-interval 30 \
  --max-clients 10 \
  -t "titleFixed=TempBot Monitor" \
  -t "disableReconnect=false" \
  -t "rendererType=canvas" \
  tmux attach-session -t "${SESSION}" -r \
  >"${LOG}" 2>&1 &

TTYD_PID=$!
echo "$TTYD_PID" > "$PID_FILE"

echo "    Waiting for ttyd to initialise..."
sleep 2

if ! kill -0 "$TTYD_PID" 2>/dev/null; then
  err "ttyd died immediately. Log:"
  cat "${LOG}"
  exit 1
fi
ok "ttyd running — PID ${TTYD_PID}"

# ── Resolve public IP ─────────────────────────────────────────────────────────
PUBLIC_IP=$(
  curl -s --max-time 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null ||
  curl -s --max-time 3 https://ifconfig.me                                  2>/dev/null ||
  echo "<EC2_PUBLIC_IP>"
)

# ── Auto-restart cron (survives reboots & crashes) ────────────────────────────
CRON_LINE="@reboot sleep 15 && ${TTYD_BIN} --port ${PORT} --readonly --ping-interval 30 -t titleFixed=TempBot tmux attach-session -t ${SESSION} -r >>${LOG} 2>&1 &"
( crontab -l 2>/dev/null | grep -v "ttyd"; echo "${CRON_LINE}" ) | crontab -
ok "Cron reboot entry added"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  ✅ Web dashboard live!${NC}"
echo -e "     URL   : ${BOLD}http://${PUBLIC_IP}:${PORT}${NC}"
echo -e "     PID   : ${TTYD_PID}"
echo -e "     Log   : ${LOG}"
echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${BOLD}  AWS Security Group — add inbound rule:${NC}"
echo -e "  ┌────────────────┬──────────┬───────────────────────────┐"
echo -e "  │ Type           │ TCP      │ Custom TCP Rule           │"
echo -e "  │ Protocol       │ TCP      │                           │"
echo -e "  │ Port range     │ ${PORT}    │                           │"
echo -e "  │ Source (phone) │ 0.0.0.0/0│ (open; restrict if needed)│"
echo -e "  │ Source (PC)    │ <YOUR_IP>/32 │ safer option          │"
echo -e "  └────────────────┴──────────┴───────────────────────────┘"
echo ""
echo -e "${BOLD}  Optional — add Basic Auth (strongly recommended if port is open):${NC}"
echo -e "    Kill current ttyd:"
echo -e "      kill ${TTYD_PID}"
echo -e "    Restart with password:"
echo -e "      ${TTYD_BIN} --port ${PORT} --readonly \\"
echo -e "        --credential admin:changeme \\"
echo -e "        tmux attach-session -t ${SESSION} -r &"
echo ""
echo -e "${BOLD}  To stop the web dashboard:${NC}"
echo -e "    kill \$(cat ${PID_FILE})"
echo ""
