# OpenAlphaOperator

**Real Time Desk** — a Streamlit operator demo that runs on public data only.

The desk computes a deterministic gas-burn PCI / ETRM ritual from public feeds. Current connectors are Open-Meteo and optional EIA Open Data. Later public connectors (not in this tree yet) are FERC, PJM, and ERCOT.

## Layout

```
OpenAlphaOperator/
├── docker-compose.yml      # redis + dashboard (default); spire-reactor via --profile full
├── Dockerfile              # multi-stage, non-root; default CMD = Streamlit
├── requirements.txt
├── requirements-dev.txt    # pytest + ruff (CI / local)
├── pyproject.toml          # pytest / ruff / coverage config
├── .env.example            # copy → .env (never commit secrets)
├── .streamlit/
│   └── secrets.toml.example  # or use Dashboard Setup stage
├── .github/workflows/
│   ├── ci.yml              # lint + unit tests on push/PR
│   └── deploy.yml          # optional compose deploy (tags / manual)
├── dashboard/
│   ├── app.py              # entry: Setup gate → Cockpit
│   ├── setup_stage.py      # connect optional public-feed logins
│   └── cockpit.py          # Real Time Desk
├── spire_reactor/
│   ├── main.py             # FastAPI + ritual engine (api | worker | trigger | ingest)
│   ├── config/
│   │   ├── integrations.py # catalog + secrets/env loader
│   │   └── connectors.py   # connection tests (Open-Meteo, EIA, Redis, …)
│   ├── ingest/             # public feeds
│   ├── temporal/           # client, settings, worker
│   ├── workflows/          # PCI_ETRM_Operator_Update
│   └── activities/         # compute_and_land + fuse_pci_etrm
└── tests/                  # unit tests (no live Temporal)
```

## Public data

`spire_reactor/ingest/public_feeds.py` is the only data path:

| Source | Login | What it supplies |
|--------|--------|------------------|
| **Open-Meteo** | No | Weather → synthetic plant load / burn |
| **EIA Open Data** | Optional free API key | Natural gas price when configured |
| **Synthetic tick** | No | Offline / CI fallback (no network) |

Those fields map into the same `gas_burn_update` ritual payload as the operator form. The desk does not call a private warehouse or a private plant feed.

## Tests & CI

```bash
pip install -r requirements-dev.txt
pytest                 # unit suite
ruff check spire_reactor dashboard tests
```

GitHub Actions **CI** runs ruff + pytest on every push/PR to `main`. Deploy is separate (`workflow_dispatch` or version tags).

## Quick start (local dashboard)

```bash
# from repo root
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
streamlit run dashboard/app.py
```

Open **http://localhost:8501**

### Setup stage (first run)

On first launch the dashboard opens **Setup — Connect real-time integrations**:

| Stream | Login? | Notes |
|--------|--------|--------|
| **Open-Meteo** | No | Weather prefill |
| **EIA Open Data** | Optional API key | Free: https://www.eia.gov/opendata/ |
| **Redis** | Usually no (local) | URL; password in URL if needed |
| **Temporal** | Optional host (+ Cloud API key) | Durable `PCI_ETRM_Operator_Update` |
| **Webhooks / xAI** | Optional | Fusion notify + optional AI text |

- **Demo only** — continue with zero logins (synthetic + Open-Meteo).
- **Live integrations** — enter optional EIA / Redis / Temporal, **Test connection**, **Save credentials**.
- Saves to **`.streamlit/secrets.toml`** (gitignored). Docker uses **`.env`** with the same key names.
- From the cockpit sidebar, use **Open Setup** to reconfigure anytime.

```bash
# optional manual secrets template
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # Windows
# cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # macOS/Linux
```

## Quick start (reactor API + Redis)

