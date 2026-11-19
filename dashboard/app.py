"""
OpenAlphaOperator dashboard entrypoint.

Flow:
  1. Setup stage — connect real-time integrations that need logins
  2. Real Time Desk — operator view on public feeds

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

# Host Streamlit parity with Docker: load .env so EIA_API_KEY / REDIS_URL work
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
    /* Hide the fixed Streamlit header so the title is not clipped. */
    header[data-testid="stHeader"] { display: none !important; }
    [data-testid="stHeader"] { display: none !important; }
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
        padding-top: 1.15rem !important;
        padding-bottom: 1.25rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 100% !important;
        background: #ffffff !important;
    }
    div[data-testid="stVerticalBlock"] { gap: 0.55rem !important; }
    div[data-testid="stHorizontalBlock"] { gap: 0.7rem !important; align-items: start !important; }

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
        margin-top: 0.2rem;
        margin-bottom: 0.2rem;
    }

    /* Captions slightly muted but still readable on white */
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p {
        color: #374151 !important;
    }

    section[data-testid="stSidebar"] .block-container { padding-top: 1rem; padding-bottom: 1rem; }

    /* Page title — Real Time Desk (native st.title → h1) */
    h1, [data-testid="stMarkdownContainer"] h1 {
        font-size: 1.55rem !important;
        font-weight: 800 !important;
        color: #0a0a0a !important;
        letter-spacing: -0.02em !important;
        line-height: 1.2 !important;
        margin: 0 0 0.15rem 0 !important;
        padding: 0.15rem 0 0 0 !important;
        overflow: visible !important;
    }
    /* Section titles (st.subheader / ###) */
    h2, h3,
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        font-size: 0.95rem !important;
        margin: 0.7rem 0 0.3rem 0 !important;
        color: #0a0a0a !important;
        font-weight: 800 !important;
    }
    div.block-container {
        padding-top: 1.15rem !important;
        padding-bottom: 0.8rem !important;
        max-width: 100% !important;
    }
    .desk-footer {
        margin: 0.55rem 0 0.45rem 0;
        padding: 8px 2px 0 2px;
        font-size: 0.78rem;
        font-weight: 600;
        color: #4b5563;
        letter-spacing: 0.01em;
        border-top: 1px solid #e5e7eb;
    }
    .desk-row {
        display: grid;
        gap: 0.65rem;
        margin: 0 0 0.65rem 0;
        align-items: stretch;
    }
    .desk-row-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .desk-row-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .desk-card {
        background: #ffffff;
        border: 1px solid #d1d5db;
        border-radius: 4px;
        padding: 8px 10px 8px 10px;
        min-height: 118px;
        height: 100%;
        display: flex;
        flex-direction: column;
        gap: 2px;
        overflow: hidden;
        box-sizing: border-box;
    }
    .desk-card-title {
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        color: #0a0a0a;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        line-height: 1.2;
    }
    .desk-card-sub {
        font-size: 0.68rem;
        font-weight: 600;
        color: #4b5563;
        white-space: normal;
        overflow: hidden;
        line-height: 1.25;
        min-height: 1.7rem;
        max-height: 1.7rem;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
    }
    .desk-card-value {
        font-size: 1.22rem;
        font-weight: 700;
        color: #0a0a0a;
        line-height: 1.25;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin: 0.2rem 0 0.15rem 0;
    }
    .desk-card-meta {
        font-size: 0.68rem;
        font-weight: 600;
        color: #374151;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-top: auto;
        line-height: 1.25;
    }
    .radar-head {
        font-size: 0.78rem;
        font-weight: 700;
        color: #0a0a0a;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        margin: 0 0 0.2rem 0;
        line-height: 1.2;
    }
    [data-testid="stCheckbox"] { margin: 0 0 0.25rem 0 !important; padding: 0 !important; min-height: 0 !important; }
    [data-testid="stCheckbox"] label p { font-size: 0.8rem !important; }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3 {
        font-weight: 800 !important;
        color: #0a0a0a !important;
    }

    /* Status strip */
    .desk-bar {
        display: flex; flex-direction: column; gap: 6px;
        background: #f8fafc; border: 1px solid #d1d5db; border-radius: 4px;
        padding: 7px 10px; margin: 0.15rem 0 0.65rem 0;
        font-size: 0.8rem; color: #0a0a0a;
    }
    .desk-bar-row {
        display: flex; flex-wrap: nowrap; align-items: center; gap: 8px;
        min-width: 0;
    }
    .desk-bar-clock {
        margin-left: auto;
        font-size: 0.72rem;
        font-weight: 600;
        color: #4b5563;
        white-space: nowrap;
    }
    .desk-bar .muted { color: #4b5563; font-weight: 600; }
    .desk-bar strong { color: #0a0a0a; font-weight: 800; }

    .desk-badge {
        display: inline-block; font-size: 0.7rem; font-weight: 700; letter-spacing: 0;
        padding: 3px 8px; border-radius: 3px; text-transform: none;
        border: 1px solid transparent; white-space: nowrap;
    }
    .desk-chip {
        display: inline-flex; align-items: baseline; gap: 0.3rem;
    }
    .desk-chip .chip-name { font-weight: 800; }
    .desk-chip .chip-asof {
        font-weight: 600; letter-spacing: 0; opacity: 0.88;
    }
    .grid-pill { margin-left: 0; }
    /* !important so badge colors beat any global span rules */
    .desk-badge.badge-lake { background: #dcfce7 !important; color: #14532d !important; border-color: #86efac !important; }
    .desk-badge.badge-ritual { background: #dbeafe !important; color: #1e3a8a !important; border-color: #93c5fd !important; }
    .desk-badge.badge-demo { background: #f3f4f6 !important; color: #111827 !important; border-color: #d1d5db !important; }
    .desk-badge.badge-green { background: #dcfce7 !important; color: #14532d !important; border-color: #86efac !important; }
    .desk-badge.badge-amber { background: #fef3c7 !important; color: #78350f !important; border-color: #fcd34d !important; }
    .desk-badge.badge-red,
    .desk-badge.badge-stale { background: #fee2e2 !important; color: #7f1d1d !important; border-color: #fca5a5 !important; }
    .desk-badge.badge-muted {
        background: #f3f4f6 !important; color: #374151 !important; border-color: #d1d5db !important;
        text-transform: uppercase;
    }
    .feed-list { display: flex; flex-direction: column; gap: 0.4rem; margin: 0.15rem 0 0.45rem 0; }
    .feed-row {
        display: flex; align-items: baseline; gap: 0.45rem;
        font-size: 0.8rem; line-height: 1.3;
        color: #0a0a0a;
    }
    .feed-row .feed-label { font-weight: 700; min-width: 4.6rem; white-space: nowrap; }
    .feed-row .feed-state { color: #374151; font-weight: 600; white-space: nowrap; }
    .feed-row.feed-ok .feed-dot { color: #15803d; }
    .feed-row.feed-synth .feed-dot { color: #b45309; }
    .feed-row.feed-muted .feed-dot { color: #9ca3af; }
    .feed-row.feed-bad .feed-dot { color: #b91c1c; }
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
