"""The five agents. Each function = one job, at most a couple of Claude calls."""
import html
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import markdown
from pathlib import Path

from .core import CFG, OUTPUT, all_stream_defs, kv_get, kv_set, kv_update, log, q, status, stream, streams, x
from .llm import ask, ask_json
from .core import api_spent


def _base_system(role):
    o = CFG["owner"]
    return (f"You are {role} in a tiny one-person digital-products business owned by {o['name']}.\n"
            f"Owner profile:\n{o['profile']}\nHard rules:\n{o['rules']}\n"
            "Be concrete and realistic. Small finished products beat ambitious unfinished ones.")


def _clean_path(raw):
    """Models sometimes write 'file.md  — a short description' on the FILE line. Keep just a safe, short path
    (Windows refuses paths longer than 260 characters)."""
    p = raw.strip().strip("`*\"' ").replace("\\", "/").lstrip("/").replace("..", "")
    p = re.split(r"\s{2,}|\s[-–—:(]\s?|\s\(", p)[0].strip()      # cut trailing descriptions
    parts = []
    for part in p.split("/"):
        part = re.sub(r"[^\w.-]+", "_", part).strip("._")[:50]
        if part:
            parts.append(part)
    parts = parts[-3:]  # at most two folders deep
    return "/".join(parts) if parts and "." in parts[-1] else ("/".join(parts) + ".md" if parts else "file.md")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "product"


def stream_stats(sid):
    live = q("select * from products where stream=? and status='live'", (sid,))
    rev = q("select coalesce(sum(amount_eur),0) s, count(*) n from ledger where kind='sale' and stream=?",
            (sid,), one=True)
    cost = -q("select coalesce(sum(amount_eur),0) s from ledger where kind='api_cost' and stream=?",
              (sid,), one=True)["s"]
    first_live = min((p["updated"] for p in live), default=None)
    return {"live": len(live), "sales": rev["n"], "revenue": round(rev["s"], 2), "api_cost": round(cost, 2),
            "weeks_live": round((time.time() - first_live) / 604800, 1) if first_live else 0}


# ── SCOUT + MASTER: find an idea and decide ───────────────────────────────
def reddit_signals(queries, limit=8):
    """Free demand evidence: what real people ask about (no key needed). Fails quietly."""
    lines = []
    for qry in queries[:3]:
        url = "https://www.reddit.com/search.json?" + urllib.parse.urlencode(
            {"q": qry, "sort": "relevance", "t": "year", "limit": limit})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agent-office/1.0 (personal research)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                for c in json.load(r)["data"]["children"]:
                    d = c["data"]
                    lines.append(f"- r/{d['subreddit']} ({d['score']}↑, {d['num_comments']} comments): {d['title'][:140]}")
        except Exception:
            continue
        time.sleep(2)
    return "\n".join(lines[:24])


# Free web research (Tavily: free plan, no card). Used only when TAVILY_API_KEY is set; capped per day.
TAVILY_URL = "https://api.tavily.com/search"
FORMAT_OF = {".xlsx": "spreadsheet", ".docx": "document", ".pdf": "document", ".dyn": "Dynamo tool",
             ".html": "browser tool", ".py": "Python project", ".ipynb": "Python project"}


