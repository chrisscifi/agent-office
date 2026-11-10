"""Turn the Builder's text into finished, no-code files a buyer can open straight away.

  Markdown guide/checklist  ->  .pdf (read & print)  +  .docx (edit in Word / Google Docs)
  Spreadsheet spec (JSON)   ->  .xlsx with working formulas, input cells, dropdowns (Excel / Google Sheets)
"""
import json
import re
from pathlib import Path

import markdown as md

CODE_SUFFIXES = {".py", ".ipynb", ".js", ".ts", ".sh", ".bat", ".ps1", ".r", ".sql", ".exe", ".vba", ".bas"}

# ── fonts (Unicode: Romanian, Russian, French all need a real TTF) ─────────
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeuii.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
]


def _font():
    for reg, bold, ital in FONT_CANDIDATES:
        if Path(reg).exists():
            return reg, (bold if Path(bold).exists() else reg), (ital if Path(ital).exists() else reg)
    return None


UNSAFE_HTML = re.compile(r"<(script|style|iframe|object|embed|link|meta|form|svg)\b[^>]*>.*?</\1\s*>|"
                         r"<(script|iframe|object|embed|link|meta|img|form|input|svg)\b[^>]*/?>", re.I | re.S)


def _plain(text, strict=True):
    """Drop emoji (and, for the basic engine, other symbols fonts often lack); normalise checkboxes;
    remove active HTML."""
    text = UNSAFE_HTML.sub("", text)
    # Markdown needs a blank line before a list or table that follows a sentence; AI output often skips it
    lines, fixed = text.splitlines(), []
    for ln in lines:
        starts_block = re.match(r"^\s*([-*+]\s|\d+[.)]\s|\|)", ln)
        prev = fixed[-1] if fixed else ""
        prev_is_block = re.match(r"^\s*([-*+]\s|\d+[.)]\s|\||#|```|>)", prev)
        if starts_block and prev.strip() and not prev_is_block:
            fixed.append("")
        fixed.append(ln)
    text = "\n".join(fixed)
    if not strict:   # browser engine: symbol fonts cover ✓ ★ ☎ etc.; only colour emoji become boxes in PDFs
        text = re.sub(r"^(\s*)[-*] \[ \]", r"\1- ☐", text, flags=re.M)
        text = re.sub(r"^(\s*)[-*] \[[xX]\]", r"\1- ☑", text, flags=re.M)
        return re.sub("[\U0001F000-\U0001FAFF\uFE0F\u200D]", "", text)
    text = re.sub(r"^(\s*)[-*] \[ \]", r"\1- ☐", text, flags=re.M)
    text = re.sub(r"^(\s*)[-*] \[[xX]\]", r"\1- ☑", text, flags=re.M)
    return "".join(ch for ch in text if ord(ch) < 0x2600 or ch in "☐☑")


