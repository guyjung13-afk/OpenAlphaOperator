# OpenAlphaOperator

**Sovereign Gas Burn PCI + ETRM Reactor**  
Replaces the legacy Excel burn sheet + Power Automate chain with a deterministic operator ritual, Snowflake dynamic tables, and a live Streamlit dashboard.

## Layout

```
OpenAlphaOperator/
├── docker-compose.yml      # redis + dashboard (default); spire-reactor via --profile full
├── Dockerfile              # multi-stage, non-root; default CMD = Streamlit
├── requirements.txt
├── .env.example            # copy → .env (never commit secrets)
├── .streamlit/
│   └── secrets.toml.example  # or use Dashboard Setup stage
├── dashboard/
│   ├── app.py              # entry: Setup gate → Cockpit
│   ├── setup_stage.py      # connect real-time integration logins
│   └── cockpit.py          # Commercial Truth Cockpit
├── spire_reactor/
│   ├── main.py             # FastAPI + ritual engine (api | worker | trigger)
│   ├── config/
│   │   ├── integrations.py # catalog + secrets/env loader
│   │   └── connectors.py   # connection tests (Snowflake, EIA, Redis, …)
│   ├── ingest/             # public feeds
│   ├── workflows/          # Temporal rituals (scaffold)
│   └── activities/         # fusion / propagation (scaffold)
├── sql/                    # Snowflake streams, DTs, tasks
└── scripts/
```

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
| **Snowflake** | Yes (account / user / password) | Required for live (non-demo) path |
| **Temporal / webhooks / xAI** | Phase 2 | Fields saved for later; not wired yet |

- **Demo only** — continue with zero logins (synthetic + Open-Meteo).
- **Live integrations** — enter Snowflake (and optional EIA/Redis), **Test connection**, **Save credentials**.
- Saves to **`.streamlit/secrets.toml`** (gitignored). Docker uses **`.env`** with the same key names.
- From the cockpit sidebar, use **Open Setup** to reconfigure anytime.

```bash
# optional manual secrets template
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # Windows
# cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # macOS/Linux
```

## Quick start (reactor API + Redis)

```bash
cp .env.example .env   # then fill Snowflake / EIA if needed

# host-side API (optional; Compose default image runs Streamlit)
python -m spire_reactor.main --mode api
# → http://localhost:8000/health
# → http://localhost:8000/docs

# one-shot ritual (no server)
python -m spire_reactor.main --mode trigger --ritual gas_burn_update --payload "{\"heat_rate\":7.5,\"award_mw\":500,\"actual_burn_mmbtu\":3750}"

# Redis worker (needs Redis up; pre-Temporal)
# python -m spire_reactor.main --mode worker

# Public feeds demo (Open-Meteo; optional EIA_API_KEY) → gas burn ritual
python -m spire_reactor.main --mode ingest
python -m spire_reactor.main --mode ingest --synthetic   # offline / CI
python -m spire_reactor.main --mode ingest --no-ritual   # snapshot only

# or Docker stack (redis + dashboard by default)
docker compose up -d --build
# include Redis worker (spire-reactor):
docker compose --profile full up -d --build
```

### Public feeds (PoC instead of Snowflake)

`spire_reactor/ingest/public_feeds.py` pulls:

- **Open-Meteo** (no key) — weather → synthetic plant load / burn
- **EIA** (optional key via Setup secrets or `EIA_API_KEY`) — natural gas price when configured
- **Synthetic tick** — air-gapped / CI

Maps into the same `gas_burn_update` ritual payload as the operator form. Snowflake stays the live system-of-record path.

### Ritual stub (demo)

```bash
curl -s -X POST http://localhost:8000/ritual/operator-update \
  -H "Content-Type: application/json" \
  -d "{\"plant_id\":\"DEMO-1\",\"award_mmbtu\":1200,\"actual_burn_mmbtu\":1185,\"heat_rate\":8.45,\"notes\":\"shift check\"}"
```

## Snowflake

In Snowsight (or your preferred client), run the scripts in order:

1. `sql/01_base_ingestion_stream.sql`
2. `sql/02_pci_dynamic_table.sql`
3. `sql/03_etrm_fusion_view.sql`
4. `sql/04_propagation_task.sql`

Configure credentials via **Dashboard Setup** (secrets.toml) or `.env` (see `.env.example`).  
Legacy path: `spire_reactor/config/snowflake_creds.env.example`.

## Status

| Layer | State |
|-------|--------|
| Setup stage | Guided logins + connection tests (Snowflake, EIA, Redis); Phase 2 slots |
| Streamlit dashboard | Commercial Truth Cockpit (hybrid v1); Spire rituals + demo envelopes |
| Spire Reactor | Ritual engine + API + Redis worker (`--profile full`) |
| Public feeds ingest | Open-Meteo + optional EIA → ritual (`--mode ingest`) |
| Docker / Redis | Compose-ready (redis + dashboard) |
| Snowflake SQL | System-of-record scripts; apply in Snowsight |
| Temporal fusion | Scaffold (`workflows/` + `activities/`) |

## Security

- Prefer **Dashboard Setup** or copy `.env.example` → `.env` / `secrets.toml.example` → `secrets.toml`
- **Do not commit** real passwords or tokens
- `.gitignore` excludes `.env`, venvs, and `.streamlit/secrets.toml`
- Secrets are never written to ritual payloads or the session audit trail
- Container runs as non-root `appuser`
- Interactive desk = secrets.toml; headless API / Docker = env injection

Built for Guy Jung · OpenAlpha / Sovereign stack
