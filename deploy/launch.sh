#!/usr/bin/env bash
# One command to put the demo live from a laptop: backend on port 8000, Cloudflare Tunnel to relayhealth.dev.
# Needs: python3, cloudflared (brew install cloudflared), an Anthropic API key, Cloudflare login for the account that owns the domain.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-relayhealth.dev}"
TUNNEL="${TUNNEL_NAME:-relayhealth-api}"

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f .env ]; then set -a; . ./.env; set +a; fi
[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ANTHROPIC_API_KEY is not set (export it or put it in .env)"; exit 1; }
ls data/packets/R*.pdf >/dev/null 2>&1 || echo "note: no packets in data/packets/ (fictional example still works)"

python3 -m pip install -q -r backend/requirements.txt
command -v cloudflared >/dev/null || { echo "install cloudflared first: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"; exit 1; }
cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL" || cloudflared tunnel create "$TUNNEL"
cloudflared tunnel route dns "$TUNNEL" "$HOST" 2>/dev/null || true   # already routed is fine

echo "starting backend on 127.0.0.1:8000"
python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --log-level warning &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
sleep 2
echo "opening tunnel: https://$HOST -> http://127.0.0.1:8000  (Ctrl-C stops both)"
echo "reminder: no login on this app. Keep the tunnel up only during the demo, or add Cloudflare Access to $HOST."
cloudflared tunnel run --url http://127.0.0.1:8000 "$TUNNEL"