# ── Markdown -> PDF, primary engine: the browser already on the laptop (Edge / Chrome) ─────
PDF_CSS = """
@page { size: A4; margin: 18mm 17mm 20mm 17mm;
  @bottom-right { content: counter(page) " / " counter(pages); font: 9pt 'Segoe UI', Arial, 'DejaVu Sans', sans-serif; color: #8a94a6; } }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, 'DejaVu Sans', 'Noto Sans',
  'Segoe UI Symbol', 'Segoe UI Emoji', 'Apple Symbols', 'Noto Sans Symbols', 'Noto Sans Symbols 2', 'Symbola',
  'Noto Emoji', sans-serif; font-size: 10.5pt;
  line-height: 1.55; color: #1f2733; margin: 0; }
h1 { font-size: 22pt; color: #16325c; margin: 0 0 6pt; padding-bottom: 6pt; border-bottom: 3px solid #3d7be0; }
h2 { font-size: 14.5pt; color: #16325c; margin: 18pt 0 6pt; page-break-after: avoid; }
h3 { font-size: 12pt; color: #24497e; margin: 14pt 0 4pt; page-break-after: avoid; }
p, ul, ol { margin: 0 0 8pt; }
li { margin: 2pt 0; }
table { width: 100%; border-collapse: collapse; margin: 8pt 0 12pt; font-size: 9.5pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #16325c; color: #fff; text-align: left; padding: 6pt 7pt; font-weight: 600; }
td { padding: 5pt 7pt; border-bottom: 1px solid #dfe5ee; vertical-align: top; }
tr:nth-child(even) td { background: #f5f8fc; }
code { font-family: Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', 'Segoe UI Symbol', 'Noto Sans Symbols', monospace; background: #eef2f8; padding: 1pt 4pt; border-radius: 3pt; font-size: 9.5pt; }
pre { background: #f1f4f9; border-left: 3px solid #3d7be0; padding: 8pt 10pt; white-space: pre-wrap; font-size: 9pt; }
pre code { background: none; padding: 0; }
blockquote { margin: 8pt 0; padding: 8pt 12pt; background: #fff7e0; border-left: 4px solid #f0b429; border-radius: 3pt; }
blockquote p { margin: 0; }
hr { border: 0; border-top: 1px solid #dfe5ee; margin: 14pt 0; }
a { color: #2f6fd6; text-decoration: none; }
.doc-title { font-size: 9pt; color: #8a94a6; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 4pt; }
"""
_BROWSER = {"path": None, "checked": False, "broken": False}


def find_browser():
    """Edge ships with Windows 10/11; Chrome or Chromium also work. Set PDF_BROWSER to override."""
    import glob, os, shutil
    if _BROWSER["checked"]:
        return _BROWSER["path"]
    _BROWSER["checked"] = True
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.environ.get("PDF_BROWSER", ""),
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    candidates += [shutil.which(n) or "" for n in ("msedge", "google-chrome", "chromium", "chromium-browser")]
    candidates += sorted(glob.glob("/opt/pw-browsers/chromium_headless_shell-*/chrome-*/chrome-headless-shell"))
    _BROWSER["path"] = next((c for c in candidates if c and os.path.exists(c)), None)
    return _BROWSER["path"]


def md_to_html(text, title=""):
    import html as _h
    body = md.markdown(_plain(text, strict=False), extensions=["tables", "fenced_code", "sane_lists"])
    body = body.replace("☐", "&#9744;").replace("☑", "&#9745;")
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"   # no scripts, no internet
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"{csp}\">"
            f"<title>{_h.escape(title)}</title><style>{PDF_CSS}</style></head><body>{body}</body></html>")


def _browser_run(html_doc, args):
    """Run the laptop's browser headless on an HTML document. Returns the temp dir path while open."""
    import os, subprocess
    exe = find_browser()
    if not exe or _BROWSER["broken"]:
        return False
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        src.write_text(html_doc, encoding="utf-8")
        cmd = [exe, "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--disable-sync", "--disable-background-networking", "--disable-component-update",
               "--disable-default-apps", "--disable-dev-shm-usage", f"--user-data-dir={Path(tmp) / 'profile'}",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000", "--font-render-hinting=none",
               "--hide-scrollbars"] + args + [src.resolve().as_uri()]
        if "headless-shell" not in exe:
            cmd.insert(1, "--headless=new")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")
        flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: no flashing windows on Windows
        try:
            subprocess.run(cmd, timeout=45, capture_output=True, creationflags=flags)
        except Exception:
            pass
    return True


def html_to_pdf(html_doc, out_path):
    out = Path(out_path).resolve()
    if out.exists():
        out.unlink()
    _browser_run(html_doc, ["--no-pdf-header-footer", f"--print-to-pdf={out}"])
    ok = out.exists() and out.stat().st_size > 1000
    if not ok and find_browser():
        _BROWSER["broken"] = True   # don't keep waiting on it this session; the built-in engine takes over
    return ok


