"""Booking RPA control dashboard (no extra packages).  Run:  python dashboard.py   ->  http://127.0.0.1:8765"""
import json, os, re, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "screenshots"
PORT = 8765
PO = r"\d{6,12}"
DATE = r"\d{4}-\d{2}-\d{2}"


def pos(name, label, rx=None, ph=""):
    return dict(name=name, label=label, kind="pos", rx=rx, ph=ph, required=True)


def opt(name, label, default="", rx=None, ph=""):
    return dict(name=name, label=label, kind="opt", flag="--" + name.replace("_", "-"), default=default, rx=rx, ph=ph)


PLAN = pos("plan_ref", "Packing plan", PO, "609537759987")
COMMON = [PLAN, dict(opt("comment", "Comment / invoice", "", None, "Inv number"), required=True),
          dict(opt("date", "Cargo ready date", "", DATE, "2026-10-19"), required=True)]
TASKS = [
    dict(id="login", group="Booking", step="1", title="Log in", script="step1_login.py", params=[],
         desc="Opens Infor Nexus, logs in (or re-activates a timed-out session) and saves the session for the other tasks."),
    dict(id="menu", group="Booking", step="2", title="Open Vendor Booking", script="step2_create_booking.py", params=[],
         desc="CREATE menu, then Vendor Booking - By Packing Plan."),
    dict(id="plan", group="Booking", step="3", title="Select packing plan", script="step3_select_plan.py", params=[PLAN],
         desc="Filters the plan, ticks it, clicks Next and confirms the pop-up."),
    dict(id="ident", group="Booking", step="4", title="Fill Identification", script="step4_identification.py",
         params=COMMON + [opt("load_type", "Shipment load type", "LCL")],
         desc="Runs steps 1-3, then ticks shipper, writes the comment, sets dates and load type and clicks Next."),
    dict(id="items", group="Booking", step="5", title="Edit Items", script="step5_edit_items.py",
         params=COMMON + [opt("load_type", "Shipment load type", "LCL"), opt("invoice", "Invoice text (blank = comment)"), opt("season", "Season", "FW27-SMS"),
                          opt("product", "Product", "WOMEN'S T-SHIRT")],
         desc="Runs steps 1-4, then fills the first PO's Commodity Description and opens the PO named in the error list."),
    dict(id="cancel", group="Cancel", step="", title="Cancel bookings", script="step_cancel_booking.py", danger=True,
         params=[pos("po", "PO number", PO, "4502402036"), opt("status", "Status to cancel", "New"),
                 opt("max", "Maximum to cancel", "100", r"\d{1,4}"),
                 dict(name="dry_run", label="Dry run (list only, cancel nothing)", kind="flag", flag="--dry-run", default=True),
                 dict(name="yes", label="", kind="flag", flag="--yes", default=False, hidden=True)],
         desc="Finds every booking with this status for the PO, then Preview, Cancel, OK. A real run cannot be undone."),
    dict(id="check", group="Tools", step="", title="Check installation", script="check_install.py", params=[],
         desc="Tells you whether every script is the latest version."),
]
BYID = {t["id"]: t for t in TASKS}
RUNS, LOCK = {}, threading.Lock()


def build_args(task, vals):
    args = []
    for p in task["params"]:
        v = vals.get(p["name"], p.get("default"))
        if p["kind"] == "flag":
            if v: args.append(p["flag"])
            continue
        v = "" if v is None else str(v).strip()
        if not v:
            if p.get("required"): raise ValueError(p["label"] + " is required")
            continue
        if p.get("rx") and not re.fullmatch(p["rx"], v): raise ValueError(p["label"] + " has the wrong format")
        args.append(v if p["kind"] == "pos" else f'{p["flag"]}={v}')
    return args


def pump(run):
    proc, buf = run["proc"], b""
    while True:
        chunk = proc.stdout.read1(4096)
        if not chunk: break
        buf += chunk.replace(b"\r", b"")
        *lines, buf = buf.split(b"\n")
        for l in lines: run["lines"].append(l.decode("utf-8", "replace"))
        rest = buf.decode("utf-8", "replace")
        if re.search(r"Press Enter|Type YES", rest):                     # a prompt waiting for input
            run["lines"].append(rest); buf = b""; run["awaiting"] = True
            if "Press Enter" in rest and run["auto_close"]:
                threading.Timer(2, lambda: send(run, "")).start()
    if buf: run["lines"].append(buf.decode("utf-8", "replace"))
    run["code"] = proc.wait(); run["ended"] = time.time(); run["awaiting"] = False


def send(run, text):
    try:
        run["proc"].stdin.write((text + "\n").encode()); run["proc"].stdin.flush(); run["awaiting"] = False
    except Exception:
        pass


