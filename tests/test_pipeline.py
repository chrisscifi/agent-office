"""End-to-end in demo mode: idea -> build -> QA -> listing -> inbox, in two parallel lanes."""
import json


def run_lane(runner, lane, n):
    for _ in range(n):
        runner.tick(lane)


def test_product_flows_from_idea_to_inbox(office):
    runner, core = office.runner, office.core
    core.kv_set("last_proposal", 9e12)            # no department proposals during this test
    run_lane(runner, "office", 1)                  # Sol researches, Mira greenlights
    assert core.q("select status from products", one=True)["status"] == "idea"
    run_lane(runner, "build", 1)                   # Bo builds
    p = core.q("select * from products", one=True)
    assert p["status"] == "built"
    files = sorted(f.name for f in (core.OUTPUT / p["folder"] / "files").iterdir())
    assert "Calculator.xlsx" in files and "Start Here.pdf" in files and "Start Here.docx" in files
    for _ in range(4):                             # QA (maybe one auto-revision) and listing
        run_lane(runner, "office", 1)
        run_lane(runner, "build", 1)
    p = core.q("select * from products", one=True)
    assert p["status"] == "awaiting"
    assert core.q("select status from approvals", one=True)["status"] == "pending"
    assert json.loads(p["qa"])["score"] >= 1


def test_lanes_split_the_work(office):
    runner, core = office.runner, office.core
    pid = core.x("insert into products(stream,title,status,idea,created,updated) values('bim','A','built','{}',0,0)")
    core.x("insert into products(stream,title,status,idea,created,updated) values('bim','B','revise','{}',0,0)")
    fn, arg = runner.next_job("office")
    assert fn.__name__ == "qa" and arg["id"] == pid      # Mira checks finished work...
    fn, arg = runner.next_job("build")
    assert fn.__name__ == "build" and arg["title"] == "B"   # ...while Bo revises another product


def test_code_files_never_reach_the_buyer(office, monkeypatch):
    agents, core = office.agents, office.core
    reply = ("=== DOC: Start Here ===\n# Kit\nUse it.\n=== END ===\n=== DOC: Guide ===\n# Guide\nSteps.\n=== END ===\n"
             "=== FILE: helper.py ===\nprint('raw script')\n=== END ===")
    monkeypatch.setattr(agents, "ask", lambda *a, **k: reply)
    pid = core.x("insert into products(stream,title,status,idea,created,updated) values('bim','Kit','idea','{}',0,0)")
    agents.build(core.q("select * from products where id=?", (pid,), one=True))
    folder = core.OUTPUT / core.q("select folder from products where id=?", (pid,), one=True)["folder"]
    assert not list((folder / "files").glob("*.py"))


def test_real_personal_data_in_a_product_is_flagged(office, monkeypatch):
    agents, core = office.agents, office.core
    reply = ("=== DOC: Start Here ===\n# Guest pack\nExample: maria.popescu@yahoo.com, +40 723 456 781\n=== END ===\n"
             "=== DOC: Guide ===\n# Guide\nSteps.\n=== END ===")
    monkeypatch.setattr(agents, "ask", lambda *a, **k: reply)
    pid = core.x("insert into products(stream,title,status,idea,created,updated) values('bim','Pack','idea','{}',0,0)")
    agents.build(core.q("select * from products where id=?", (pid,), one=True))
    folder = core.OUTPUT / core.q("select folder from products where id=?", (pid,), one=True)["folder"]
    problems = json.loads((folder / "source" / "_checks.json").read_text())["problems"]
    assert any("e-mail" in p for p in problems) and any("phone" in p for p in problems)


def _failing_product(core, tries):
    """A built product whose review keeps failing, after `tries` automatic revisions."""
    pid = core.x("insert into products(stream,title,status,idea,folder,created,updated) "
                 "values('bim','Weak kit','built','{}','bim/weak',0,0)")
    (core.OUTPUT / "bim" / "weak" / "files").mkdir(parents=True, exist_ok=True)
    (core.OUTPUT / "bim" / "weak" / "files" / "Start Here.md").write_text("# Start", encoding="utf-8")
    core.kv_set(f"qa_tries:{pid}", tries)
    return pid


def test_weak_product_is_sent_back_first(office, monkeypatch):
    agents, core = office.agents, office.core
    monkeypatch.setattr(agents, "ask_json", lambda *a, **k: {"score": 3, "verdict": "missing tool", "fix_notes": "add it"})
    pid = _failing_product(core, 0)
    agents.qa(core.q("select * from products where id=?", (pid,), one=True))
    assert core.q("select status from products where id=?", (pid,), one=True)["status"] == "revise"


def test_still_failing_after_revisions_is_shelved_not_sent_on(office, monkeypatch):
    agents, core = office.agents, office.core
    monkeypatch.setattr(agents, "ask_json", lambda *a, **k: {"score": 3, "verdict": "missing tool", "fix_notes": "add it"})
    pid = _failing_product(core, 2)
    agents.qa(core.q("select * from products where id=?", (pid,), one=True))
    assert core.q("select status from products where id=?", (pid,), one=True)["status"] == "dropped"


def test_borderline_after_revisions_still_reaches_the_owner(office, monkeypatch):
    agents, core = office.agents, office.core
    monkeypatch.setattr(agents, "ask_json", lambda *a, **k: {"score": 6, "verdict": "ok-ish", "fix_notes": "polish"})
    pid = _failing_product(core, 2)
    agents.qa(core.q("select * from products where id=?", (pid,), one=True))
    assert core.q("select status from products where id=?", (pid,), one=True)["status"] == "checked"
