# Launching on relayhealth.dev (Cloudflare)

## Connect GitHub to Cloudflare Pages (frontend, automatic on every save)

In the Cloudflare dashboard: **Workers & Pages → Create → Pages → Connect to Git**, authorize GitHub for
`notrestai/claudeimpactlab`, then:

| Setting | Value |
|---|---|
| Production branch | `claude/sleepy-planck-khriw0` (the project's main line today) |
| Framework preset | None |
| Build command | leave empty |
| Build output directory | `frontend` |

Save and deploy. Then **Custom domains → Set up a custom domain → relayhealth.dev**. Every save to the
project republishes the page within a minute. `frontend/config.js` already points the page at
`https://api.relayhealth.dev` when it is opened from relayhealth.dev or a `*.pages.dev` preview.

The backend cannot live on Pages (it is Python that renders PDFs and calls Claude), so it runs on a laptop
or a container host and is reached through a Cloudflare Tunnel at `api.relayhealth.dev`:

```bash
export ANTHROPIC_API_KEY=...
./deploy/launch.sh            # routes api.relayhealth.dev -> this laptop's backend
```


**Fastest path (five minutes, from the laptop that has the key):**

```bash
export ANTHROPIC_API_KEY=...        # or put it in .env
./deploy/launch.sh                  # backend on :8000 + Cloudflare Tunnel to relayhealth.dev
```

`deploy/launch.sh` installs the Python dependencies, creates the tunnel if needed, routes the domain to it,
starts the backend (which also serves the frontend) and opens the tunnel. Ctrl-C stops both.
A `Dockerfile` at the repo root runs the same thing on any container host if the demo should outlive a laptop.


The backend is Python (FastAPI, the Anthropic SDK, a PDF renderer). It does not run inside a Cloudflare
Worker, so the launch has two halves:

| Half | Where it runs | How it reaches the domain |
|---|---|---|
| Frontend (`frontend/`, static files) | Cloudflare Pages | `relayhealth.dev` custom domain on the Pages project |
| Backend (`backend/`, Python) | A laptop or a small server | Cloudflare Tunnel at `api.relayhealth.dev` |

Only the team lead puts anything live. Everything below is prepared so that takes a few minutes.

## 1. Backend, with a tunnel

On the machine that will serve the demo (it needs the packets in `data/packets/` and the API key):

```bash
pip install -r backend/requirements.txt
export ANTHROPIC_API_KEY=...            # never commit it; .env is ignored
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, with `cloudflared` installed and logged in to the Cloudflare account that owns relayhealth.dev:

```bash
cloudflared tunnel create relayhealth-api
cloudflared tunnel route dns relayhealth-api api.relayhealth.dev
cloudflared tunnel --config deploy/cloudflared.yml run relayhealth-api
```

`deploy/cloudflared.yml` points `api.relayhealth.dev` at `http://127.0.0.1:8000`. Fill in the tunnel id it prints.

## 2. Frontend on Pages

Set the API address the page should call, then publish the `frontend/` folder:

```bash
echo "window.SST_API_BASE = 'https://api.relayhealth.dev';" > frontend/config.js
npx wrangler pages project create relayhealth --production-branch main
npx wrangler pages deploy frontend --project-name relayhealth
```

Then in the Cloudflare dashboard: Pages, the `relayhealth` project, Custom domains, add `relayhealth.dev`.
`deploy/wrangler.toml` holds the same settings for `wrangler pages deploy` without flags.

## 3. CORS

The frontend on `relayhealth.dev` calls `api.relayhealth.dev`, a different origin. The backend allows it
through `SST_CORS_ORIGINS` (comma separated). Default allows `http://localhost:*` and `https://relayhealth.dev`.

## Simplest fallback for the demo

Skip Pages entirely: the FastAPI app already serves `frontend/` at `/`. Point one tunnel at port 8000 and
route `relayhealth.dev` itself to it:

```bash
cloudflared tunnel route dns relayhealth-api relayhealth.dev
```

One process, one URL, no CORS. This is the recommended path for today.

## What is not safe to put on a public URL

The service has no login; the role is a header the browser sends. On a public domain anyone with the URL
can read every referral. For the hackathon demo, either keep the tunnel up only during the demo, or add
Cloudflare Access in front of the hostname (Zero Trust, Access, Applications, self-hosted, require an
email one-time code). That takes about five minutes in the dashboard and needs no code change.
No real patient data is ever on the server: the packets are de-identified and stay on the machine's disk.
