"""Start the office: agents in the background, pixel office at http://localhost:8000"""
import json
import os
import threading
import time

import uvicorn
from fastapi import Body, FastAPI, HTTPException
import csv
import html as htmllib
import io
import sys

import markdown as md
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from agency import agents, runner
from agency import llm
from agency.core import (AGENTS, CFG, KEY_NAMES, OUTPUT, ROOT, api_spent, day_start, kv_get, kv_set, kv_update,
                         log, month_start, q, save_key, streams, x)

app = FastAPI(title="Agent Office")
app.mount("/output", StaticFiles(directory=OUTPUT), name="output")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


NO_CACHE = {"Cache-Control": "no-store, must-revalidate"}


@app.get("/")
def index():
    # never cached, so a restart always shows the newest version of the office
    return FileResponse(ROOT / "static" / "index.html", headers=NO_CACHE)


def _product(p):
    p = dict(p)
    p["idea"] = json.loads(p["idea"]) if p.get("idea") else None
    p["listing"] = json.loads(p["listing"]) if p.get("listing") else None
    p["qa"] = json.loads(p["qa"]) if p.get("qa") else None
    if p.get("folder"):
        files = OUTPUT / p["folder"] / "files"
        p["files"] = sorted(f.relative_to(OUTPUT).as_posix() for f in files.rglob("*") if f.is_file()) if files.exists() else []
        p["zip"] = next((z.relative_to(OUTPUT).as_posix() for z in (OUTPUT / p["folder"]).glob("*.zip")), None)
        prev = OUTPUT / p["folder"] / "source" / "previews"
        p["previews"] = sorted(f.relative_to(OUTPUT).as_posix() for f in prev.glob("*.png"))[:6] if prev.exists() else []
    return p


VIEW_CSS = """body{font-family:system-ui,Segoe UI,sans-serif;max-width:960px;margin:30px auto;padding:0 20px;line-height:1.6;color:#222}
table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}td,th{border:1px solid #ccc;padding:6px 8px;vertical-align:top}
th{background:#f3f0fa;position:sticky;top:0}pre{background:#f6f6f6;padding:14px;overflow:auto}code{background:#f3f3f3}
.bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #ddd;margin-bottom:16px;font-size:13px;color:#666}"""


def _safe(rel):
    f = (OUTPUT / rel).resolve()
    if OUTPUT.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404, "File not found")
    return f


