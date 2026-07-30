"""
Dashboard Setup stage — connect real-time integrations before the desk.

Persists credentials to .streamlit/secrets.toml (gitignored).
Falls back to env vars for Docker / headless. Never logs secrets.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from spire_reactor.config.connectors import run_test, test_live_integrations
from spire_reactor.config.integrations import (
    is_demo_mode,
    is_setup_complete,
    live_integrations,
    load_credentials,
    mask_secret,
    phase2_integrations,
    secrets_path,
    write_secrets,
)


def _init_setup_state() -> None:
    if "setup_initialized" in st.session_state:
        return
    st.session_state.setup_initialized = True
    st.session_state.test_results = {}
    st.session_state.setup_flash = None
    # Form buffers — prefill from loaded credentials (secrets masked for secret fields)
    creds = load_credentials()
    for integ in live_integrations() + phase2_integrations():
        iid = integ["id"]
        for f in integ["fields"]:
            sk = f"setup_{iid}_{f['key']}"
            if sk not in st.session_state:
                val = (creds.get(iid) or {}).get(f["key"], f.get("default") or "")
                # Don't put raw secrets into default text for password widgets — leave empty if secret set
                if f.get("secret") and val:
                    st.session_state[sk] = ""
                    st.session_state[f"{sk}_has_saved"] = True
                else:
                    st.session_state[sk] = val
                    st.session_state[f"{sk}_has_saved"] = bool(val)


def _collect_form_fields(integ: dict[str, Any], existing: dict[str, str]) -> dict[str, str]:
    """Merge form inputs with previously saved secrets (blank secret field keeps existing)."""
    out: dict[str, str] = {}
    for f in integ["fields"]:
        key = f["key"]
        sk = f"setup_{integ['id']}_{key}"
        typed = str(st.session_state.get(sk, "") or "").strip()
        if f.get("secret"):
            if typed:
                out[key] = typed
            else:
                # Keep previously saved / env value
                out[key] = str(existing.get(key) or "").strip()
        else:
            out[key] = typed if typed != "" else str(existing.get(key) or f.get("default") or "")
    return out


def _status_dot(ok: bool | None, skipped: bool = False, phase2: bool = False) -> str:
    if phase2:
        return "⬜"
    if skipped:
        return "⚪"
    if ok is True:
        return "🟢"
    if ok is False:
        return "🔴"
    return "⚪"


def render_integration_status_strip() -> None:
    """Compact status chips for cockpit sidebar."""
    creds = load_credentials()
    results = st.session_state.get("test_results") or {}
    st.markdown("**Integrations**")
    cols = st.columns(4)
    live = live_integrations()
    for i, integ in enumerate(live):
        iid = integ["id"]
        r = results.get(iid)
        ok = r.get("ok") if r else None
        skipped = bool(r and r.get("skipped"))
        with cols[i % 4]:
            st.caption(f"{_status_dot(ok, skipped=skipped)} {integ['label'].split('(')[0].strip()}")
    if st.button("⚙ Open Setup", key="open_setup_from_strip", use_container_width=True):
        st.session_state.show_setup = True
        st.session_state.force_setup = True
        st.rerun()
    mode = "Demo" if is_demo_mode(creds) else "Live"
    done = "configured" if is_setup_complete(creds) else "not saved"
    st.caption(f"Mode: **{mode}** · Setup: {done}")


def render_setup_stage() -> None:
    """Full Setup stage UI. Call only when operator should configure integrations."""
    _init_setup_state()
    creds = load_credentials()

    st.markdown(
        """