def html_preview_png(html_doc, out_path, width=1200, height=1600):
    """A listing photo / thumbnail of a designed page (Etsy needs images)."""
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    _browser_run(html_doc, [f"--window-size={width},{height}", f"--screenshot={out}"])
    return out.exists() and out.stat().st_size > 1000


def browser_pdf(text, out_path, title=""):
    return html_to_pdf(md_to_html(text, title), out_path)


PRINT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"


def printable_problems(html_doc):
    probs = []
    low = html_doc.lower()
    if "<html" not in low and "<!doctype" not in low:
        probs.append("printable is not a complete HTML page")
    if "@page" not in low:
        probs.append("printable has no @page rule (paper size/margins)")
    if re.search(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", html_doc, re.I) or "@import" in low:
        probs.append("printable loads things from the internet: inline everything (system fonts, CSS, SVG)")
    if len(html_doc) < 1200:
        probs.append("printable is too small to be a real product")
    return probs


def secure_printable(html_doc):
    """Strip scripts and lock the page down before printing."""
    html_doc = re.sub(r"<script\b.*?</script\s*>", "", html_doc, flags=re.I | re.S)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{PRINT_CSP}">'
    if re.search(r"<head[^>]*>", html_doc, re.I):
        return re.sub(r"(<head[^>]*>)", r"\1" + meta, html_doc, count=1, flags=re.I)
    return meta + html_doc


def md_to_pdf(text, out_path, title=""):
    """Best quality first (the laptop's own browser), then the built-in engine as a fallback."""
    try:
        from .core import CFG
        engine = CFG.get("render", {}).get("pdf_engine", "browser")
    except Exception:
        engine = "browser"
    if engine == "browser" and browser_pdf(text, out_path, title):
        return "browser"
    basic_pdf(text, out_path, title)
    return "basic"


# ── Markdown -> PDF, fallback engine (pure Python) ─────────────────────────
def basic_pdf(text, out_path, title=""):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    font = _font()
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    fam = "Helvetica"
    if font:
        pdf.add_font("Body", "", font[0])
        pdf.add_font("Body", "B", font[1])
        pdf.add_font("Body", "I", font[2])
        pdf.add_font("Body", "BI", font[1])
        fam = "Body"
    pdf.set_title(title or Path(out_path).stem)
    pdf.add_page()
    pdf.set_font(fam, size=10.5)
    body = _plain(text).replace("☐", "[  ]").replace("☑", "[x]")
    html = md.markdown(body, extensions=["tables", "fenced_code", "sane_lists"])
    html = re.sub(r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>",
                  lambda m: "<p>" + m.group(1).strip().replace("\n", "<br>") + "</p>", html, flags=re.S)  # code blocks
    html = re.sub(r"<code[^>]*>(.*?)</code>", r"<b>\1</b>", html, flags=re.S)   # inline code -> bold
    html = re.sub(r"<pre[^>]*>(.*?)</pre>", r"<p>\1</p>", html, flags=re.S)
    html = re.sub(r"</?(tt|kbd|samp|var)>", "", html)
    html = html.replace("<hr />", "<br>")
    # the PDF library can't nest tags inside table cells: keep the text, drop inner formatting
    def _clean_cell(m):
        tag, attrs, inner = m.group(1), m.group(2) or "", m.group(3)
        inner = re.sub(r"<br\s*/?>", " ", inner)
        inner = re.sub(r"<[^>]+>", "", inner).strip()
        attrs = re.sub(r'\s*style="[^"]*"', "", attrs)   # alignment styles also trip the library
        return f"<{tag}{attrs}>{inner}</{tag}>"
    html = re.sub(r"<(td|th)(\s[^>]*)?>(.*?)</\1>", _clean_cell, html, flags=re.S)
    if not font:  # core fonts are Latin-1 only
        html = html.encode("latin-1", "replace").decode("latin-1")
    import inspect
    extra = {"pre_code_font": fam} if "pre_code_font" in inspect.signature(pdf.write_html).parameters else {}
    styles = {
        "h1": FontFace(color=(25, 45, 90), size_pt=19, emphasis="B"),
        "h2": FontFace(color=(25, 45, 90), size_pt=14.5, emphasis="B"),
        "h3": FontFace(color=(40, 60, 100), size_pt=12, emphasis="B"),
        "code": FontFace(family=fam), "pre": FontFace(family=fam),
    }
    try:
        pdf.write_html(html, font_family=fam, table_line_separators=True, tag_styles=styles, **extra)
    except Exception:
        # fallback: simple, safe layout (tables become 'a | b' lines) instead of failing the whole product
        pdf = _plain_pdf(html, fam, font, title)
    pdf.output(str(out_path))


def _plain_pdf(html, fam, font, title):
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    if font:
        pdf.add_font(fam, "", font[0])
        pdf.add_font(fam, "B", font[1])
    pdf.set_title(title or "Document")
    pdf.add_page()
    html = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", html)
    html = re.sub(r"</tr>", "\n", html)
    blocks = re.split(r"(<h[1-3][^>]*>.*?</h[1-3]>)", html, flags=re.S)
    for b in blocks:
        m = re.match(r"<h([1-3])[^>]*>(.*?)</h\1>", b, re.S)
        if m:
            pdf.set_font(fam, "B", {"1": 18, "2": 14, "3": 12}[m.group(1)])
            pdf.multi_cell(0, 8, re.sub(r"<[^>]+>", "", m.group(2)).strip())
            pdf.ln(1)
            continue
        text = re.sub(r"<li[^>]*>", "\n• ", b)
        text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        import html as _h
        text = re.sub(r"\n{3,}", "\n\n", _h.unescape(text)).strip()
        if text:
            pdf.set_font(fam, "", 10.5)
            pdf.multi_cell(0, 5.5, text)
            pdf.ln(2)
    return pdf


# ── Markdown -> DOCX ───────────────────────────────────────────────────────
def _runs(par, text):
    """Bold (**x**), italic (*x*) and `code` inside a paragraph."""
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            par.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            par.add_run(part[1:-1]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            par.add_run(part[1:-1]).italic = True
        else:
            par.add_run(re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", part))


def md_to_docx(text, out_path):
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    lines = _plain(text).splitlines()
    i, in_code, code = 0, False, []
    while i < len(lines):
        line = lines[i].rstrip()
        if line.strip().startswith("```"):
            if in_code:
                p = doc.add_paragraph("\n".join(code))
                for r in p.runs:
                    r.font.name = "Consolas"
                code, in_code = [], False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            rows = [line]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].rstrip())
                i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            width = max(len(r) for r in cells)
            table = doc.add_table(rows=len(cells), cols=width)
            table.style = "Table Grid"
            for r, row in enumerate(cells):
                for c in range(width):
                    par = table.cell(r, c).paragraphs[0]
                    _runs(par, row[c] if c < len(row) else "")
                    if r == 0:
                        for run in par.runs:
                            run.bold = True
            doc.add_paragraph()
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            doc.add_heading(re.sub(r"[*`]", "", m.group(2)), level=min(len(m.group(1)), 3))
        elif re.match(r"^\s*[-*+]\s+", line):
            _runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*+]\s+", "", line))
        elif re.match(r"^\s*\d+[.)]\s+", line):
            _runs(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+[.)]\s+", "", line))
        elif line.startswith(">"):
            p = doc.add_paragraph()
            _runs(p, line.lstrip("> "))
            for r in p.runs:
                r.italic = True
        elif line.strip() and not re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            _runs(doc.add_paragraph(), line)
        i += 1
    doc.save(str(out_path))


