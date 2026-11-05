"""Config, keys, database and small shared helpers."""
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
KEYS_FILE = ROOT / "keys.env"
DATA.mkdir(exist_ok=True)
OUTPUT.mkdir(exist_ok=True)

try:  # Windows consoles choke on emoji otherwise
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CFG = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8-sig"))

KEY_NAMES = ["GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"]


def load_keys():
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ[k.strip()] = v.strip()


def save_key(name, value):
    if name not in KEY_NAMES:
        raise ValueError("unknown key")
    keys = {}
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    keys[name] = value.strip()
    if value.strip():
        os.environ[name] = value.strip()
    else:
        os.environ.pop(name, None)
    KEYS_FILE.write_text("# Your API keys. Keep this file private.\n" +
                         "\n".join(f"{k}={v}" for k, v in keys.items()) + "\n", encoding="utf-8")


load_keys()

AGENTS = {
    "master":     {"name": "Mira",  "role": "Master / manager & quality control"},
    "scout":      {"name": "Sol",   "role": "Scout / market research"},
    "builder":    {"name": "Bo",    "role": "Builder / makes products"},
    "marketer":   {"name": "Maya",  "role": "Marketer / listings & posts"},
    "accountant": {"name": "Abe",   "role": "Accountant / money & budget"},
}

SCHEMA = """
create table if not exists agents(id text primary key, state text, bubble text, target text, updated real);
create table if not exists products(
  id integer primary key autoincrement, stream text, title text, status text,
  idea text, folder text, listing text, url text, feedback text, created real, updated real, qa text);
create table if not exists approvals(
  id integer primary key autoincrement, product_id integer, status text, created real, decided real);
create table if not exists ledger(
  id integer primary key autoincrement, ts real, kind text, stream text,
  amount_eur real, memo text, ext_id text unique);
create table if not exists events(id integer primary key autoincrement, ts real, agent text, text text);
create table if not exists kv(k text primary key, v text);
"""

_lock = threading.RLock()
_conn = sqlite3.connect(DATA / "agency.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(SCHEMA)
for _col in ("qa text", "built_with text", "build_cost real"):
    try:
        _conn.execute(f"alter table products add column {_col}")
    except sqlite3.OperationalError:
        pass
for aid in AGENTS:
    _conn.execute("insert or ignore into agents values(?,?,?,?,?)", (aid, "idle", "", None, time.time()))
_conn.commit()


def q(sql, args=(), one=False):
    with _lock:
        rows = [dict(r) for r in _conn.execute(sql, args).fetchall()]
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with _lock:
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur.lastrowid


def kv_get(k, default=None):
    r = q("select v from kv where k=?", (k,), one=True)
    return json.loads(r["v"]) if r else default


def kv_set(k, v):
    x("insert into kv values(?,?) on conflict(k) do update set v=excluded.v", (k, json.dumps(v)))


def kv_update(k, fn, default=None):
    """Read-modify-write a value atomically (the office and the web page both change things)."""
    with _lock:
        v = fn(kv_get(k, default))
        kv_set(k, v)
        return v


def log(agent, text):
    x("insert into events(ts,agent,text) values(?,?,?)", (time.time(), agent, text))
    print(f"[{datetime.now():%H:%M:%S}] {AGENTS.get(agent, {'name': agent})['name']:>5}: {text}", flush=True)


def status(agent, state, bubble="", target=None):
    """state: idle | thinking | working | walking | napping. target: agent id or 'inbox'."""
    x("update agents set state=?, bubble=?, target=?, updated=? where id=?",
      (state, bubble[:140], target, time.time(), agent))


def all_stream_defs():
    """Departments from config.toml plus the ones added later from the office (stored in the database)."""
    return list(CFG["streams"]) + [dict(d, custom=True) for d in kv_get("custom_streams", [])]


def streams(active_only=False):
    paused = kv_get("paused_streams", [])
    out = []
    for s in all_stream_defs():
        s = dict(s, paused=s["id"] in paused)
        if active_only and (not s.get("active", True) or s["paused"]):
            continue
        out.append(s)
    return out


def stream(sid):
    return next(s for s in all_stream_defs() if s["id"] == sid)


def month_start():
    n = datetime.now()
    return datetime(n.year, n.month, 1).timestamp()


def day_start():
    n = datetime.now()
    return datetime(n.year, n.month, n.day).timestamp()


def api_spent(since):
    return -q("select coalesce(sum(amount_eur),0) s from ledger where kind='api_cost' and ts>=?",
              (since,), one=True)["s"]