```bash
cp .env.example .env   # EIA_API_KEY is optional

# host-side API (optional; Compose default image runs Streamlit)
python -m spire_reactor.main --mode api
# → http://localhost:8000/health
# → http://localhost:8000/docs

# one-shot ritual (no server)
python -m spire_reactor.main --mode trigger --ritual gas_burn_update --payload "{\"heat_rate\":7.5,\"award_mw\":500,\"actual_burn_mmbtu\":3750}"

# Redis worker (needs Redis up)
# python -m spire_reactor.main --mode worker

# Temporal worker (needs TEMPORAL_HOST — local dev server or Cloud)
# temporal server start-dev   # separate terminal, host localhost:7233
# python -m spire_reactor.main --mode temporal

# Public feeds (Open-Meteo; optional EIA_API_KEY) → gas burn ritual
python -m spire_reactor.main --mode ingest
python -m spire_reactor.main --mode ingest --synthetic   # offline / CI
python -m spire_reactor.main --mode ingest --no-ritual   # snapshot only

# or Docker stack (redis + dashboard by default)
docker compose up -d --build
# include Redis worker (spire-reactor):
docker compose --profile full up -d --build
# include Temporal worker (requires TEMPORAL_HOST in .env):
docker compose --profile temporal up -d --build
```

### Ritual example

```bash
curl -s -X POST http://localhost:8000/ritual/operator-update \
  -H "Content-Type: application/json" \
  -d "{\"plant_id\":\"DEMO-PUBLIC-1\",\"award_mmbtu\":1200,\"actual_burn_mmbtu\":1185,\"heat_rate\":8.45,\"notes\":\"shift check\"}"
```

Public snapshot and a one-shot ritual from the same feeds:

```bash
curl -s "http://localhost:8000/ingest/snapshot"
curl -s -X POST "http://localhost:8000/ingest/demo-ritual?synthetic=true"
```

## Temporal (optional durable ritual)

When Temporal is configured and dispatch is enabled, `gas_burn_update` starts
workflow **`PCI_ETRM_Operator_Update`** instead of running only in-process:

1. **Activity** `compute_and_land_gas_burn` — burn math + Redis publish
2. **Activity** `fuse_pci_etrm` — rule-based (optional xAI) insight, Redis, optional webhook

| Gate | Behavior |
|------|----------|
| No `TEMPORAL_HOST` | Local ritual only (default) |
| Host set + `DEMO_MODE=false` | Dispatch to Temporal |
| `TEMPORAL_USE=true` | Force dispatch even in demo |
| `TEMPORAL_USE=false` | Never dispatch |
| Worker down / start fails | **Falls back to local** ritual (`temporal_fallback` on response) |

```bash
temporal server start-dev

export TEMPORAL_HOST=localhost:7233
export TEMPORAL_USE=true
python -m spire_reactor.main --mode temporal

python -m spire_reactor.main --mode trigger --ritual gas_burn_update \
  --payload "{\"plant_id\":\"DEMO-PUBLIC-1\",\"heat_rate\":7.5,\"award_mw\":500,\"actual_burn_mmbtu\":3750}"
```

## Status

| Layer | State |
|-------|--------|
| Setup stage | Optional logins + connection tests (Open-Meteo, EIA, Redis, Temporal) |
| Streamlit dashboard | Real Time Desk on public feeds |
| Spire Reactor | Ritual engine + API + Redis worker (`--profile full`) |
| Public feeds | Open-Meteo + optional EIA → ritual (`--mode ingest`) |
| Docker / Redis | Compose-ready (redis + dashboard) |
| Temporal fusion | `PCI_ETRM_Operator_Update` workflow + worker (`--mode temporal`) |

## Security

- Prefer **Dashboard Setup** or copy `.env.example` → `.env` / `secrets.toml.example` → `secrets.toml`
- **Do not commit** real passwords or tokens
- `.gitignore` excludes `.env`, venvs, and `.streamlit/secrets.toml`
- Secrets are never written to ritual payloads or the session audit trail
- Container runs as non-root `appuser`
- Interactive desk = secrets.toml; headless API / Docker = env injection