def start(task, vals, headless, auto_close):
    with LOCK:
        if any(r["code"] is None for r in RUNS.values()): raise RuntimeError("A task is already running")
        args = build_args(task, vals)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", HEADLESS="true" if headless else "false")
        kw = dict(creationflags=subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else {}
        proc = subprocess.Popen([sys.executable, "-u", task["script"]] + args, cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        rid = str(int(time.time() * 1000))
        run = dict(id=rid, task=task["id"], title=task["title"], cmd=" ".join(["python", task["script"]] + args),
                   started=time.time(), ended=None, code=None, lines=[], proc=proc, awaiting=False, auto_close=auto_close)
        RUNS[rid] = run
        threading.Thread(target=pump, args=(run,), daemon=True).start()
        return rid


def stop(run):
    if run["code"] is None:
        if os.name == "nt": subprocess.run(["taskkill", "/F", "/T", "/PID", str(run["proc"].pid)], capture_output=True)
        else: run["proc"].terminate()
        run["lines"].append("--- stopped from the dashboard ---")


def summary(r):
    st = "running" if r["code"] is None else ("done" if r["code"] == 0 else "failed")
    return dict(id=r["id"], task=r["task"], title=r["title"], cmd=r["cmd"], started=r["started"], ended=r["ended"],
                code=r["code"], status=st, awaiting=r["awaiting"])


import io, zipfile, datetime as _dt
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _esc

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
BATCH = {}


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data):
    """First sheet of an .xlsx as a list of rows (standard library only)."""
    z = zipfile.ZipFile(io.BytesIO(data))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si"):
            ss.append("".join(t.text or "" for t in si.iter(NS + "t")))
    sheet = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))[0]
    table = []
    for r in ET.fromstring(z.read(sheet)).iter(NS + "row"):
        row = []
        for c in r.iter(NS + "c"):
            i = _col(c.get("r")); row += [""] * (i + 1 - len(row))
            t, v = c.get("t"), c.find(NS + "v")
            if t == "s" and v is not None: val = ss[int(v.text)]
            elif t == "inlineStr": val = "".join(x.text or "" for x in c.iter(NS + "t"))
            else:
                val = v.text if v is not None and v.text else ""
                if t is None and val:
                    try:
                        f = float(val); val = str(int(f)) if f == int(f) else val
                    except ValueError:
                        pass
            row[i] = val
        table.append(row)
    return table


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


COLS = ["action", "plan_ref", "comment", "date", "load_type", "invoice", "season", "product", "po", "status", "max"]
EXAMPLES = [
    ["booking", "609537759987", "Inv number", "2026-10-19", "LCL", "", "FW27-SMS", "WOMEN'S T-SHIRT", "", "", ""],
    ["cancel", "", "", "", "", "", "", "", "4502402036", "New", "100"],
]


def route(vals):
    a = vals.pop("action", "").strip().lower()
    if a not in ("booking", "cancel"):
        a = "cancel" if vals.get("po") and not vals.get("plan_ref") else "booking"
    task = "cancel" if a == "cancel" else "items"
    names = {p["name"] for p in BYID[task]["params"] if not p.get("hidden")}
    return task, {k: v for k, v in vals.items() if k in names}


def rows_for_master(table):
    """One Excel format for all jobs. Returns (rows, ignored_columns); each row = {task, vals, error?}."""
    alias = {c: c for c in COLS}
    for tid in ("items", "cancel"):
        for p in BYID[tid]["params"]:
            if not p.get("hidden"):
                alias[_norm(p["name"])] = p["name"]; alias[_norm(p["label"].replace("*", ""))] = p["name"]
    alias.update(po_number="po", packing_plan="plan_ref", cargo_ready_date="date", shipment_load_type="load_type")
    head = [alias.get(_norm(h)) for h in table[0]]
    ignored = [str(h) for h, k in zip(table[0], head) if str(h).strip() and not k]
    out = []
    for r in table[1:]:
        vals = {k: str(v).strip() for k, v in zip(head, r) if k and str(v).strip()}
        if not vals: continue
        if re.fullmatch(r"\d{5}", vals.get("date", "")):                      # Excel date serial
            vals["date"] = (_dt.date(1899, 12, 30) + _dt.timedelta(days=int(vals["date"]))).isoformat()
        task, vals = route(vals)
        row = dict(task=task, vals=vals)
        try: build_args(BYID[task], {**({"dry_run": True} if task == "cancel" else {}), **vals})
        except ValueError as e: row["error"] = str(e)
        out.append(row)
    return out, ignored