@app.get("/view", response_class=HTMLResponse)
def view(p: str):
    """Read a product file nicely in the browser: Markdown rendered, CSV as a table, code as text."""
    f = _safe(p)
    text = f.read_text(encoding="utf-8", errors="replace")
    if f.suffix.lower() == ".pdf":
        return FileResponse(f, media_type="application/pdf")
    if f.suffix.lower() == ".xlsx":
        from agency.formats import xlsx_preview_html
        body = "<p><i>Preview: formulas are shown as written; Excel / Google Sheets calculates them.</i></p>" + xlsx_preview_html(f)
        return f"<!doctype html><meta charset=utf-8><title>{htmllib.escape(f.name)}</title><style>{VIEW_CSS}</style>" \
               f"<div class=bar><a href='/'>← back to the office</a> · {htmllib.escape(p)} · " \
               f"<a href='/output/{htmllib.escape(p)}' download>download</a></div>{body}"
    if f.suffix.lower() == ".html":
        # live preview of a browser tool, locked down: no internet, no access to the office, no form posts
        csp = ("sandbox allow-scripts allow-modals allow-downloads allow-popups; default-src 'none'; "
               "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; "
               "form-action 'none'")
        return HTMLResponse(text, headers={"Content-Security-Policy": csp})
    if f.suffix.lower() == ".dyn":
        g = json.loads(text)
        code = next((n.get("Code", "") for n in g.get("Nodes", []) if "PythonNode" in n.get("ConcreteType", "")), "")
        rows = "".join(f"<tr><td>{htmllib.escape(i['Name'])}</td><td>{htmllib.escape(i['Type'])}</td>"
                       f"<td>{htmllib.escape(str(i['Value']))}</td><td>{htmllib.escape(i.get('Description', ''))}</td></tr>"
                       for i in g.get("Inputs", []))
        body = (f"<h1>{htmllib.escape(g.get('Name', f.stem))}</h1><p>{htmllib.escape(g.get('Description', ''))}</p>"
                "<h2>What the buyer sees in Dynamo Player</h2><table><tr><th>Field</th><th>Type</th><th>Default</th>"
                f"<th>Help text</th></tr>{rows}</table><h2>Script inside (for your review)</h2>"
                f"<pre>{htmllib.escape(code)}</pre>")
        return f"<!doctype html><meta charset=utf-8><title>{htmllib.escape(f.name)}</title><style>{VIEW_CSS}</style>" \
               f"<div class=bar><a href='/'>← back to the office</a> · {htmllib.escape(p)} · " \
               f"<a href='/output/{htmllib.escape(p)}' download>download</a></div>{body}"
    if f.suffix.lower() in {".docx", ".zip"}:
        return FileResponse(f, filename=f.name)
    if f.suffix.lower() == ".md":
        body = md.markdown(text, extensions=["tables", "fenced_code"])
    elif f.suffix.lower() == ".csv":
        rows = list(csv.reader(io.StringIO(text)))
        head = "".join(f"<th>{htmllib.escape(c)}</th>" for c in (rows[0] if rows else []))
        trs = "".join("<tr>" + "".join(f"<td>{htmllib.escape(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
        body = f"<table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table>"
    else:
        body = f"<pre>{htmllib.escape(text)}</pre>"
    return f"<!doctype html><meta charset=utf-8><title>{htmllib.escape(f.name)}</title><style>{VIEW_CSS}</style>" \
           f"<div class=bar><a href='/'>← back to the office</a> · {htmllib.escape(p)} · " \
           f"<a href='/output/{htmllib.escape(p)}' download>download</a></div>{body}"


@app.post("/api/products/{pid}/open_folder")
def open_folder(pid: int):
    p = q("select folder from products where id=?", (pid,), one=True)
    if not p or not p["folder"]:
        raise HTTPException(404, "No folder yet")
    folder = (OUTPUT / p["folder"]).resolve()
    if sys.platform == "win32":
        os.startfile(folder)  # opens Windows Explorer on this laptop
    return {"folder": str(folder)}


@app.get("/api/state")
def state():
    sales = q("select coalesce(sum(amount_eur),0) s, count(*) n from ledger where kind='sale'", one=True)
    sales_month = q("select coalesce(sum(amount_eur),0) s from ledger where kind='sale' and ts>=?",
                    (month_start(),), one=True)["s"]
    cost_total = api_spent(0)
    st = [dict(s, stats=agents.stream_stats(s["id"]),
               pipeline=q("select id,title,status from products where stream=? and status not in "
                          "('dropped','failed') order by id desc limit 6", (s["id"],)))
          for s in streams()]
    return {
        "demo": llm.demo_mode(),
        "providers": llm.provider_status(),
        "keys": {k: ("…" + os.environ[k][-4:]) if os.environ.get(k) else "" for k in KEY_NAMES},
        "last_model": kv_get("last_model"),
        "trial": trial_report(),
        "paused": kv_get("paused", False),
        "agents": {a["id"]: dict(a, **AGENTS[a["id"]]) for a in q("select * from agents")},
        "money": {"revenue": round(sales["s"], 2), "sales": sales["n"], "revenue_month": round(sales_month, 2),
                  "api_cost": round(cost_total, 2), "net": round(sales["s"] - cost_total, 2),
                  "spent_today": round(api_spent(day_start()), 3), "spent_month": round(api_spent(month_start()), 2),
                  "budget_day": CFG["budget"]["daily_eur"], "budget_month": CFG["budget"]["monthly_eur"]},
        "streams": st,
        "inbox": [dict(a, product=_product(q("select * from products where id=?", (a["product_id"],), one=True)))
                  for a in q("select * from approvals where status='pending' order by id")],
        "to_upload": [_product(p) for p in q("select * from products where status='approved' order by id")],
        "live": [_product(p) for p in q("select * from products where status='live' order by id desc")],
        "memo": kv_get("weekly_memo"),
        "proposals": [p for p in kv_get("dept_proposals", []) if p["status"] == "pending"],
        "dept_requests": kv_get("dept_requests", []),
        "want_proposal": kv_get("want_proposal", False),
        "events": q("select * from events order by id desc limit 40"),
    }


def _approval(aid):
    a = q("select * from approvals where id=? and status='pending'", (aid,), one=True)
    if not a:
        raise HTTPException(404, "No such pending approval")
    return a


@app.post("/api/approvals/{aid}/approve")
def approve(aid: int):
    a = _approval(aid)
    agents.package(a["product_id"])
    x("update approvals set status='approved', decided=? where id=?", (time.time(), aid))
    x("update products set status='approved', updated=? where id=?", (time.time(), a["product_id"]))
    log("master", f"Owner approved #{a['product_id']} — zipped and ready to upload")
    return {"ok": True}


@app.post("/api/approvals/{aid}/revise")
def revise(aid: int, body: dict = Body(...)):
    a = _approval(aid)
    x("update approvals set status='revise', decided=? where id=?", (time.time(), aid))
    x("update products set status='revise', feedback=?, updated=? where id=?",
      (body.get("note", ""), time.time(), a["product_id"]))
    log("master", f"Owner sent #{a['product_id']} back: {body.get('note', '')[:80]}")
    return {"ok": True}


@app.post("/api/approvals/{aid}/drop")
def drop(aid: int):
    a = _approval(aid)
    x("update approvals set status='dropped', decided=? where id=?", (time.time(), aid))
    x("update products set status='dropped', updated=? where id=?", (time.time(), a["product_id"]))
    log("master", f"Owner dropped #{a['product_id']}")
    return {"ok": True}


@app.post("/api/products/{pid}/live")
def mark_live(pid: int, body: dict = Body(...)):
    x("update products set status='live', url=?, updated=? where id=?", (body.get("url", ""), time.time(), pid))
    log("master", f"🎉 #{pid} is live!")
    return {"ok": True}


@app.post("/api/products/{pid}/remove")
def remove(pid: int):
    x("update products set status='dropped', updated=? where id=?", (time.time(), pid))
    log("master", f"Owner removed #{pid}")
    return {"ok": True}


@app.post("/api/sale")
def add_sale(body: dict = Body(...)):
    x("insert into ledger(ts,kind,stream,amount_eur,memo) values(?,?,?,?,?)",
      (time.time(), "sale", body.get("stream"), float(body["amount"]), body.get("memo", "manual")))
    log("accountant", f"💰 Logged a €{float(body['amount']):.2f} sale")
    return {"ok": True}


def trial_report():
    t = llm.trial_info()
    if not t["start"]:
        return None
    rows = q("select built_with, build_cost, qa from products where built_with is not null and created >= ?",
             (t["start"] - 14 * 86400,))
    def summary(group):
        scores = [json.loads(r["qa"]).get("score") for r in group if r["qa"]]
        scores = [s for s in scores if isinstance(s, (int, float))]
        return {"products": len(group), "avg_qa": round(sum(scores) / len(scores), 1) if scores else None,
                "cost": round(sum(r["build_cost"] or 0 for r in group), 2)}
    claude = [r for r in rows if (r["built_with"] or "").startswith("claude")]
    free = [r for r in rows if r["built_with"] and not r["built_with"].startswith("claude")]
    c = summary(claude)
    c["cost_per_product"] = round(c["cost"] / c["products"], 2) if c["products"] else None
    return dict(t, claude=c, free=summary(free), spent=round(api_spent(t["start"]), 2))


@app.post("/api/trial/{action}")
def trial_action(action: str):
    if action == "keep":
        kv_set("claude_keep", True); kv_set("claude_stopped", False)
        log("accountant", "Owner decided to keep Claude for the Builder")
    elif action == "stop":
        kv_set("claude_stopped", True); kv_set("claude_keep", False)
        log("accountant", "Owner stopped the Claude trial: back to free models only")
    elif action == "resume":
        kv_set("claude_stopped", False)
    return {"ok": True}


@app.post("/api/keys")
def set_key(body: dict = Body(...)):
    save_key(body["name"], body.get("value", ""))
    log("accountant", f"Updated {body['name'].split('_')[0].title()} connection")
    return {"ok": True}


@app.post("/api/test")
def test_providers():
    return llm.test_all()


@app.post("/api/departments")
def request_department(body: dict = Body(...)):
    name = (body.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "Give the department a name")
    req = {"name": name, "about": (body.get("about") or "").strip()[:500]}
    kv_update("dept_requests", lambda reqs: reqs + [req], [])
    log("master", f"Owner asked for a new department: {name}")
    return {"ok": True}


@app.post("/api/departments/suggest")
def suggest_department():
    kv_set("want_proposal", True)
    return {"ok": True}


@app.post("/api/proposals/{pid}/{decision}")
def decide_proposal(pid: int, decision: str):
    found = []

    def mark(props):
        for x in props:
            if x["id"] == pid and x["status"] == "pending":
                x["status"] = "accepted" if decision == "accept" else "declined"
                found.append(dict(x))
        return props
    kv_update("dept_proposals", mark, [])
    if not found:
        raise HTTPException(404, "No such proposal")
    if decision == "accept":
        agents.add_stream(found[0])
    else:
        log("master", f"Owner declined the department idea: {found[0]['name']}")
    return {"ok": True}


@app.post("/api/departments/{sid}/delete")
def delete_department(sid: str):
    kv_update("custom_streams", lambda c: [d for d in c if d["id"] != sid], [])
    log("master", f"Owner closed the department {sid}")
    return {"ok": True}


@app.post("/api/pause")
def pause():
    kv_set("paused", not kv_get("paused", False))
    return {"paused": kv_get("paused")}


@app.post("/api/streams/{sid}/toggle")
def toggle_stream(sid: str):
    paused = kv_get("paused_streams", [])
    paused.remove(sid) if sid in paused else paused.append(sid)
    kv_set("paused_streams", paused)
    return {"paused_streams": paused}


if __name__ == "__main__":
    threading.Thread(target=runner.loop, daemon=True).start()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  Agent Office -> http://localhost:{port}" + ("   (DEMO MODE until you connect a free AI key)" if llm.demo_mode() else "") + "\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
