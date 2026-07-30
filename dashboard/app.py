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
    page_title="Real Time Desk",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Light, high-contrast operator desk — white ground, black type.
# Prefer native Streamlit headings (h1/h2/h3) for titles so they always render;
# custom HTML classes are unreliable across Streamlit versions.
st.markdown(
    """
<style>
    html, body, [data-testid="stAppViewContainer"], .main, .stApp {
        background-color: #ffffff !important;
        color: #0a0a0a !important;
    }
    [data-testid="stHeader"] { background: #ffffff !important; }
    section[data-testid="stSidebar"] {
        background-color: #f8fafc !important;
        border-right: 1px solid #d1d5db;
    }
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span {
        color: #0a0a0a !important;
    }

    .block-container {
        /* Clear fixed Streamlit header so title is never clipped */
        padding-top: 2.75rem !important;
        padding-bottom: 1.25rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 100% !important;
        background: #ffffff !important;
    }
    /* Breathing room between major blocks without sparse waste */
    div[data-testid="stVerticalBlock"] { gap: 0.55rem; }
    div[data-testid="stHorizontalBlock"] { gap: 0.65rem; }

    /* Metrics — light tiles, black type */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #d1d5db;
        border-radius: 4px;
        padding: 10px 12px;
    }
    div[data-testid="stMetric"] label {
        font-size: 0.75rem !important;
        color: #1f2937 !important;
        font-weight: 700 !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.25rem !important;
        font-weight: 700 !important;
        color: #0a0a0a !important;
    }
    div[data-testid="stMetricDelta"] { font-size: 0.75rem !important; }

    .stTextInput, .stNumberInput, .stSelectbox, .stTextArea { margin-bottom: 0.15rem !important; }
    div[data-testid="stExpander"] {
        border: 1px solid #d1d5db;
        border-radius: 4px;
        background: #ffffff;
        margin-top: 0.35rem;
    }

    /* Captions slightly muted but still readable on white */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p {
        color: #374151 !important;
    }

    section[data-testid="stSidebar"] .block-container { padding-top: 1rem; padding-bottom: 1rem; }

    /* Page title — Real Time Desk (native st.title → h1) */
    h1, [data-testid="stMarkdownContainer"] h1 {
        font-size: 2.5rem !important;
        font-weight: 800 !important;
        color: #0a0a0a !important;
        letter-spacing: -0.02em !important;
        line-height: 1.15 !important;
        margin: 0.15rem 0 0.65rem 0 !important;
        padding: 0 !important;
    }
    /* Section titles (st.subheader / ###) */
    h2, h3,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        font-size: 1.05rem !important;
        margin: 0.65rem 0 0.4rem 0 !important;
        color: #0a0a0a !important;
        font-weight: 800 !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        font-weight: 800 !important;
        color: #0a0a0a !important;
    }

    /* Status strip */
    .desk-bar {
        display: flex; flex-wrap: wrap; align-items: center; gap: 12px 18px;
        background: #f8fafc; border: 1px solid #d1d5db; border-radius: 4px;
        padding: 10px 14px; margin: 0.25rem 0 0.65rem 0;
        font-size: 0.88rem; color: #0a0a0a;
    }
    .desk-bar .muted { color: #4b5563; font-weight: 600; }
    .desk-bar strong { color: #0a0a0a; font-weight: 800; }

    .desk-badge {
        display: inline-block; font-size: 0.7rem; font-weight: 800; letter-spacing: 0.04em;
        padding: 3px 8px; border-radius: 3px; text-transform: uppercase;
        border: 1px solid transparent;
    }
    /* !important so badge colors beat any global span rules */
    .desk-badge.badge-lake { background: #dcfce7 !important; color: #14532d !important; border-color: #86efac !important; }
    .desk-badge.badge-ritual { background: #dbeafe !important; color: #1e3a8a !important; border-color: #93c5fd !important; }
    .desk-badge.badge-demo { background: #f3f4f6 !important; color: #111827 !important; border-color: #d1d5db !important; }
    .desk-badge.badge-green { background: #dcfce7 !important; color: #14532d !important; border-color: #86efac !important; }
    .desk-badge.badge-amber { background: #fef3c7 !important; color: #78350f !important; border-color: #fcd34d !important; }
    .desk-badge.badge-red,
    .desk-badge.badge-stale { background: #fee2e2 !important; color: #7f1d1d !important; border-color: #fca5a5 !important; }
    .desk-bar .muted { color: #4b5563 !important; font-weight: 600; }
    .desk-bar strong { color: #0a0a0a !important; font-weight: 800; }

    .truth-tile {
        background: #ffffff; border: 1px solid #d1d5db; border-left-width: 4px;
        border-radius: 4px; padding: 12px 12px; min-height: 100px; height: 100%;
        margin-bottom: 0.25rem;
    }
    .truth-tile .t-label {
        font-size: 0.72rem; color: #0a0a0a; text-transform: uppercase;
        letter-spacing: 0.03em; font-weight: 800;
    }
    .truth-tile .t-value {
        font-size: 0.98rem; font-weight: 700; color: #0a0a0a; margin: 6px 0 4px 0;
        line-height: 1.3;
    }
    .truth-tile .t-sub { font-size: 0.75rem; color: #374151; line-height: 1.35; font-weight: 500; }

    hr { margin: 0.65rem 0 !important; border-color: #d1d5db !important; }

    [data-testid="stDataFrame"] { border: 1px solid #d1d5db; margin-top: 0.35rem; }

    /* Work-row breathing room */
    div[data-testid="column"] > div { padding-bottom: 0.15rem; }
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
