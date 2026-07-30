# OpenAlphaOperator

**Sovereign Gas Burn PCI + ETRM Reactor**  
Replaces the legacy Excel burn sheet + Power Automate chain with a deterministic operator ritual, Snowflake dynamic tables, and a live Streamlit dashboard.

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
│   ├── setup_stage.py      # connect real-time integration logins
│   └── cockpit.py          # Commercial Truth Cockpit
├── spire_reactor/
│   ├── main.py             # FastAPI + ritual engine (api | worker | trigger)
│   ├── config/
│   │   ├── integrations.py # catalog + secrets/env loader
│   │   └── connectors.py   # connection tests (Snowflake, EIA, Redis, …)
│   ├── ingest/             # public feeds
│   ├── store/              # Snowflake landing (SoR write/read)
│   ├── temporal/           # client, settings, worker
│   ├── workflows/          # PCI_ETRM_Operator_Update
│   └── activities/         # compute_and_land + fuse_pci_etrm
├── tests/                  # unit tests (no live Snowflake/Temporal)
├── sql/                    # Landing DDL, streams, DTs, tasks
└── scripts/
```

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
| **Snowflake** | Yes (account / user / password) | Required for live (non-demo) path |
| **Temporal** | Optional host (+ Cloud API key) | Durable `PCI_ETRM_Operator_Update` |
| **Webhooks / xAI** | Optional | Fusion notify + optional AI text |

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

# Redis worker (needs Redis up)
# python -m spire_reactor.main --mode worker

# Temporal worker (needs TEMPORAL_HOST — local dev server or Cloud)
# temporal server start-dev   # separate terminal, host localhost:7233
# python -m spire_reactor.main --mode temporal

# Public feeds demo (Open-Meteo; optional EIA_API_KEY) → gas burn ritual
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

### 1. Apply DDL (Snowsight)

Run in order against your target database/schema (defaults: `ALPHAGEN_ETRM.GOLD`):

1. `sql/00_landing_operator_burn.sql` — **LANDING_OPERATOR_BURN_UPDATE** + **STAGING_GAS_BURN**
2. `sql/01_base_ingestion_stream.sql` — stream on staging
3. `sql/02_pci_dynamic_table.sql`
4. `sql/03_etrm_fusion_view.sql`
5. `sql/04_propagation_task.sql`

### 2. Credentials

Configure via **Dashboard Setup** (secrets.toml) or `.env` (see `.env.example`).  
Legacy path: `spire_reactor/config/snowflake_creds.env.example`.

### 3. Ritual landing write (live path)

`gas_burn_update` / operator-update appends a row to **LANDING_OPERATOR_BURN_UPDATE** when:

- Snowflake account/user/password are configured (not placeholders), and
- `DEMO_MODE=false` **or** `SNOWFLAKE_WRITE=true`

Optional dual-write maps the same ritual into **STAGING_GAS_BURN** (for the stream/DT path). **Off by default** until gas_m3 unit mapping is real — enable with `SNOWFLAKE_STAGING_WRITE=true`.

Ritual `mode` is **`live` only when landing succeeds**; otherwise `stub` (check the `snowflake` object for skip/fail detail).

Ritual response includes a safe `snowflake` object:

```json
{
  "ok": true,
  "load_id": "…",
  "landing_table": "ALPHAGEN_ETRM.GOLD.LANDING_OPERATOR_BURN_UPDATE",
  "staging_written": true,
  "message": "Landed load_id=…"
}
```

Demo mode skips the write (`skipped: true`) so local desks stay offline-safe.

```bash
# Live write test (needs real creds + DDL applied)
# DEMO_MODE=false  or  SNOWFLAKE_WRITE=true
python -m spire_reactor.main --mode trigger --ritual gas_burn_update \
  --payload "{\"plant_id\":\"LINDA-1\",\"heat_rate\":7.5,\"award_mw\":500,\"actual_burn_mmbtu\":3750,\"notes\":\"landing check\"}"
```

### 4. Cockpit / API SoR read

When Snowflake credentials are configured, the Commercial Truth Cockpit shows a
**Snowflake SoR** section that loads recent landing rows (filterable by plant).
Reads are allowed even in demo mode so you can verify historical landings.

API (reactor):

```bash
# After: python -m spire_reactor.main --mode api
curl -s "http://localhost:8000/sor/landing?limit=10"
curl -s "http://localhost:8000/sor/landing?limit=5&plant_id=LINDA-1"
```

Disable reads with `SNOWFLAKE_READ=false`.

## Temporal (durable PCI / ETRM)

When Temporal is configured and dispatch is enabled, `gas_burn_update` starts
workflow **`PCI_ETRM_Operator_Update`** instead of running only in-process:

1. **Activity** `compute_and_land_gas_burn` — burn math + Snowflake landing + Redis  
2. **Activity** `fuse_pci_etrm` — rule-based (optional xAI) insight, Redis, optional webhook  

| Gate | Behavior |
|------|----------|
| No `TEMPORAL_HOST` | Local ritual only (default) |
| Host set + `DEMO_MODE=false` | Dispatch to Temporal |
| `TEMPORAL_USE=true` | Force dispatch even in demo |
| `TEMPORAL_USE=false` | Never dispatch |
| Worker down / start fails | **Falls back to local** ritual (`temporal_fallback` on response) |

```bash
# 1) Temporal frontend (example local)
temporal server start-dev

# 2) Worker
export TEMPORAL_HOST=localhost:7233
export TEMPORAL_USE=true
python -m spire_reactor.main --mode temporal

# 3) Trigger (another shell) — waits for workflow result
python -m spire_reactor.main --mode trigger --ritual gas_burn_update \
  --payload "{\"plant_id\":\"LINDA-1\",\"heat_rate\":7.5,\"award_mw\":500,\"actual_burn_mmbtu\":3750}"
```

Setup stage: **Temporal**, **webhooks**, and **xAI** are live optional integrations with connection tests.

## Status

| Layer | State |
|-------|--------|
| Setup stage | Guided logins + connection tests (Snowflake, EIA, Redis); Phase 2 slots |
| Streamlit dashboard | Commercial Truth Cockpit (hybrid v1); Spire rituals + demo envelopes |
| Spire Reactor | Ritual engine + API + Redis worker (`--profile full`) |
| Public feeds ingest | Open-Meteo + optional EIA → ritual (`--mode ingest`) |
| Docker / Redis | Compose-ready (redis + dashboard) |
| Snowflake landing | Ritual → `LANDING_OPERATOR_BURN_UPDATE` (+ optional staging dual-write) |
| Snowflake SoR read | Cockpit table + `GET /sor/landing` when credentials configured |
| Snowflake SQL | Landing DDL + stream/DT/task scripts; apply in Snowsight |
| Temporal fusion | `PCI_ETRM_Operator_Update` workflow + worker (`--mode temporal`) |

## Security

- Prefer **Dashboard Setup** or copy `.env.example` → `.env` / `secrets.toml.example` → `secrets.toml`
- **Do not commit** real passwords or tokens
- `.gitignore` excludes `.env`, venvs, and `.streamlit/secrets.toml`
- Secrets are never written to ritual payloads or the session audit trail
- Container runs as non-root `appuser`
- Interactive desk = secrets.toml; headless API / Docker = env injection

Built for Guy Jung · OpenAlpha / Sovereign stack