# ── Spreadsheet spec -> XLSX ───────────────────────────────────────────────
NUMBER_FORMATS = {"eur": '#,##0.00 "€"', "dkk": '#,##0.00 "kr."', "usd": '"$"#,##0.00', "number": "#,##0.00",
                  "integer": "0", "percent": "0.0%", "date": "yyyy-mm-dd", "time": "hh:mm", "text": "@"}


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def spec_problems(spec):
    """Cheap sanity checks so broken spreadsheets never reach the buyer."""
    probs = []
    sheets = spec.get("sheets") or []
    if not sheets:
        return ["spreadsheet has no sheets"]
    names = {s.get("name", "") for s in sheets}
    for s in sheets:
        if not s.get("columns"):
            probs.append(f"sheet '{s.get('name')}' has no columns")
        for row in s.get("rows", []):
            for v in row:
                if isinstance(v, str) and v.startswith("="):
                    if v.count("(") != v.count(")"):
                        probs.append(f"unbalanced brackets in formula {v[:60]}")
                    for ref in re.findall(r"'([^']+)'!", v) + re.findall(r"(?<![\w'])([A-Za-z_][\w ]*)!", v):
                        if ref.strip() not in names:
                            probs.append(f"formula {v[:60]} points to missing sheet '{ref}'")
    return probs[:10]


