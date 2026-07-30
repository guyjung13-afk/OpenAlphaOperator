"""
OpenAlphaOperator dashboard entrypoint.

Flow:
  1. Setup stage — connect real-time integrations that need logins
  2. Commercial Truth Cockpit — desk operator view

Run from repo root:
  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Repo root on path when launched as `streamlit run dashboard/app.py`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Host Streamlit parity with Docker: load .env so SNOWFLAKE_*/EIA_API_KEY/REDIS_URL work
# without requiring secrets.toml (Docker injects env; interactive desk also uses Setup).
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:  # noqa: BLE001 — dotenv optional if env already set
    pass

st.set_page_config(
    page_title="AlphaGen • Commercial Truth Cockpit",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    .main { background-color: #0b1220; }
    .stMetric { background-color: #1e2937; border-radius: 8px; padding: 12px; }
</style>
""",
    unsafe_allow_html=True,
)

from dashboard.cockpit import render_cockpit  # noqa: E402
from dashboard.setup_stage import render_setup_stage, should_show_setup  # noqa: E402

if should_show_setup():
    render_setup_stage()
else:
    render_cockpit()
