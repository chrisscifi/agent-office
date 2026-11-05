"""The office clock: two lanes (building / everything else) each take one job per tick, in parallel."""
import os
import random
import time
import traceback

from . import agents
from .core import AGENTS, CFG, kv_get, kv_update, x, kv_set, log, q, status, streams
from .llm import NoModelAvailable, budget_left, demo_mode

IDLE_LINES = ["☕ coffee break", "tidying my desk", "reading the inbox", "stretching", "humming", "watering the plant"]


def _in_flight(sid):
    return q("select count(*) n from products where stream=? and status in "
             "('idea','built','checked','revise','awaiting')", (sid,), one=True)["n"]


# Two lanes work at the same time: Bo builds while Mira, Sol, Maya and Abe check, research and write.
LANES = {"build": ["builder"], "office": ["master", "scout", "marketer", "accountant"]}
JOB_AGENT = {"build": "builder", "qa": "master", "market": "marketer", "scout_and_pick": "scout",
             "propose_department": "master", "create_requested_department": "master", "weekly_review": "master"}


def next_job(lane):
    if lane == "build":   # revisions first: they're closest to done
        for st in ("revise", "idea"):
            p = q("select * from products where status=? order by updated limit 1", (st,), one=True)
            if p:
                return agents.build, p
        return None
    # office lane: nearly-finished work first, so products reach your inbox sooner
    for st, fn in (("checked", agents.market), ("built", agents.qa)):
        p = q("select * from products where status=? order by updated limit 1", (st,), one=True)
        if p:
            return fn, p
    ids = [s["id"] for s in streams(active_only=True)]
    jump = kv_get("scout_next")   # a department that should get the very next scouting turn
    if jump:
        kv_set("scout_next", None)
        if jump in ids and _in_flight(jump) < CFG["loop"]["max_waiting_per_stream"]:
            kv_set("rr_last", jump)
            return agents.scout_and_pick, jump
    last = kv_get("rr_last")
    start = ids.index(last) + 1 if last in ids else 0
    for sid in ids[start:] + ids[:start]:  # fair rotation between departments
        if _in_flight(sid) < CFG["loop"]["max_waiting_per_stream"]:
            kv_set("rr_last", sid)
            return agents.scout_and_pick, sid
    return None


def idle_lane(lane):
    for aid in LANES[lane]:
        status(aid, "idle", random.choice(IDLE_LINES) if random.random() < .3 else "")


def idle_everyone(except_=()):
    for aid in AGENTS:
        if aid not in except_:
            status(aid, "idle", random.choice(IDLE_LINES) if random.random() < .3 else "")


def housekeeping():
    """Office-lane chores. Returns True if it used this turn."""
    now = time.time()
    if now - kv_get("last_prune", 0) > 86400:   # privacy: keep the activity log short
        kv_set("last_prune", now)
        days = CFG.get("privacy", {}).get("keep_activity_log_days", 90)
        x("delete from events where ts < ?", (now - days * 86400,))
    if now - kv_get("last_sync", 0) > 3600:
        kv_set("last_sync", now)
        agents.accountant_sync()
    if kv_get("last_review") is None:
        kv_set("last_review", now)  # first memo after one week of work
    if now - kv_get("last_review") > 7 * 86400:
        kv_set("last_review", now)
        agents.weekly_review()
        return True
    taken = []   # owner asked for a department → set it up first
    kv_update("dept_requests", lambda reqs: (taken.extend(reqs[:1]), reqs[1:])[1], [])
    if taken:
        agents.create_requested_department(taken[0])
        return True
    props = kv_get("dept_proposals", [])
    pending = any(p["status"] == "pending" for p in props)
    grow = CFG.get("growth", {})
    room = len(streams(active_only=True)) < grow.get("max_departments", 8)
    due = now - kv_get("last_proposal", 0) > grow.get("propose_every_days", 3) * 86400
    if not pending and room and (kv_get("want_proposal", False) or (due and grow.get("auto_propose", True))):
        kv_set("want_proposal", False)
        kv_set("last_proposal", now)
        agents.propose_department()
        return True
    return False


_current = {}   # lane -> agent doing the current job (for honest error messages)


def tick(lane="office"):
    if kv_get("paused", False):
        for aid in LANES[lane]:
            status(aid, "napping", "Office paused 💤")
        return
    if lane == "office":
        _current[lane] = "master"
        if housekeeping():
            return
    job = next_job(lane)
    idle_lane(lane)   # only this lane's agents relax; the other lane keeps working
    if job is None:
        if lane == "office":
            status("master", "idle", "Waiting for you to clear the inbox 📥")
            status("accountant", "idle", f"Paid-AI budget left today: €{max(0, budget_left()):.2f}")
        else:
            status("builder", "idle", "Nothing to build right now")
        return
    fn, arg = job
    _current[lane] = JOB_AGENT.get(fn.__name__, "master")
    key = f"fail:{arg['id'] if isinstance(arg, dict) else arg}"
    try:
        fn(arg)
        if kv_get(key, 0):
            kv_set(key, 0)  # a success wipes earlier hiccups, so 3 strikes means 3 in a row
    except NoModelAvailable:
        raise
    except Exception:
        # don't burn money retrying a broken job forever
        n = kv_get(key, 0) + 1
        kv_set(key, n)
        if isinstance(arg, dict) and n >= 3:
            x("update products set status='failed' where id=?", (arg["id"],))
            log("master", f"Gave up on #{arg['id']} after 3 failed attempts")
        raise


def rescue_recent_failures():
    """Products that failed in the last few days because of bugs since fixed get one more chance."""
    import json
    for r in q("select id, folder, qa from products where status='failed' and updated > ?", (time.time() - 4 * 86400,)):
        flag = f"rescued2:{r['id']}"
        if kv_get(flag):
            continue
        kv_set(flag, True)
        kv_set(f"fail:{r['id']}", 0)
        x("update products set status='revise', updated=? where id=?", (time.time(), r["id"]))
        log("master", f"Gave #{r['id']} another chance (it failed on a bug that's now fixed)")


def lane_loop(lane):
    while True:
        try:
            tick(lane)
        except NoModelAvailable as e:
            for aid in LANES[lane]:
                status(aid, "napping", "No AI available — check AI connections 💤")
            log("accountant", f"{e}. Napping 2 min, then retrying.")
            time.sleep(120)
        except Exception as e:
            log(_current.get(lane, "master"), f"Hit a snag: {e}")
            traceback.print_exc()
            idle_lane(lane)
        time.sleep(int(os.environ.get("DEMO_TICK", "6")) if demo_mode() else CFG["loop"]["tick_seconds"])


def loop():
    rescue_recent_failures()
    log("master", "Good morning, office is open." + (" (demo mode)" if demo_mode() else ""))
    import threading
    threading.Thread(target=lane_loop, args=("build",), daemon=True, name="build-lane").start()
    lane_loop("office")