def spec_to_xlsx(spec, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    head_fill = PatternFill("solid", fgColor="1F3A5F")
    input_fill = PatternFill("solid", fgColor="FFF6D5")   # yellow = type here
    thin = Side(style="thin", color="D0D7E2")
    wb = Workbook()
    wb.remove(wb.active)

    if spec.get("instructions"):
        ws = wb.create_sheet("How to use")
        ws["A1"] = spec.get("title", "How to use")
        ws["A1"].font = Font(bold=True, size=15, color="1F3A5F")
        r = 3
        for line in _plain(spec["instructions"]).splitlines():
            ws.cell(r, 1, re.sub(r"[*#`]", "", line).strip())
            ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        ws.cell(r + 1, 1, "Yellow cells are for your input. White cells calculate automatically.").font = Font(italic=True)
        ws.column_dimensions["A"].width = 100

    for s in spec["sheets"]:
        ws = wb.create_sheet(str(s.get("name", "Sheet"))[:31])
        cols = s["columns"]
        inputs = set(s.get("input_columns", []))
        for c, col in enumerate(cols, 1):
            cell = ws.cell(1, c, col.get("header", f"Column {c}"))
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[_col(c)].width = col.get("width", max(12, len(str(cell.value)) + 4))
        for r, row in enumerate(s.get("rows", []), 2):
            for c, v in enumerate(row, 1):
                if c > len(cols):
                    break
                fmt = cols[c - 1].get("format")
                if fmt == "date" and isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                    import datetime
                    v = datetime.date.fromisoformat(v)
                cell = ws.cell(r, c, v)
                if fmt in NUMBER_FORMATS:
                    cell.number_format = NUMBER_FORMATS[fmt]
                if (c - 1) in inputs or cols[c - 1].get("input"):
                    cell.fill = input_fill
                cell.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        last = max(2, len(s.get("rows", [])) + 1)
        for v in s.get("validation", []):
            c = v.get("column", 0) + 1
            options = ",".join(str(o).replace(",", " ") for o in v.get("options", []))[:250]
            if options:
                dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{_col(c)}2:{_col(c)}{max(last, 200)}")
        ws.freeze_panes = "A2"
        if s.get("notes"):
            note_col = len(cols) + 2
            ws.cell(1, note_col, "Notes").font = Font(bold=True)
            ws.cell(2, note_col, s["notes"]).alignment = Alignment(wrap_text=True, vertical="top")
            ws.column_dimensions[_col(note_col)].width = 45
    if spec.get("instructions"):
        wb.move_sheet("How to use", offset=-(len(wb.sheetnames) - 1))
    wb.save(str(out_path))


# ── Preview for the office viewer ─────────────────────────────────────────
def xlsx_preview_html(path, max_rows=60):
    import html
    from openpyxl import load_workbook
    wb = load_workbook(path)  # formulas shown as written, so you can check them
    out = []
    for ws in wb.worksheets:
        out.append(f"<h2>{html.escape(ws.title)}</h2><table>")
        for r, row in enumerate(ws.iter_rows(values_only=True)):
            if r >= max_rows:
                out.append("<tr><td>…</td></tr>")
                break
            tag = "th" if r == 0 else "td"
            out.append("<tr>" + "".join(f"<{tag}>{html.escape('' if v is None else str(v))}</{tag}>" for v in row) + "</tr>")
        out.append("</table>")
    return "".join(out)


# ── Dynamo tool spec -> ready-to-run .dyn graph (Dynamo Player friendly) ───
def _guid():
    import uuid
    return uuid.uuid4().hex


def _port(name, desc):
    return {"Id": _guid(), "Name": name, "Description": desc, "UsingDefaultValue": False, "Level": 2,
            "UseLevels": False, "KeepListStructure": False}


def dynamo_problems(spec):
    """Syntax-check the embedded Python and look for the things that make Revit tools unsafe or unfriendly."""
    import ast
    probs = []
    code = spec.get("python", "")
    if not code.strip():
        return ["no Python code in the Dynamo tool"]
    try:
        ast.parse(code)
    except SyntaxError as e:
        probs.append(f"Python syntax error on line {e.lineno}: {e.msg}")
    inputs = spec.get("inputs", [])
    for i, _ in enumerate(inputs):
        if f"IN[{i}]" not in code:
            probs.append(f"input {i} ('{inputs[i].get('name')}') is never read via IN[{i}]")
    if "OUT" not in code:
        probs.append("the script never sets OUT, so the user sees no result")
    changes_model = any(k in code for k in ("Transaction", "Delete(", ".Set(", "Create.", "Move", "Rotate"))
    if changes_model and "TransactionManager" not in code and "Transaction(" not in code:
        probs.append("script changes the model but has no transaction")
    if changes_model and not any(i.get("type") == "boolean" for i in inputs):
        probs.append("script changes the model but has no on/off switch input (e.g. 'Apply changes', default false)")
    if re.search(r"ElementId\(\s*\d{3,}", code):
        probs.append("hard-coded element ids won't exist in the buyer's model")
    if ".IntegerValue" in code:
        probs.append("uses ElementId.IntegerValue, removed in recent Revit versions: use .Value instead")
    if re.search(r"TransactionTaskDone\(\s*\w", code):
        probs.append("TransactionTaskDone() takes no arguments: call TransactionManager.Instance.TransactionTaskDone()")
    if "__revit__" in code:
        probs.append("uses __revit__, which only exists in pyRevit: use DocumentManager.Instance.CurrentDBDocument in Dynamo")
    if "IronPython" in code or "import clr" not in code:
        probs.append("Dynamo Python (PythonNet3) needs 'import clr' and the Revit API references; IronPython-only code won't run")
    return probs[:10]


def spec_to_dyn(spec, out_path):
    name = spec.get("title", Path(out_path).stem)
    nodes, views, connectors, graph_inputs = [], [], [], []
    py_id = _guid()
    py_inputs = []
    for i, inp in enumerate(spec.get("inputs", [])):
        nid = _guid()
        t = inp.get("type", "string")
        out_port = _port("", t)
        if t == "number":
            node = {"ConcreteType": "CoreNodeModels.Input.DoubleInput, CoreNodeModels", "NumberType": "Double",
                    "InputValue": float(inp.get("default", 0) or 0), "NodeType": "NumberInputNode"}
        elif t == "boolean":
            node = {"ConcreteType": "CoreNodeModels.Input.BoolSelector, CoreNodeModels",
                    "InputValue": bool(inp.get("default", False)), "NodeType": "BooleanInputNode"}
        else:
            node = {"ConcreteType": "CoreNodeModels.Input.StringInput, CoreNodeModels",
                    "InputValue": str(inp.get("default", "")), "NodeType": "StringInputNode"}
        node.update({"Id": nid, "Inputs": [], "Outputs": [out_port], "Replication": "Disabled",
                     "Description": inp.get("description", "")})
        nodes.append(node)
        in_port = _port(f"IN[{i}]", f"Input #{i}")
        py_inputs.append(in_port)
        connectors.append({"Start": out_port["Id"], "End": in_port["Id"], "Id": _guid(), "IsHidden": "False"})
        label = inp.get("name", f"Input {i + 1}")
        views.append({"Name": label, "ShowGeometry": True, "Id": nid, "IsSetAsInput": True, "IsSetAsOutput": False,
                      "Excluded": False, "X": 0.0, "Y": 140.0 * i})
        graph_inputs.append({"Id": nid, "Name": label, "Type": {"number": "number", "boolean": "boolean"}.get(t, "string"),
                             "Value": str(node["InputValue"]).lower() if t == "boolean" else str(node["InputValue"]),
                             "Description": inp.get("description", "")})
    out_port = _port("OUT", "Result of the python script")
    nodes.append({"ConcreteType": "PythonNodeModels.PythonNode, PythonNodeModels", "Code": spec["python"],
                  "Engine": "PythonNet3" if str(spec.get("engine", "")).lower() in ("", "cpython3", "cpython", "ironpython2") else spec.get("engine"), "VariableInputPorts": True, "Id": py_id,
                  "NodeType": "PythonScriptNode", "Inputs": py_inputs, "Outputs": [out_port],
                  "Replication": "Disabled", "Description": "Runs an embedded Python script."})
    views.append({"Name": "Result: " + name[:40], "ShowGeometry": True, "Id": py_id, "IsSetAsInput": False,
                  "IsSetAsOutput": True, "Excluded": False, "X": 420.0, "Y": 0.0})
    graph = {
        "Uuid": str(__import__("uuid").uuid4()), "IsCustomNode": False, "Description": spec.get("description", ""),
        "Name": name, "ElementResolver": {"ResolutionMap": {}}, "Inputs": graph_inputs,
        "Outputs": [{"Id": py_id, "Name": "Result", "Type": "unknown", "InitialValue": "", "Description": ""}],
        "Nodes": nodes, "Connectors": connectors, "Dependencies": [], "NodeLibraryDependencies": [], "Bindings": [],
        "View": {"Dynamo": {"ScaleFactor": 1.0, "HasRunWithoutCrash": True, "IsVisibleInDynamoLibrary": True,
                            "Version": spec.get("dynamo_version", "2.16.1.2727"), "RunType": "Manual", "RunPeriod": "1000"},
                 "Camera": {"Name": "Background Preview", "EyeX": -17.0, "EyeY": 24.0, "EyeZ": 50.0, "LookX": 12.0,
                            "LookY": -13.0, "LookZ": -58.0, "UpX": 0.0, "UpY": 1.0, "UpZ": 0.0},
                 "NodeViews": views, "Annotations": [], "X": 0.0, "Y": 0.0, "Zoom": 1.0},
    }
    Path(out_path).write_text(json.dumps(graph, indent=2), encoding="utf-8")


# ── Browser tool: one self-contained .html file that works offline ────────
def app_problems(html_text):
    probs = []
    low = html_text.lower()
    if "<html" not in low and "<!doctype" not in low:
        probs.append("browser tool is not a complete HTML page")
    if "<script" not in low and "<form" not in low and "<input" not in low:
        probs.append("browser tool has no interactive part (inputs/script)")
    ext = re.findall(r"""(?:src|href)\s*=\s*["'](https?:)?//[^"']+""", html_text, re.I)
    ext += re.findall(r"""(?:fetch|XMLHttpRequest|import\s*\()\s*\(?\s*["']https?://""", html_text, re.I)
    if ext:
        probs.append("browser tool loads things from the internet: make it fully self-contained (works offline, "
                     "nothing leaves the buyer's computer)")
    if re.search(r"(?i)localStorage|indexedDB", html_text) and "privacy" not in low:
        probs.append("browser tool stores data in the browser: say so on the page (privacy) and offer a 'clear data' button")
    if len(html_text) < 1500:
        probs.append("browser tool is too small to be a real product")
    return probs[:8]


# ── Python project for coders: complete, runs first time (checked, never executed) ──
STDLIB = {"os", "sys", "re", "json", "csv", "math", "statistics", "datetime", "time", "pathlib", "random",
          "collections", "itertools", "functools", "argparse", "typing", "dataclasses", "logging", "unittest",
          "string", "decimal", "fractions", "io", "shutil", "glob", "textwrap", "copy", "enum", "abc", "zipfile",
          "sqlite3", "hashlib", "uuid", "tempfile", "subprocess", "urllib", "html", "xml", "calendar", "locale",
          "operator", "pprint", "warnings", "traceback", "heapq", "bisect", "contextlib", "inspect", "secrets"}


def python_project_problems(files):
    """files: {relative_path: text}. Static checks only: the office never runs AI-written code on your laptop."""
    import ast
    probs = []
    py = {p: t for p, t in files.items() if p.endswith(".py")}
    nb = {p: t for p, t in files.items() if p.endswith(".ipynb")}
    if not py and not nb:
        return ["Python project has no .py or .ipynb files"]
    imports = set()
    for p, t in py.items():
        try:
            tree = ast.parse(t)
        except SyntaxError as e:
            probs.append(f"{p}: syntax error on line {e.lineno}: {e.msg}")
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                imports.add(n.module.split(".")[0])
        if re.search(r"(?i)\b(TODO|FIXME|your[_ ]code[_ ]here|implement (this|me))\b", t):
            probs.append(f"{p}: contains TODO / unfinished parts")
    for p, t in nb.items():
        try:
            cells = json.loads(t).get("cells", [])
            if not cells:
                probs.append(f"{p}: notebook has no cells")
        except Exception:
            probs.append(f"{p}: notebook is not valid JSON")
    local = {Path(p).stem for p in py}
    third_party = sorted(i for i in imports if i not in STDLIB and i not in local and i != "__future__")
    req = next((t for p, t in files.items() if Path(p).name == "requirements.txt"), None)
    if third_party and req is None:
        probs.append(f"uses {', '.join(third_party)} but has no requirements.txt")
    elif third_party:
        missing = [m for m in third_party if m.lower().replace("_", "-") not in req.lower().replace("_", "-")
                   and not (m == "sklearn" and "scikit-learn" in req) and not (m == "yaml" and "pyyaml" in req.lower())
                   and not (m == "PIL" and "pillow" in req.lower()) and not (m == "bs4" and "beautifulsoup4" in req.lower())]
        if missing:
            probs.append(f"requirements.txt is missing: {', '.join(missing)}")
    readme = next((t for p, t in files.items() if Path(p).name.lower().startswith("readme")), "")
    if not readme:
        probs.append("no README with exact install + run steps")
    elif not re.search(r"(?i)(python3? |pip install|jupyter)", readme):
        probs.append("README doesn't show the exact commands to install and run it")
    reads_data = any(re.search(r"""read_csv|open\(\s*["'][^"']+\.(csv|xlsx|json|txt)""", t) for t in py.values())
    if reads_data and not any(p.endswith((".csv", ".json", ".xlsx", ".txt")) for p in files):
        probs.append("code reads data files but no sample data is included")
    from .privacy import NETWORK_CALLS
    if any(NETWORK_CALLS.search(t) for t in py.values()) and "network" not in readme.lower() and "internet" not in readme.lower():
        probs.append("code uses the internet but the README doesn't say what it connects to and why")
    return probs[:10]