def web_search(query, include_domains=None, max_results=5):
    """One real web search. Returns [{title, url, snippet}]; [] when there's no key, the daily cap is hit or it fails."""
    import os
    from .core import day_start
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    cap = int(CFG.get("research", {}).get("web_search_daily_cap", 30))
    counter = f"calls:tavily:{int(day_start())}"
    if kv_get(counter, 0) >= cap:
        return []
    from .privacy import redact
    body = {"api_key": key, "query": redact(query)[:300], "max_results": max_results, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains
    req = urllib.request.Request(TAVILY_URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}",
                                          "User-Agent": "AgentOffice/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except Exception as e:
        log("accountant", f"Web search hiccup: {str(e)[:90]}")
        return []
    kv_set(counter, kv_get(counter, 0) + 1)
    return [{"title": (r.get("title") or "")[:140], "url": r.get("url", ""),
             "snippet": " ".join((r.get("content") or "").split())[:300]} for r in data.get("results", [])]


def web_research(queries):
    """A few targeted searches: general demand, what sells on marketplaces, and the free alternatives."""
    if not queries:
        return ""
    plan = [(queries[0], None, "Demand"),
            (queries[0], ["etsy.com", "gumroad.com"], "Marketplace listings"),
            (f"free {queries[0]} template OR plugin OR download", None, "Free alternatives")]
    if len(queries) > 1:
        plan.insert(1, (queries[1], None, "Demand"))
    out = []
    for qry, domains, label in plan:
        for r in web_search(qry, domains):
            out.append(f"- [{label}] {r['title']} | {r['url']} | {r['snippet']}")
    return "\n".join(out[:24])


def _formats(folder):
    files = OUTPUT / folder / "files" if folder else None
    if not files or not files.exists():
        return set()
    return {FORMAT_OF[f.suffix.lower()] for f in files.rglob("*") if f.suffix.lower() in FORMAT_OF}


def office_memory(sid, recent=40):
    """What the office has learned: quality per format and model, the owner's decisions, and what failed."""
    rows = q("select * from products order by id desc limit ?", (recent,))
    owner_dropped = {r["product_id"] for r in q("select product_id from approvals where status='dropped'")}
    scores = {}
    liked, rejected, shelved = [], [], []
    for r in rows:
        qa = json.loads(r["qa"]) if r["qa"] else {}
        score = qa.get("score")
        model = (r["built_with"] or "").split(":")[0] or None
        if score is not None and model:
            for f in _formats(r["folder"]) or {"unknown"}:
                scores.setdefault((f, model), []).append(int(score))
        if r["status"] in ("approved", "live"):
            liked.append(r["title"])
        elif r["status"] == "dropped" and r["id"] in owner_dropped:
            rejected.append(r["title"])
        elif r["status"] == "dropped" and score is not None and score < 5 and r["stream"] == sid:
            shelved.append(f"{r['title']} ({score}/10: {(qa.get('verdict') or '')[:110]})")
    quality = [f"{f} built with {m}: average {sum(v) / len(v):.1f}/10 over {len(v)}"
               for (f, m), v in sorted(scores.items(), key=lambda kv: -len(kv[1]))]
    return "\n".join([
        "Average review score by format and builder model: " + ("; ".join(quality[:10]) or "no data yet"),
        "Owner approved / published: " + ("; ".join(liked[:8]) or "nothing yet"),
        "Owner dropped (avoid anything similar): " + ("; ".join(rejected[:8]) or "nothing yet"),
        "Shelved in this department after failing review (avoid these shapes): " + ("; ".join(shelved[:6]) or "none"),
    ])


def _builder_now():
    """Which model would build right now, so ideas fit what can actually be made."""
    from .llm import chain, usable
    for entry in chain("builder"):
        if usable(entry.split(":")[0], entry):
            return entry
    return "unknown"


SCOUT_RULES = """SCOUT RULES (follow all of them):
1. HONEST EVIDENCE. Base each idea on the research above. For every claim give its source (a URL or a Reddit thread
   from the list) and label it "found" (seen in the research) or "assumed" (your judgement). Weak evidence is fine if
   it is labelled. NEVER invent sales numbers, review counts, prices or listings.
2. FREE-ALTERNATIVE CHECK. Name the best free option the buyer already has (built into their software, free
   templates, free plugins/packages) and say concretely why they would still pay. No convincing answer = drop the idea.
3. ONE JOB, DONE WELL. Each product solves ONE clear problem with at most 3 product files plus a Start Here guide.
   No all-in-one bundles.
4. BUILDABLE NOW. The Builder will use: {builder}. Prefer formats that score well with it (see office memory).
   On free models: spreadsheets and documents first, Dynamo tools for Revit users, browser tools only when small.
5. THREE GENUINELY DIFFERENT IDEAS: different buyer problems and, ideally, different formats.
6. LEARN FROM HISTORY. Nothing similar to what was already made, dropped by the owner, or shelved (office memory).
7. FOLLOW THE DEPARTMENT BRIEF and the owner's taste over generic trends.
8. REALISTIC PRICE: what comparable products cost, with the source if found.
Good example of the right size: "a Dynamo Player tool that renumbers doors by room for BIM coordinators"."""


def scout_and_pick(sid):
    import random
    s = stream(sid)
    existing = [p["title"] for p in q("select title from products where stream=?", (sid,))]
    stats = stream_stats(sid)
    memory = office_memory(sid)
    builder = _builder_now()

    status("scout", "working", f"Researching {s['name']}…")
    log("scout", f"Researching demand for “{s['name']}”")
    queries = s.get("research_queries", [])
    web = web_research(queries)
    if web:
        log("scout", f"Read {web.count(chr(10)) + 1} web results (demand, marketplaces, free alternatives)")
    signals = reddit_signals(queries)
    if signals:
        log("scout", f"Read {signals.count(chr(10)) + 1} real Reddit threads for demand clues")
    research = CFG.get("research", {})
    use_claude = research.get("use_claude_web_research", False)
    from .llm import usable
    scout_key = "researcher" if use_claude and usable("claude") else "scout"
    if scout_key == "researcher":
        log("scout", "Searching marketplaces (Etsy, Gumroad, forums) for proven demand")
    res = ask_json("scout", scout_key, _base_system("the Scout (market researcher)"), f"""
Department: {s['name']}  (sold via {s['channel']})
Brief: {s['brief']}
Already made in this department (don't repeat): {existing or 'nothing yet'}
Performance so far: {json.dumps(stats)}

OFFICE MEMORY
{memory}

WEB RESEARCH (title | url | snippet):
{web or '(no web search available: rely on Reddit and label your judgement as "assumed")'}

REDDIT THREADS (what people struggle with):
{signals or '(none fetched)'}

Propose 3 specific, finished digital products this department could sell.
{SCOUT_RULES.format(builder=builder)}
{"" if printables_allowed() else "Do NOT suggest designed printables (planners, wall art, signs, decorative pages)."}
Every idea must be a FINISHED product its buyer can use as-is: spreadsheets with formulas, print-ready PDF + Word
documents, offline browser tools (one HTML file) for non-technical buyers, Dynamo Player graphs for Revit users,
or complete runnable Python projects ONLY when the buyers are coders/data learners buying the code itself.
JSON: {{"ideas":[{{"title":"","buyer":"","problem":"","format":"spreadsheet|document|Dynamo tool|browser tool|Python project",
"files":"the max 3 product files + Start Here","evidence":"claims with sources, each marked found/assumed",
"sources":["urls or thread titles"],"free_alternative":"","why_pay_anyway":"","gap":"","price_eur":0,
"price_source":"","effort":"S|M","marketplace":"Etsy|Gumroad"}}]}}""", stream=sid,
        web_search=scout_key == "researcher", max_tokens=6000)
    ideas = [i for i in res.get("ideas", []) if isinstance(i, dict)][:3]
    if not ideas:
        raise ValueError("The Scout returned no usable ideas")
    random.shuffle(ideas)   # no first-in-the-list bias when Mira scores them
    status("scout", "walking", f"{len(ideas)} ideas for Mira!", target="master")

    status("master", "thinking", f"Scoring ideas for {s['name']}…")
    pick = ask_json("master", "master", _base_system("the Master agent (manager)"), f"""
The Scout proposes these ideas for {s['name']} (numbered from 0):
{json.dumps(list(enumerate(ideas)), indent=1)}
Department brief: {s['brief']}
Department performance: {json.dumps(stats)}
The Builder will use: {builder}
OFFICE MEMORY
{memory}

Score EVERY idea from 1 (poor) to 5 (excellent) on:
- evidence: real, sourced signs that buyers pay ("found" beats "assumed"; invented-looking claims score 1)
- gap: a clear reason to pay over the free alternative
- buildable: the Builder can fully make it as finished files in one go with the model above (use the memory's
  scores per format; files the Builder can't make, e.g. DWG/CAD, Revit families, video = 1)
- owner_fit: fits the department brief and the owner's past approvals; nothing like dropped or shelved items
- price: realistic price for what the buyer gets
Be critical; do not give everything 4s. For each idea also write precise build notes: buyer type and exactly which
spreadsheets (tabs, columns, key formulas), documents (sections), browser tools (inputs/outputs), Dynamo tools
(inputs, what OUT shows) or Python files to make.
JSON: {{"scores":[{{"idea":0,"evidence":1,"gap":1,"buildable":1,"owner_fit":1,"price":1,"note":"one sentence","build_notes":""}}]}}""",
        stream=sid, mock_key="master_pick")
    weights = {"evidence": 2, "gap": 1, "buildable": 2, "owner_fit": 1, "price": 1}

    def total(sc):
        t = 0
        for k, w in weights.items():
            try:
                t += w * max(1, min(5, int(sc.get(k, 1))))
            except (TypeError, ValueError):
                t += w
        return t
    scored = []
    for sc in pick.get("scores", []):
        try:
            i = int(sc.get("idea", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(ideas) and i not in {x_[0] for x_ in scored}:
            scored.append((i, total(sc), sc))
    if not scored:   # model ignored the format: fall back to its old-style answer or the first idea
        scored = [(int(pick.get("pick", 0)) % len(ideas), 0, {"build_notes": pick.get("build_notes", ""),
                                                             "note": pick.get("reason", "")})]
    scored.sort(key=lambda t: -t[1])
    best_i, best_total, best = scored[0]
    idea = ideas[best_i]
    idea["build_notes"] = best.get("build_notes", "")
    idea["mira_scores"] = {k: best.get(k) for k in weights} | {"total": best_total, "max": 5 * sum(weights.values())}
    pid = x("insert into products(stream,title,status,idea,created,updated) values(?,?,?,?,?,?)",
            (sid, idea["title"], "idea", json.dumps(idea), time.time(), time.time()))
    board = ", ".join(f"“{ideas[i]['title'][:38]}” {t}" for i, t, _ in scored)
    log("master", f"Greenlit #{pid} “{idea['title']}” ({best_total}/{5 * sum(weights.values())}) — "
                  f"{(best.get('note') or '')[:110]} | scores: {board}")
    status("master", "walking", f"Bo, build “{idea['title'][:40]}”", target="builder")


def files_digest(folder, total=24000):
    """Every file gets a fair share: its start and its end, plus its true size, so nothing looks 'empty'."""
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix != ".html")
    if not files:
        return "(no files)"
    share = max(1200, total // len(files))
    out = []
    for f in files:
        body = f.read_text(encoding="utf-8", errors="replace")
        name = f.relative_to(folder).as_posix()
        if len(body) <= share:
            out.append(f"--- {name} ({len(body)} chars, complete) ---\n{body}")
        else:
            head, tail = body[:share - 400], body[-400:]
            out.append(f"--- {name} ({len(body)} chars; showing start and end, middle omitted for length) ---\n"
                       f"{head}\n[... {len(body) - share} chars omitted ...]\n{tail}")
    return "\n".join(out)


# ── BUILDER: write the product files ─────────────────────────────────────
CSS = """body{font-family:Georgia,serif;max-width:780px;margin:40px auto;padding:0 20px;line-height:1.6;color:#222}
h1,h2,h3{font-family:Helvetica,Arial,sans-serif}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}
code,pre{background:#f4f4f4}pre{padding:12px;overflow:auto}@media print{body{margin:0}}"""


NO_CODE_RULES = """FINISHED-PRODUCT RULES (these override anything else, including the department brief):
- The product must be COMPLETE and usable as-is by its intended buyer, in tools that buyer already has.
  Never snippets, partial scripts, "starter code" or anything the buyer must finish, wire up or integrate.
- Pick the format for the BUYER:
  * non-technical buyers (hostels, shops, freelancers, students): spreadsheets with formulas, print-ready
    documents, or a BROWSER TOOL (APP block: one self-contained HTML file they double-click; works offline,
    no install). Never ask them to install Python or use a terminal.
  * Revit/BIM users: Dynamo Player graphs (DYNAMO block) for automation, plus documents/spreadsheets.
  * coders and data learners, when the code itself is what they're buying: a COMPLETE Python project
    (PYFILE blocks) that runs first time: sample data included, requirements.txt with versions, README with the
    exact install + run commands and what output to expect, clear comments, no TODOs.
- Anything that calculates or tracks for a non-technical buyer: spreadsheet or browser tool.
- Every Dynamo tool needs: an 'Apply changes' switch defaulting to false (preview first), a transaction around
  model changes, a readable result list in OUT, no hard-coded element ids, and Start Here steps for Dynamo Player.
  Dynamo Python is the PythonNet3 engine (Dynamo 3.x/4.x, Revit 2025+): get the document with
  clr.AddReference('RevitServices'); from RevitServices.Persistence import DocumentManager;
  doc = DocumentManager.Instance.CurrentDBDocument; wrap changes in TransactionManager.Instance.EnsureInTransaction(doc)
  / TransactionTaskDone() (no arguments). Never use __revit__ (pyRevit only) or ElementId.IntegerValue (removed:
  use .Value). Wrap .NET collections in list(...) before indexing or
  appending, use enum members (not integers), and call ToDSType/UnwrapElement where needed.
- Planners, worksheets, signs, CV/resume templates, cards and wall printables are PRINTABLE blocks: a designed
  page in HTML/CSS (beautiful, on-trend, consistent style, generous white space, print-safe colours), printed to PDF.
- Browser tools: fully self-contained (inline CSS/JS, no internet requests), clear labels, works on phone and
  desktop, a 'Print / save as PDF' option where useful, and if it saves data in the browser, say so and offer
  a 'clear data' button.
- PRIVACY & GDPR BY DESIGN: use only clearly fictional example data (names like "Guest Name" or "Anna Example",
  e-mails @example.com, phones like +45 00 00 00 00). Never real people, real customer data, real ID numbers.
  If a spreadsheet, template or tool stores personal data (guests, staff, customers), collect only what's needed,
  add retention guidance, and include a short "Privacy note" in Start Here. Guest-facing message templates
  include a placeholder line pointing to the business's own privacy policy. Products about laws, tax or GDPR
  say clearly they are general guidance, not legal advice. Tools never send data over the network.
- Formulas must work in both Excel and Google Sheets: use SUM, SUMIF(S), COUNTIF(S), AVERAGE, IF, IFERROR, AND, OR,
  ROUND, MIN, MAX, VLOOKUP, INDEX, MATCH, TODAY, TEXT, DATEDIF. No LET, LAMBDA, XLOOKUP, array formulas or macros."""

OUTPUT_FORMAT = """Output ONLY blocks in these formats, nothing outside them:

=== DOC: Start Here ===
<Markdown: what's inside, how to use it step by step, license (use on own projects/business, no resale)>
=== END ===

=== DOC: <Document title> ===
<Markdown: headings, paragraphs, bullet lists, numbered steps, "- [ ]" checkboxes, tables>
=== END ===
(becomes a print-ready PDF + an editable Word file)

=== SHEET: <Spreadsheet title> ===
{"title": "...", "instructions": "plain-language steps shown on a 'How to use' tab",
 "sheets": [{"name": "Tab name", "notes": "optional short tip",
   "columns": [{"header": "Item", "format": "text", "width": 22},
               {"header": "Amount", "format": "eur", "input": true},
               {"header": "Total", "format": "eur"}],
   "rows": [["Example item", 120, "=B2*1.25"], ["", "", "=B3*1.25"]],
   "validation": [{"column": 0, "options": ["Option A", "Option B"]}]}]}
=== END ===
(becomes an .xlsx file. Row 1 is the header, data starts at row 2, so formulas use A1-style references
accordingly. Cross-tab references look like 'Tab name'!B2. Formats: text, number, integer, eur, dkk, usd,
percent, date, time. Mark columns the buyer types into with "input": true (shown yellow). Include realistic
example rows AND empty pre-formulated rows (e.g. 20) the buyer can fill. "column" in validation is 0-based.)

=== DYNAMO: <Tool title> ===   (only for Revit/BIM tools)
{"title": "...", "description": "one line", "revit_versions": "2025 and newer", "engine": "PythonNet3",
 "inputs": [{"name": "Prefix", "type": "string", "default": "R-", "description": "shown in Dynamo Player"},
            {"name": "Apply changes", "type": "boolean", "default": false, "description": "Off = preview only"}],
 "python": "full Python for the Dynamo Python Script node; read inputs as IN[0], IN[1]...; set OUT to a list of readable lines"}
=== END ===
(becomes a ready .dyn graph: the inputs appear as fields in Dynamo Player, connected to the script.
Types: string, number, boolean. Use RevitServices DocumentManager / TransactionManager.)

=== PRINTABLE: <Title> ===   (designed pages: planners, worksheets, signs, CVs, cards, wall art)
<one complete HTML document with inline CSS: @page { size: A4; margin: 0 } (or Letter/A3/A5 as fits), one
.page div per printed page with page-break-after, system fonts only ('Segoe UI', Georgia, Arial...), inline SVG
for decoration, no images or links from the internet, no scripts>
=== END ===
(printed to a high-quality PDF; a preview image is made for the listing photos)

=== APP: <Tool title> ===   (browser tool for non-technical buyers)
<one complete, self-contained HTML page with inline CSS and JavaScript>
=== END ===

=== PYFILE: <project-folder>/<path/file.ext> ===   (only for coder/data-learner products; one block per file:
<file content>                                    .py, .ipynb, requirements.txt, README.md, sample .csv/.json)
=== END ===

Optional: === FILE: name.csv === for a plain data table. Nothing else."""


def printables_allowed():
    return CFG.get("formats", {}).get("allow_printables", False)


def builder_rules():
    rules, fmt = NO_CODE_RULES, OUTPUT_FORMAT
    if not printables_allowed():
        rules = re.sub(r"- Planners, worksheets, signs, CV/resume templates.*?printed to PDF\.\n", "", rules, flags=re.S)
        rules += ("\n- Do NOT make designed printables (planners, wall art, signs, decorative pages). CV/resume "
                  "templates are Word documents (DOC blocks).")
        fmt = re.sub(r"=== PRINTABLE:.*?\(printed to a high-quality PDF; a preview image is made for the listing photos\)\n\n",
                     "", fmt, flags=re.S)
    return rules, fmt


def _title_to_name(t):
    t = re.sub(r"[^\w &()+,.-]+", "", t).strip(" .")
    return (t[:60] or "Document")


def build(product):
    from .formats import (CODE_SUFFIXES, app_problems, dynamo_problems, html_preview_png, html_to_pdf, md_to_docx,
                          md_to_html, md_to_pdf, printable_problems, python_project_problems, secure_printable,
                          spec_problems, spec_to_dyn, spec_to_xlsx)
    idea = json.loads(product["idea"])
    s = stream(product["stream"])
    revising = product["status"] == "revise"
    status("builder", "working", ("Revising" if revising else "Building") + f" “{product['title'][:40]}”")
    log("builder", ("Revising" if revising else "Building") + f" #{product['id']} “{product['title']}”")

    feedback = ""
    if revising and product.get("folder"):
        base = OUTPUT / product["folder"]
        prev = base / "source" if (base / "source").exists() else base / "files"
        prev_txt = files_digest(prev, total=30000) if prev.exists() else "(none)"
        feedback = (f"\nThis is a REVISION. Your previous version (excerpts):\n{prev_txt}\n\n"
                    f"FEEDBACK — fix exactly this, keep everything that was already good:\n{product['feedback']}\n"
                    "Output the complete revised product (every block, full content).\n")
    spent_before = api_spent(0)
    rules, out_format = builder_rules()
    text = ask("builder", "builder", _base_system("the Builder") + "\n" + rules, f"""
Build this digital product completely, ready to sell. Department: {s['name']}.
Idea: {json.dumps(idea, indent=1)}{feedback}

{out_format}

Content must be original, specific and genuinely useful: no filler, no "insert text here" placeholders
(empty input cells in spreadsheets are fine).""",
               stream=product["stream"], max_tokens=CFG["loop"].get("builder_max_tokens", 12000), mock_key="builder")

    blocks = re.findall(r"=== (DOC|SHEET|DYNAMO|PRINTABLE|APP|PYFILE|FILE): (.+?) ===\s*\n(.*?)\n=== END ===", text, re.S)
    if not blocks:
        if len(text) < 800:
            raise ValueError("Builder returned almost nothing")
        blocks = [("DOC", product["title"], text)]  # model ignored the format: keep it as one document
    folder = OUTPUT / product["stream"] / f"{product['id']:03d}-{_slug(product['title'])[:30]}"
    if folder.exists():
        shutil.rmtree(folder)
    files_dir, src_dir = folder / "files", folder / "source"
    files_dir.mkdir(parents=True)
    src_dir.mkdir()
    made, problems, skipped, pyproject = [], [], [], {}
    for kind, name, content in blocks:
        name = name.strip().strip("`*\"' ")
        if kind == "DOC":
            fname = _title_to_name(name)
            (src_dir / f"{fname}.md").write_text(content, encoding="utf-8")
            if md_to_pdf(content, files_dir / f"{fname}.pdf", title=name) == "browser" and fname != "Start Here":
                html_preview_png(md_to_html(content, name), src_dir / "previews" / f"{fname}.png", 794, 1123)
            md_to_docx(content, files_dir / f"{fname}.docx")
            made += [f"{fname}.pdf", f"{fname}.docx"]
        elif kind == "SHEET":
            fname = _title_to_name(name)
            try:
                from .llm import extract_json
                spec = extract_json(content)
            except Exception:
                raise ValueError(f"spreadsheet '{name}' was not valid JSON")
            (src_dir / f"{fname}.sheet.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
            problems += [f"{fname}.xlsx: {p}" for p in spec_problems(spec)]
            spec_to_xlsx(spec, files_dir / f"{fname}.xlsx")
            made.append(f"{fname}.xlsx")
        elif kind == "DYNAMO":
            fname = _title_to_name(name)
            try:
                from .llm import extract_json
                spec = extract_json(content)
            except Exception:
                raise ValueError(f"Dynamo tool '{name}' was not valid JSON")
            spec.setdefault("title", name)
            (src_dir / f"{fname}.dynamo.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
            (src_dir / f"{fname}.py").write_text(spec.get("python", ""), encoding="utf-8")  # for Mira's review
            problems += [f"{fname}.dyn: {p}" for p in dynamo_problems(spec)]
            spec_to_dyn(spec, files_dir / f"{fname}.dyn")
            made.append(f"{fname}.dyn")
        elif kind == "PRINTABLE" and not printables_allowed():
            skipped.append(f"{name} (designed printables are switched off)")
        elif kind == "PRINTABLE":
            fname = _title_to_name(name)
            doc = re.sub(r"^```(?:html)?\s*|\s*```$", "", content.strip())
            (src_dir / f"{fname}.printable.html").write_text(doc, encoding="utf-8")
            problems += [f"{fname}.pdf: {p}" for p in printable_problems(doc)]
            safe = secure_printable(doc)
            if html_to_pdf(safe, files_dir / f"{fname}.pdf"):
                made.append(f"{fname}.pdf")
                html_preview_png(safe, src_dir / "previews" / f"{fname}.png", 794, 1123)
            else:
                problems.append(f"{fname}: could not print the designed page (browser engine unavailable)")
        elif kind == "APP":
            fname = _title_to_name(name)
            html_text = content.strip()
            html_text = re.sub(r"^```(?:html)?\s*|\s*```$", "", html_text)
            (files_dir / f"{fname}.html").write_text(html_text, encoding="utf-8")
            (src_dir / f"{fname}.html").write_text(html_text, encoding="utf-8")
            problems += [f"{fname}.html: {p}" for p in app_problems(html_text)]
            made.append(f"{fname}.html")
        elif kind == "PYFILE":
            path = _clean_path(name)
            for d in (files_dir, src_dir):
                (d / path).parent.mkdir(parents=True, exist_ok=True)
                (d / path).write_text(content, encoding="utf-8")
            pyproject[path] = content
            made.append(path)
        else:
            path = _clean_path(name)
            if Path(path).suffix.lower() in CODE_SUFFIXES or Path(path).suffix.lower() not in {".csv", ".txt"}:
                skipped.append(path)  # code / unsupported files never reach the buyer
                continue
            (files_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (files_dir / path).write_text(content, encoding="utf-8")
            (src_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (src_dir / path).write_text(content, encoding="utf-8")
            made.append(path)
    if pyproject:
        problems += [f"Python project: {p}" for p in python_project_problems(pyproject)]
    # a product that promises to calculate / track / analyse must actually include a working tool
    promise = (product["title"] + " " + json.dumps(idea)).lower()
    tool_words = ("calculat", "tracker", "reconcil", "analy", "splitter", "allocator", "planner", "budget",
                  "dashboard", "log", "turnover", "reorder", "forecast", "cash", "kpi", "sentiment")
    printables = (src_dir.glob("*.printable.html"))
    has_printable = any(True for _ in printables)
    if any(w in promise for w in tool_words) and not any(m.endswith((".xlsx", ".dyn", ".html", ".py", ".ipynb")) for m in made) \
            and not (has_printable and "planner" in promise):
        problems.append("the product promises a tool (calculate/track/analyse) but contains no working tool: add a "
                        "spreadsheet, browser tool or (for coders) a complete Python project")
    if sum(m.endswith(".pdf") for m in made) < 2 and not has_printable \
            and not any(m.endswith((".xlsx", ".dyn", ".html", ".py", ".ipynb")) for m in made):
        problems.append("only one document: include a Start Here guide AND the actual product files")
    # privacy scan of everything the buyer will receive
    from .privacy import NETWORK_CALLS, scan
    for f in sorted(src_dir.rglob("*")):
        if f.is_file() and f.name != "_checks.json":
            body = f.read_text(encoding="utf-8", errors="replace")
            for kind, sample in scan(body)[:5]:
                problems.append(f"{f.name}: real-looking {kind} '{sample[:4]}…': replace with fictional example data")
            if f.suffix == ".py" and NETWORK_CALLS.search(body) and f.name.replace(".py", ".dynamo.json") in \
                    {g.name for g in src_dir.iterdir()}:
                problems.append(f"{f.name}: Dynamo script makes network calls: remove them")
    if not made:
        raise ValueError("Builder produced no finished files" + (f" (skipped code: {skipped})" if skipped else ""))
    (src_dir / "_checks.json").write_text(json.dumps({"problems": problems, "skipped_code": skipped}), encoding="utf-8")
    x("update products set status='built', folder=?, updated=? where id=?",
      (folder.relative_to(OUTPUT).as_posix(), time.time(), product["id"]))
    used = (kv_get("last_model") or {}).get("model", "?")
    cost = round(api_spent(0) - spent_before, 4)
    x("update products set built_with=?, build_cost=coalesce(build_cost,0)+? where id=?", (used, cost, product["id"]))
    log("builder", f"Finished #{product['id']}: {', '.join(made)[:150]}" + (f" (left out: {skipped})" if skipped else ""))
    status("builder", "walking", "Done! Mira, please check it", target="master")


# ── MASTER QA: nothing reaches your inbox unchecked ───────────────────────
def qa(product):
    idea = json.loads(product["idea"])
    base = OUTPUT / product["folder"]
    src = base / "source" if (base / "source").exists() else base / "files"
    digest = files_digest(src)
    checks = {}
    if (src / "_checks.json").exists():
        checks = json.loads((src / "_checks.json").read_text(encoding="utf-8"))
    buyer_files = sorted(p.name for p in (base / "files").rglob("*") if p.is_file())
    code_files = [f for f in buyer_files if Path(f).suffix.lower() in {".js", ".sh", ".bat", ".ps1", ".exe"}]  # loose scripts
    status("master", "thinking", f"Quality check: “{product['title'][:36]}”")
    r = ask_json("master", "master", _base_system("the Master agent doing strict quality control"), f"""
You are a demanding buyer who paid €{idea.get('price_eur', '?')}. Be strict: most first drafts are a 5-7.
Promised: {json.dumps(idea)}
{NO_CODE_RULES}
These rules beat the original promise. Judge whether the FORMAT fits the intended buyer: Python is right
only for coders/data learners buying code, and then it must be a complete project that runs first time;
for non-technical buyers it must be a spreadsheet, documents or a browser tool. A product that promises a
tool without a working one, or that hands the buyer unfinished parts, is incomplete.
Files the buyer downloads: {buyer_files}
Automatic checks: {json.dumps(checks)}
Source of those files (long files shown as start + end excerpts with their real size; do NOT call a file
empty or cut off just because its middle is omitted):
{digest}

Score each 0-2: FINISHED FOR ITS BUYER (usable as-is in tools they have: spreadsheets work by typing values,
browser tools open with a double-click, Dynamo tools run from Dynamo Player, Python projects run first time
with the README commands; nothing to finish or wire up; clear Start Here / README),
COMPLETE (no placeholders/TODOs, empty rows for input are fine), CORRECT (facts, formulas and references right),
SPECIFIC (concrete to the buyer's real situation, not generic advice), WORTH THE PRICE (saves real time).
score = sum (0-10). 9-10 only if you'd happily pay for it yourself. Any automatic-check problem = max 5.
PRIVACY counts under CORRECT: real-looking personal data, storing personal data without a privacy note /
retention guidance, collecting more than needed, or legal/GDPR claims without a "not legal advice" note = max 5.
JSON: {{"score":0,"breakdown":{{"usable":0,"complete":0,"correct":0,"specific":0,"worth":0}},
"verdict":"one honest sentence","fix_notes":"concrete changes if score < 7, else empty"}}""",
                 stream=product["stream"], mock_key="qa")
    score = int(r.get("score", 0))
    if checks.get("problems") or code_files:
        score = min(score, 5)
        r["score"] = score
        r["fix_notes"] = ("Fix these automatic-check problems: " + "; ".join(checks.get("problems", []) +
                          [f"remove code file {c}" for c in code_files]) + ". " + r.get("fix_notes", "")).strip()
    auto_tries = kv_get(f"qa_tries:{product['id']}", 0)
    x("update products set qa=? where id=?", (json.dumps(r), product["id"]))
    if score < 7 and auto_tries < CFG["loop"].get("max_auto_revisions", 2):
        kv_set(f"qa_tries:{product['id']}", auto_tries + 1)
        x("update products set status='revise', feedback=?, updated=? where id=?",
          (r.get("fix_notes", ""), time.time(), product["id"]))
        log("master", f"QA {score}/10 on #{product['id']} — sent back to Bo: {r.get('fix_notes', '')[:100]}")
        status("master", "walking", f"Bo, needs work ({score}/10)", target="builder")
    elif score < CFG["loop"].get("min_score_after_revisions", 5):
        # Still failing after the allowed revisions: shelve it instead of passing it on to Maya and the owner.
        x("update products set status='dropped', feedback=?, updated=? where id=?",
          (r.get("fix_notes", ""), time.time(), product["id"]))
        x("update approvals set status='superseded' where product_id=? and status='pending'", (product["id"],))
        log("master", f"Shelved #{product['id']}: still {score}/10 after {auto_tries} revisions — "
                      f"{r.get('verdict', '')[:100]}")
        status("master", "thinking", f"Shelved #{product['id']} ({score}/10)")
    else:
        x("update products set status='checked', updated=? where id=?", (time.time(), product["id"]))
        log("master", f"QA {score}/10 on #{product['id']} — {r.get('verdict', '')[:100]}")
        status("master", "walking", f"Maya, it's ready ({score}/10)", target="marketer")


# ── MARKETER: listing + posts, then into your inbox ──────────────────────
def market(product):
    idea = json.loads(product["idea"])
    base = OUTPUT / product["folder"]
    folder = base / "files"
    names = [p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()]
    readme = ""
    for cand in [base / "source" / "Start Here.md", folder / "README.md"]:
        if cand.exists():
            readme = cand.read_text(encoding="utf-8")[:3000]
            break
    status("marketer", "working", f"Writing listing for “{product['title'][:36]}”")
    lst = ask_json("marketer", "marketer", _base_system("the Marketer"), f"""
Write the sales listing for this product. Honest, specific, benefit-first. No hype, no fake reviews, no income claims.
Describe ONLY the formats actually delivered (see Files: .xlsx = works in Excel & Google Sheets, .pdf = print-ready,
.docx = editable in Word / Google Docs, .dyn = runs from Dynamo Player in Revit (state Revit versions),
.html = browser tool, double-click to open, works offline, .py/.ipynb = complete Python project, state the
Python version and that it runs out of the box). For non-code formats say clearly that no coding is needed. If the product stores personal data and includes privacy/retention
guidance, you may say it's "built with GDPR in mind", never claim it makes the buyer "GDPR compliant".
Posts: no personal data, no cold DMs or e-mail lists, no tagging people; value first, link at the end.
Idea: {json.dumps(idea)}
Files: {names}
README: {readme}
Also write 3 value-first social posts (where it fits: a relevant subreddit, LinkedIn, Pinterest pin text). Posts must
be useful on their own and mention the product only lightly at the end — never spammy.
Also write the ETSY version: title max 140 characters, front-loaded with the exact phrases buyers search for;
exactly 13 tags, each max 20 characters, specific long-tail phrases; and an honest one-line AI disclosure for the
description (e.g. "Designed by me with the help of AI tools; every file reviewed and tested by me.").
JSON: {{"title":"","subtitle":"","description":"markdown","price_eur":0,"tags":["max 13"],
"etsy_title":"","etsy_tags":["13 tags, each <= 20 chars"],"ai_disclosure":"",
"posts":[{{"where":"","text":""}}]}}""", stream=product["stream"], mock_key="marketer")

    lst["etsy_title"] = (lst.get("etsy_title") or lst.get("title", ""))[:140]
    lst["etsy_tags"] = [t[:20] for t in (lst.get("etsy_tags") or lst.get("tags", []))][:13]
    x("update products set status='awaiting', listing=?, updated=? where id=?",
      (json.dumps(lst), time.time(), product["id"]))
    x("insert into approvals(product_id,status,created) values(?,?,?)", (product["id"], "pending", time.time()))
    log("marketer", f"#{product['id']} is in your inbox for approval")
    status("marketer", "walking", "New item in your inbox 📥", target="inbox")


def package(product_id):
    """Called when you approve: zip the files so you can upload them."""
    p = q("select * from products where id=?", (product_id,), one=True)
    folder = OUTPUT / p["folder"]
    zip_path = shutil.make_archive(str(folder / _slug(p["title"])), "zip", folder / "files")
    lst = json.loads(p["listing"])
    photos = sorted((folder / "source" / "previews").glob("*.png")) if (folder / "source" / "previews").exists() else []
    if photos:
        (folder / "listing-photos").mkdir(exist_ok=True)
        for ph in photos[:10]:
            shutil.copy(ph, folder / "listing-photos" / ph.name)
    etsy = (f"\n\n## Etsy\n\n**Title (max 140):** {lst.get('etsy_title', lst['title'])}\n\n"
            f"**13 tags:** {', '.join(lst.get('etsy_tags', []))}\n\n"
            f"**AI disclosure (Etsy requires it):** in 'How it's made' choose 'With an AI generator', and add to the "
            f"description: {lst.get('ai_disclosure') or 'Designed with the help of AI tools; reviewed and tested by me.'}\n\n"
            f"**Listing photos:** the listing-photos folder ({len(photos)} preview images).\n")
    (folder / "LISTING.md").write_text(
        f"# {lst['title']}\n\n*{lst.get('subtitle', '')}*\n\n**Price:** €{lst['price_eur']}\n\n"
        f"**Tags:** {', '.join(lst.get('tags', []))}\n\n## Description\n\n{lst['description']}\n\n## Posts\n\n" +
        "\n\n".join(f"### {po['where']}\n{po['text']}" for po in lst.get("posts", [])) + etsy, encoding="utf-8")
    return zip_path


# ── ACCOUNTANT: import sales, watch the budget ───────────────────────────
def accountant_sync():
    token = CFG["sales"].get("gumroad_token")
    if not token:
        return
    status("accountant", "working", "Checking Gumroad sales…")
    after = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    url = f"https://api.gumroad.com/v2/sales?" + urllib.parse.urlencode({"access_token": token, "after": after})
    new = 0
    titles = {p["title"].lower(): p["stream"] for p in q("select title, stream from products")}
    try:
        while url:
            data = json.load(urllib.request.urlopen(url, timeout=20))
            for sale in data.get("sales", []):  # privacy: only product, amount and sale id are kept, never buyer details
                name = sale.get("product_name", "")
                sid = titles.get(name.lower()) or next((v for k, v in titles.items() if k[:25] in name.lower()), None)
                eur = float(sale.get("price", 0)) / 100 * CFG["sales"]["usd_to_eur"]
                try:
                    x("insert into ledger(ts,kind,stream,amount_eur,memo,ext_id) values(?,?,?,?,?,?)",
                      (time.time(), "sale", sid, round(eur, 2), f"Gumroad: {name}", f"gumroad:{sale['id']}"))
                    new += 1
                except Exception:
                    pass  # already imported
            nxt = data.get("next_page_url")
            url = f"https://api.gumroad.com{nxt}&access_token={token}" if nxt else None
    except Exception as e:
        log("accountant", f"Gumroad sync failed: {e}")
        return
    if new:
        log("accountant", f"💰 Imported {new} new sale(s) from Gumroad")
        status("accountant", "walking", f"{new} new sale(s)!", target="master")


def weekly_review():
    """Master pauses streams that keep not selling, and writes you a 3-line memo."""
    kill_weeks = CFG["loop"]["kill_after_weeks_without_sale"]
    paused = kv_get("paused_streams", [])
    report = {}
    for s in all_stream_defs():
        st = stream_stats(s["id"])
        report[s["id"]] = st
        if st["live"] >= 2 and st["sales"] == 0 and st["weeks_live"] >= kill_weeks and s["id"] not in paused:
            paused.append(s["id"])
            log("master", f"⏸ Paused {s['name']}: {st['live']} products live {st['weeks_live']} weeks, no sales")
    kv_set("paused_streams", paused)
    status("master", "thinking", "Writing the weekly memo…")
    memo = ask("master", "master", _base_system("the Master agent"), f"""
Weekly numbers per department: {json.dumps(report)}
Write the owner a memo of max 3 short lines: what's working, what to change, and the one thing (if any) they
should do this week (max 20 minutes). Plain text.""", max_tokens=400, mock_key="memo")
    kv_set("weekly_memo", {"ts": time.time(), "text": memo.strip()})
    log("master", "Wrote the weekly memo")


# ── GROWTH: new departments ───────────────────────────────────────────────
PALETTE = ["#e86fbf", "#a78bfa", "#2dd4bf", "#f87171", "#facc15", "#60a5fa", "#fb923c", "#4ade80"]
DEPT_SHAPE = """{"name":"short department name","channel":"where it sells, e.g. Gumroad (digital download)",
"brief":"3-5 sentences: buyers, product types the Builder (text files only: markdown, csv, python, html) can fully make,
price range in EUR","research_queries":["3 short Reddit search phrases where these buyers ask for help"],
"why":"one sentence: why the owner can credibly sell this"}"""


def _new_stream(d):
    existing = {x["id"] for x in all_stream_defs()}
    base = _slug(d["name"])[:20] or "dept"
    sid, n = base, 2
    while sid in existing:
        sid, n = f"{base}{n}", n + 1
    used = {x.get("color") for x in all_stream_defs()}
    color = next((c for c in PALETTE if c not in used), PALETTE[len(existing) % len(PALETTE)])
    return {"id": sid, "name": d["name"][:40], "color": color, "active": True,
            "channel": d.get("channel", "Gumroad (digital download)"), "brief": d["brief"],
            "research_queries": d.get("research_queries", [])[:3]}


def add_stream(d):
    from .core import _lock
    with _lock:
        st = _new_stream(d)
        kv_update("custom_streams", lambda c: c + [st], [])
    log("master", f"🏷 New department opened: {st['name']}")
    return st


def propose_department():
    """Mira suggests ONE new department; it goes to your inbox to accept or decline."""
    status("master", "thinking", "Thinking about a new department…")
    current = [{"name": s["name"], **stream_stats(s["id"])} for s in streams()]
    declined = [p["name"] for p in kv_get("dept_proposals", []) if p["status"] == "declined"]
    r = ask_json("master", "master", _base_system("the Master agent planning growth"), f"""
Current departments and results: {json.dumps(current)}
Already declined by the owner (don't suggest again): {declined or 'none'}
Suggest ONE new department where digital downloads PROVABLY SELL: a niche with strong marketplace demand (Etsy is
where most buyers search; Gumroad needs own traffic), buyers who pay, and room to stand out. It does NOT have to
match the owner's skills: demand comes first, skills are a bonus. Think niche audiences (e.g. teachers,
nurses, wedding planners, landlords, freelancers, small cafés, students in specific fields) and formats that
sell ({"planners, printable signs and worksheets, " if printables_allowed() else ""}trackers and niche spreadsheet
tools, CV/resume templates in Word, offline browser tools, professional document kits).
{"" if printables_allowed() else "No designed printables (planners, wall art, signs): the owner doesn't want AI-designed decor."}
Avoid saturated broad niches and avoid overlap with existing departments. Put the evidence in "why".
JSON: {DEPT_SHAPE}""", mock_key="dept")
    kv_update("dept_proposals", lambda props: props + [dict(
        r, id=max([p["id"] for p in props], default=0) + 1, status="pending", ts=time.time())], [])
    log("master", f"💡 Proposed a new department: {r['name']} — in your inbox")
    status("master", "walking", f"Idea: {r['name'][:30]}!", target="inbox")


def create_requested_department(req):
    """Owner typed an area; Mira turns it into a proper department brief."""
    status("master", "thinking", f"Setting up “{req['name'][:30]}”…")
    r = ask_json("master", "master", _base_system("the Master agent"), f"""
The owner wants a new department: "{req['name']}". Their notes: "{req.get('about', '')}".
Write the department setup, keeping the owner's intent.
JSON: {DEPT_SHAPE}""", mock_key="dept")
    r["name"] = req["name"] or r.get("name", "New department")
    add_stream(r)
    status("master", "walking", f"Opened {r['name'][:28]}!", target="scout")