<div style="background-color:#1a1a2e; padding:14px; border-radius:8px; margin-bottom:16px;">
<b>🔌 SETUP — Connect real-time integrations</b><br>
Enter logins for streams that require them. Credentials stay on this machine
(<code>.streamlit/secrets.toml</code> or env vars). Nothing is committed to git.
Demo mode needs <b>zero</b> logins.
</div>
""",
        unsafe_allow_html=True,
    )

    st.title("Connect real-time integrations")
    st.caption(
        "Open-Meteo needs no login. Snowflake is required for live SoR. "
        "Temporal, webhooks, and xAI are optional (durable fusion / notify / AI text)."
    )

    # Mode
    demo_default = is_demo_mode(creds)
    mode = st.radio(
        "Desk mode",
        ["Demo only (no logins required)", "Live integrations"],
        index=0 if demo_default else 1,
        horizontal=True,
        help="Demo uses public feeds + synthetic math. Live expects Snowflake for system-of-record.",
    )
    want_demo = mode.startswith("Demo")

    results = st.session_state.test_results
    live = live_integrations()
    tested_ok = sum(1 for i in live if (results.get(i["id"]) or {}).get("ok") is True)
    st.progress(tested_ok / max(len(live), 1), text=f"Live checks green: {tested_ok}/{len(live)}")

    if st.session_state.setup_flash:
        st.success(st.session_state.setup_flash)
        st.session_state.setup_flash = None

    # ── Live integration cards ───────────────────────────────────────
    st.subheader("Live streams")
    for integ in live:
        iid = integ["id"]
        existing = dict(creds.get(iid) or {})
        r = results.get(iid)
        title = f"{_status_dot(r.get('ok') if r else None, skipped=bool(r and r.get('skipped')))} {integ['label']}"
        with st.expander(title, expanded=integ.get("auth_required", False) and not want_demo):
            st.write(integ.get("description") or "")
            if integ.get("help_url"):
                st.markdown(f"[Registration / docs]({integ['help_url']})")

            if not integ.get("auth_required"):
                st.info("No login required.")

            cols = st.columns(2)
            for idx, f in enumerate(integ["fields"]):
                sk = f"setup_{iid}_{f['key']}"
                with cols[idx % 2]:
                    if f.get("secret"):
                        placeholder = (
                            f"Saved ({mask_secret(existing.get(f['key']))}) — leave blank to keep"
                            if existing.get(f["key"])
                            else "Paste key / password"
                        )
                        st.text_input(
                            f["label"],
                            type="password",
                            key=sk,
                            placeholder=placeholder,
                            help="Leave blank to keep the previously saved value.",
                        )
                    else:
                        st.text_input(f["label"], key=sk)

            c1, c2 = st.columns([1, 3])
            with c1:
                if st.button("Test connection", key=f"test_{iid}", use_container_width=True):
                    fields = _collect_form_fields(integ, existing)
                    st.session_state.test_results[iid] = run_test(str(integ["test"]), fields)
                    st.rerun()
            with c2:
                if r:
                    icon = "✅" if r.get("ok") else ("⏭" if r.get("skipped") else "❌")
                    st.caption(f"{icon} {r.get('message')} · {r.get('latency_ms')} ms")

    # ── Phase 2 (reserved for future slots) ──────────────────────────
    phase2 = phase2_integrations()
    if phase2:
        st.subheader("Phase 2 (coming soon)")
        st.caption("Fields can be saved for later; tests report not-wired.")
        for integ in phase2:
            iid = integ["id"]
            existing = dict(creds.get(iid) or {})
            with st.expander(f"⬜ {integ['label']}", expanded=False):
                st.write(integ.get("description") or "")
                cols = st.columns(2)
                for idx, f in enumerate(integ["fields"]):
                    sk = f"setup_{iid}_{f['key']}"
                    with cols[idx % 2]:
                        if f.get("secret"):
                            st.text_input(
                                f["label"],
                                type="password",
                                key=sk,
                                placeholder="Optional — save for later",
                            )
                        else:
                            st.text_input(f["label"], key=sk)
                if st.button("Test (Phase 2)", key=f"test_{iid}"):
                    fields = _collect_form_fields(integ, existing)
                    st.session_state.test_results[iid] = run_test(str(integ["test"]), fields)
                    st.rerun()
                r = results.get(iid)
                if r:
                    st.caption(f"{r.get('message')}")

    st.divider()

    # ── Actions ──────────────────────────────────────────────────────
    a1, a2, a3 = st.columns(3)

    with a1:
        if st.button("Test all live", use_container_width=True):
            # Build temp creds from form
            merged = load_credentials()
            for integ in live_integrations():
                iid = integ["id"]
                merged[iid] = _collect_form_fields(integ, dict(creds.get(iid) or {}))
            st.session_state.test_results = {
                **st.session_state.test_results,
                **test_live_integrations(merged),
            }
            st.rerun()

    with a2:
        if st.button("Save credentials", type="primary", use_container_width=True):
            sections: dict[str, dict[str, Any]] = {
                "app": {
                    "demo_mode": want_demo,
                    "setup_complete": True,
                }
            }
            for integ in live_integrations() + phase2_integrations():
                iid = integ["id"]
                section = str(integ["secrets_section"])
                fields = _collect_form_fields(integ, dict(creds.get(iid) or {}))
                # Only write non-empty sections
                if any(str(v).strip() for v in fields.values()):
                    sections[section] = fields
            path = write_secrets(sections)
            # Clear typed passwords from session after save
            for integ in live_integrations() + phase2_integrations():
                for f in integ["fields"]:
                    if f.get("secret"):
                        sk = f"setup_{integ['id']}_{f['key']}"
                        st.session_state[sk] = ""
                        st.session_state[f"{sk}_has_saved"] = True
            # Dismiss gate immediately so Streamlit secrets cache lag cannot re-show Setup
            st.session_state.setup_dismissed = True
            st.session_state.setup_flash = f"Saved to `{path}` (gitignored). Rerun picks up secrets."
            st.session_state.show_setup = False
            st.session_state.force_setup = False
            st.rerun()

    with a3:
        skip_sf = st.checkbox(
            "Skip Snowflake for now",
            value=want_demo,
            help="Allow entering the desk without a green Snowflake test (demo paths).",
        )
        continue_label = "Continue to desk (demo)" if want_demo else "Continue to desk"
        if st.button(continue_label, use_container_width=True):
            snow_ok = (results.get("snowflake") or {}).get("ok") is True
            if not want_demo and not snow_ok and not skip_sf:
                st.error(
                    "Live mode requires a green Snowflake connection test, "
                    "or check “Skip Snowflake for now”."
                )
            else:
                # Mark session as past setup even if not persisted
                st.session_state.setup_dismissed = True
                st.session_state.show_setup = False
                st.session_state.force_setup = False
                if want_demo:
                    st.session_state.demo_session = True
                st.rerun()

    st.caption(
        f"Secrets file: `{secrets_path()}` · "
        "Docker/prod: inject the same keys via `.env` (`SNOWFLAKE_*`, `EIA_API_KEY`, `REDIS_URL`)."
    )


def should_show_setup() -> bool:
    """Gate: show setup until completed, dismissed this session, or forced open."""
    if st.session_state.get("force_setup") or st.session_state.get("show_setup"):
        return True
    if st.session_state.get("setup_dismissed"):
        return False
    if is_setup_complete():
        return False
    return True
