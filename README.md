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
├── .dockerignore
├── dashboard/
│   └── app.py              # Streamlit operator UI (demo via session_state)
├── spire_reactor/
│   ├── main.py             # FastAPI + ritual engine (api | worker | trigger)
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

## Quick start (reactor API + Redis)

```bash
cp .env.example .env   # then fill Snowflake / Temporal if needed

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
- **EIA** (optional `EIA_API_KEY`) — natural gas price when configured
- **Synthetic tick** — air-gapped / CI

Maps into the same `gas_burn_update` ritual payload as the operator form. Snowflake stays Phase 2 for system-of-record.

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

Configure connector credentials via `.env` (see `.env.example`).  
Legacy path: `spire_reactor/config/snowflake_creds.env.example`.

## Status

| Layer | State |
|-------|--------|
| Streamlit dashboard | Commercial Truth Cockpit (hybrid v1); Spire rituals + demo envelopes |
| Spire Reactor | Ritual engine + API + Redis worker (`--profile full`) |
| Public feeds ingest | Open-Meteo + optional EIA → ritual (`--mode ingest`) |
| Docker / Redis | Compose-ready (redis + dashboard) |
| Snowflake SQL | Phase 2 system-of-record; apply in Snowsight |
| Temporal fusion | Scaffold (`workflows/` + `activities/`) |

## Security

- Copy `.env.example` → `.env`; **do not commit** real passwords or tokens
- `.gitignore` excludes `.env`, venvs, and Streamlit secrets
- Container runs as non-root `appuser`

Built for Guy Jung · OpenAlpha / Sovereign stack
