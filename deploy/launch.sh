#!/usr/bin/env bash
# One command to put the demo live from a laptop: backend on port 8000, Cloudflare Tunnel to relayhealth.dev.
# Needs: python3, cloudflared (brew install cloudflared), an Anthropic API key, Cloudflare login for the account that owns the domain.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-api.relayhealth.dev}"   # default: API only; the frontend comes from Cloudflare Pages. Pass relayhealth.dev to serve both from the laptop.
TUNNEL="${TUNNEL_NAME:-relayhealth-api}"

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f .env ]; then set -a; . ./.env; set +a; fi
[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ANTHROPIC_API_KEY is not set (export it or put it in .env)"; exit 1; }
ls data/packets/R*.pdf >/dev/null 2>&1 || echo "note: no packets in data/packets/ (fictional example still works)"

[ -d .venv ] || python3 -m venv .venv          # Macs refuse system-wide installs; use a project environment
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q --upgrade pip >/dev/null
python -m pip install -q -r backend/requirements.txt
command -v cloudflared >/dev/null || { echo "cloudflared is not installed. On a Mac: brew install cloudflared   then: cloudflared tunnel login"; exit 1; }
[ -f "$HOME/.cloudflared/cert.pem" ] || { echo "not logged in to Cloudflare yet. Run: cloudflared tunnel login   (pick the relayhealth.dev zone), then re-run this script"; exit 1; }
case "$ANTHROPIC_API_KEY" in sk-ant-*) ;; *) echo "warning: ANTHROPIC_API_KEY does not start with sk-ant-. The console's key ID (apikey_...) is not the key; the key is shown once at creation." ;; esac
cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL" || cloudflared tunnel create "$TUNNEL"
cloudflared tunnel route dns "$TUNNEL" "$HOST" 2>/dev/null || true   # already routed is fine

echo "starting backend on 127.0.0.1:8000"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level warning &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
sleep 2
echo "opening tunnel: https://$HOST -> http://127.0.0.1:8000  (Ctrl-C stops both)"
echo "reminder: no login on this app. Keep the tunnel up only during the demo, or add Cloudflare Access to $HOST."
cloudflared tunnel run --url http://127.0.0.1:8000 "$TUNNEL"