def make_xlsx():
    """The single Excel template: header row + one booking example + one cancel example."""
    def row(n, vals):
        cells = "".join(f'<c r="{chr(65 + i)}{n}" t="inlineStr"><is><t>{_esc(str(v))}</t></is></c>' for i, v in enumerate(vals))
        return f'<row r="{n}">{cells}</row>'
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             + row(1, COLS) + "".join(row(i + 2, e) for i, e in enumerate(EXAMPLES)) + "</sheetData></worksheet>")
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels", f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{R}"><sheets><sheet name="Jobs" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def run_batch(b):
    for row in b["rows"]:
        if b["stop"]:
            row["state"] = "skipped"; continue
        row["state"] = "running"
        task = BYID[row["task"]]
        defaults = {"dry_run": b["dry_run"], "yes": not b["dry_run"]} if task.get("danger") else {}
        rid, ok = None, False
        for _ in range(120):
            try: rid = start(task, {**defaults, **row["vals"]}, b["headless"], b["auto_close"]); break
            except RuntimeError: time.sleep(1)
            except ValueError as e: row["error"] = str(e); break
        if rid is None:
            row["state"] = "failed"; row.setdefault("error", "could not start")
        else:
            row["run_id"] = b["current"] = rid
            while RUNS[rid]["code"] is None: time.sleep(0.5)
            ok = RUNS[rid]["code"] == 0; row["state"] = "done" if ok else "failed"
        if not ok and b["stop_on_fail"]: b["stop"] = True
    b["state"] = "stopped" if b["stop"] else "finished"


def start_batch(d):
    if BATCH.get("cur") and BATCH["cur"]["state"] == "running": raise RuntimeError("A batch is already running")
    rows = [dict(task=r["task"], vals=r["vals"], state="waiting") for r in d["rows"] if r["task"] in BYID]
    if not rows: raise ValueError("No rows to run")
    b = dict(rows=rows, dry_run=d.get("dry_run", True), headless=d.get("headless", False), auto_close=d.get("auto_close", True),
             stop_on_fail=d.get("stop_on_fail", True), state="running", stop=False, current=None)
    BATCH["cur"] = b
    threading.Thread(target=run_batch, args=(b,), daemon=True).start()
    return {"ok": True, "rows": len(rows)}


def stop_batch():
    b = BATCH.get("cur")
    if b and b["state"] == "running":
        b["stop"] = True
        if b.get("current") in RUNS: stop(RUNS[b["current"]])


def batch_view():
    b = BATCH.get("cur")
    if not b: return {"rows": [], "state": "none", "current": None}
    return {"state": b["state"], "current": b["current"],
            "rows": [dict(task=r["task"], vals=r["vals"], state=r["state"], run_id=r.get("run_id"), error=r.get("error")) for r in b["rows"]]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def out(self, obj, code=200, ctype="application/json", fname=None):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if fname: self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.end_headers(); self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query); p = u.path
        if p == "/":
            return self.out((ROOT / "dashboard.html").read_bytes(), ctype="text/html; charset=utf-8")
        if p == "/api/template":
            return self.out(make_xlsx(), ctype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", fname="booking_jobs_template.xlsx")
        if p == "/api/batch": return self.out(batch_view())
        if p == "/api/tasks": return self.out(TASKS)
        if p == "/api/runs":
            return self.out([summary(r) for r in sorted(RUNS.values(), key=lambda r: -r["started"])[:30]])
        if p.startswith("/api/run/"):
            r = RUNS.get(p.split("/")[-1])
            if not r: return self.out({"error": "unknown run"}, 404)
            since = int(q.get("since", ["0"])[0])
            return self.out(dict(summary(r), lines=r["lines"][since:], total=len(r["lines"])))
        if p == "/api/shots":
            files = sorted(SHOTS.glob("*.png"), key=lambda f: -f.stat().st_mtime)[:24] if SHOTS.exists() else []
            return self.out([dict(name=f.name, time=f.stat().st_mtime) for f in files])
        if p.startswith("/shots/"):
            f = SHOTS / Path(unquote(p[7:])).name
            if f.is_file() and f.suffix == ".png": return self.out(f.read_bytes(), ctype="image/png")
        self.out({"error": "not found"}, 404)

    def upload(self, p):
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            rows, ignored = rows_for_master(read_xlsx(data))
        except Exception as e:
            return self.out({"error": "Could not read the Excel file (use .xlsx): %s" % e}, 400)
        if not rows: return self.out({"error": "No data rows found. Row 1 must hold the column names."}, 400)
        self.out({"rows": rows, "ignored": ignored})

    def do_POST(self):
        p = self.path
        if p.startswith("/api/upload"): return self.upload(p)
        d = self.body()
        try:
            if p == "/api/run":
                t = BYID[d["task"]]
                return self.out({"id": start(t, d.get("values", {}), d.get("headless", False), d.get("auto_close", True))})
            if p == "/api/batch": return self.out(start_batch(d))
            if p == "/api/batch/stop": stop_batch(); return self.out({"ok": True})
            r = RUNS.get(p.split("/")[-1])
            if p.startswith("/api/stop/") and r: stop(r); return self.out({"ok": True})
            if p.startswith("/api/input/") and r: send(r, d.get("text", "")); return self.out({"ok": True})
        except (ValueError, KeyError) as e:
            return self.out({"error": str(e)}, 400)
        except RuntimeError as e:
            return self.out({"error": str(e)}, 409)
        self.out({"error": "not found"}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Dashboard running at http://127.0.0.1:{PORT}   (Ctrl+C to quit)")
    threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
