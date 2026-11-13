"""Booking RPA control panel on Streamlit.

Local:   streamlit run app.py
Hosted:  Streamlit Community Cloud (main file = app.py), see DEPLOY.md

It reuses the task engine in dashboard.py (task list, argument checks, run/stop, Excel batch)
and runs the existing step*.py scripts as subprocesses, so the automation code is unchanged.
"""
import hmac
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Booking RPA", page_icon="📦", layout="wide")


# ---------------------------------------------------------------- secrets / config
def secret(name, default=None):
    """st.secrets (hosted) first, then a plain environment variable (local / .env)."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:          # no secrets file at all
        pass
    return os.getenv(name, default)


# Hand the hosted secrets to the step scripts (they read os.environ; load_dotenv never overrides it).
for _k in ("NEXUS_USER", "NEXUS_PASS", "INFOR_URL", "SEASON", "PRODUCT_DESC", "AUTO_CONFIRM_POPUP"):
    _v = secret(_k)
    if _v:
        os.environ[_k] = _v


# ---------------------------------------------------------------- access gate
def gate():
    expected = secret("APP_PASSWORD")
    if not expected:
        st.error("APP_PASSWORD is not set. Add it under App settings -> Secrets "
                 "(or in .streamlit/secrets.toml locally). The app stays locked until then, "
                 "because it can create and cancel real bookings.")
        st.stop()
    if st.session_state.get("authed"):
        return
    st.title("📦 Booking RPA")
    pw = st.text_input("Access password", type="password")
    if st.button("Unlock", type="primary"):
        if hmac.compare_digest(pw.encode(), expected.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Wrong password.")
    st.stop()


gate()

# ---------------------------------------------------------------- engine (dashboard.py)
import dashboard as D  # noqa: E402  (module-level RUNS / BATCH live as long as the server process)

MISSING = [k for k in ("NEXUS_USER", "NEXUS_PASS", "INFOR_URL") if not os.getenv(k)]


@st.cache_resource(show_spinner="Installing the Chromium browser (first start only)...")
def ensure_browser():
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)[-2000:]


code, log = ensure_browser()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("📦 Booking RPA")
    headless = st.toggle("Headless browser", value=True,
                         help="Must stay ON on a hosted server (there is no screen). "
                              "Turn it off only when running on your own PC to watch the browser.")
    if MISSING:
        st.error("Missing secrets: " + ", ".join(MISSING))
    else:
        st.success("Login settings found")
    if code != 0:
        st.error("Chromium install failed")
        with st.expander("Details"):
            st.code(log)
    running = [r for r in D.RUNS.values() if r["code"] is None]
    st.metric("Task running", "Yes" if running else "No")
    if st.button("Lock"):
        st.session_state["authed"] = False
        st.rerun()


# ---------------------------------------------------------------- helpers
def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "-"


def param_widgets(task, key):
    vals = {}
    for p in task["params"]:
        if p.get("hidden"):
            continue
        k = f"{key}_{p['name']}"
        if p["kind"] == "flag":
            vals[p["name"]] = st.checkbox(p["label"], value=bool(p.get("default")), key=k)
        else:
            label = p["label"] + (" *" if p.get("required") else "")
            vals[p["name"]] = st.text_input(label, value=p.get("default", "") or "",
                                            placeholder=p.get("ph", ""), key=k)
    return vals


def launch(task, vals):
    try:
        rid = D.start(task, vals, headless, True)
        st.session_state["run_pick"] = rid
        st.toast(f"Started: {task['title']}")
    except (ValueError, RuntimeError) as e:
        st.error(str(e))


# ---------------------------------------------------------------- tabs
tab_run, tab_batch, tab_shots = st.tabs(["Run a task", "Excel batch", "Screenshots"])

# ----- single task
with tab_run:
    groups = {}
    for t in D.TASKS:
        groups.setdefault(t["group"], []).append(t)
    c1, c2 = st.columns([1, 2])
    with c1:
        group = st.radio("Group", list(groups), horizontal=True)
        task = st.selectbox("Task", groups[group],
                            format_func=lambda t: (f"Step {t['step']} - " if t["step"] else "") + t["title"])
    with c2:
        st.caption(task["desc"])
        vals = param_widgets(task, task["id"])

        if task.get("danger"):
            dry = vals.get("dry_run", True)
            if not dry:
                st.warning("REAL RUN: bookings will be cancelled and this cannot be undone.")
                typed = st.text_input("Type CANCEL to confirm", key="cancel_confirm")
                ok = typed.strip().upper() == "CANCEL"
            else:
                ok = True
            vals["yes"] = not dry
        else:
            ok = True

        if st.button("▶ Run", type="primary", disabled=not ok or bool(MISSING)):
            launch(task, vals)

# ----- batch
with tab_batch:
    st.write("One Excel file for all jobs: a `booking` row runs steps 1-5, a `cancel` row cancels by PO.")
    st.download_button("⬇ Download template", D.make_xlsx(), "booking_jobs_template.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    up = st.file_uploader("Upload filled template (.xlsx)", type=["xlsx"])
    if up is not None:
        try:
            rows, ignored = D.rows_for_master(D.read_xlsx(up.getvalue()))
        except Exception as e:
            rows, ignored = [], []
            st.error(f"Could not read the file: {e}")
        if rows:
            if ignored:
                st.info("Ignored columns: " + ", ".join(ignored))
            st.dataframe(
                [dict(row=i + 1, task=r["task"], **r["vals"], problem=r.get("error", "")) for i, r in enumerate(rows)],
                use_container_width=True, hide_index=True)
            good = [r for r in rows if "error" not in r]
            bad = len(rows) - len(good)
            if bad:
                st.warning(f"{bad} row(s) have problems and will be skipped.")
            b1, b2 = st.columns(2)
            dry_batch = b1.checkbox("Dry run for cancel rows (list only)", value=True)
            stop_fail = b2.checkbox("Stop at the first failed row", value=True)
            if not dry_batch and any(r["task"] == "cancel" for r in good):
                st.warning("Cancel rows will REALLY cancel bookings.")
                okb = st.text_input("Type CANCEL to confirm", key="batch_confirm").strip().upper() == "CANCEL"
            else:
                okb = True
            if st.button("▶ Run batch", type="primary", disabled=not good or not okb or bool(MISSING)):
                try:
                    D.start_batch(dict(rows=good, dry_run=dry_batch, headless=headless,
                                       auto_close=True, stop_on_fail=stop_fail))
                    st.toast("Batch started")
                except (ValueError, RuntimeError) as e:
                    st.error(str(e))

    @st.fragment(run_every=2)
    def batch_status():
        v = D.batch_view()
        if v["state"] == "none":
            return
        st.subheader(f"Batch: {v['state']}")
        icon = dict(waiting="⏳", running="🔄", done="✅", failed="❌", skipped="⏭")
        for i, r in enumerate(v["rows"], 1):
            line = f"{icon.get(r['state'], '')} {i}. {r['task']} - " + ", ".join(f"{k}={x}" for k, x in r["vals"].items())
            if r.get("error"):
                line += f"  ({r['error']})"
            st.write(line)
        if v["state"] == "running" and st.button("■ Stop batch"):
            D.stop_batch()

    batch_status()

# ----- screenshots
with tab_shots:
    shots = sorted(D.SHOTS.glob("*.png"), key=lambda f: -f.stat().st_mtime)[:12] if D.SHOTS.exists() else []
    if st.button("Refresh"):
        st.rerun()
    if not shots:
        st.caption("No screenshots yet. They appear here after a task runs.")
    cols = st.columns(3)
    for i, f in enumerate(shots):
        with cols[i % 3]:
            st.image(str(f), caption=f"{f.name} ({fmt_time(f.stat().st_mtime)})")

# ---------------------------------------------------------------- live log (all tabs)
st.divider()
st.subheader("Live log")


@st.fragment(run_every=1.5)
def live_log():
    runs = sorted(D.RUNS.values(), key=lambda r: -r["started"])[:30]
    if not runs:
        st.caption("Nothing has run yet.")
        return
    ids = [r["id"] for r in runs]
    if st.session_state.get("run_pick") not in ids:
        st.session_state["run_pick"] = ids[0]
    cur = st.selectbox("Run", ids, key="run_pick",
                       format_func=lambda i: f"{fmt_time(D.RUNS[i]['started'])}  {D.RUNS[i]['title']}  [{D.summary(D.RUNS[i])['status']}]")
    r = D.RUNS[cur]
    s = D.summary(r)
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.code(r["cmd"], language="bash")
    c2.metric("Status", s["status"])
    if s["status"] == "running":
        if c3.button("■ Stop", key="stop_run"):
            D.stop(r)
    else:
        c3.metric("Exit code", s["code"])
    if s["awaiting"]:
        reply = st.text_input("The script is waiting for input", key="awaiting_in")
        if st.button("Send", key="awaiting_send"):
            D.send(r, reply)
    st.code("\n".join(r["lines"][-400:]) or "(no output yet)", language=None)


live_log()
