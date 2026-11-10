"""Scout research, office memory and Mira's scored, shuffled pick."""
import io
import json


def test_web_search_needs_a_key(office, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert office.agents.web_search("revit dynamo") == []


def test_web_search_returns_results_and_respects_the_daily_cap(office, monkeypatch):
    agents, core = office.agents, office.core
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    core.CFG.setdefault("research", {})["web_search_daily_cap"] = 2
    sent = []

    def fake_urlopen(req, timeout=0):
        sent.append(json.loads(req.data))
        body = {"results": [{"title": "Revit audit tool", "url": "https://example.com/a", "content": "Buyers ask for it"}]}
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(agents.urllib.request, "urlopen", fake_urlopen)
    first = agents.web_search("revit view audit", include_domains=["etsy.com"])
    assert first == [{"title": "Revit audit tool", "url": "https://example.com/a", "snippet": "Buyers ask for it"}]
    assert sent[0]["include_domains"] == ["etsy.com"]
    agents.web_search("second")
    assert agents.web_search("third") == []          # cap of 2 reached: no third request
    assert len(sent) == 2


def test_search_queries_are_privacy_scrubbed(office, monkeypatch):
    agents = office.agents
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    sent = []
    monkeypatch.setattr(agents.urllib.request, "urlopen",
                        lambda req, timeout=0: sent.append(json.loads(req.data)) or io.BytesIO(b'{"results": []}'))
    agents.web_search("templates for maria.popescu@yahoo.com")
    assert "maria.popescu@yahoo.com" not in sent[0]["query"]


def test_office_memory_reports_quality_and_owner_decisions(office):
    agents, core = office.agents, office.core
    folder = core.OUTPUT / "bim" / "kit"
    (folder / "files").mkdir(parents=True)
    (folder / "files" / "Tool.dyn").write_text("{}")
    pid = core.x("insert into products(stream,title,status,idea,folder,qa,built_with,created,updated) values"
                 "('bim','Door Renumber Tool','approved','{}','bim/kit',?,'gemini:flash',0,0)", (json.dumps({"score": 8}),))
    drop = core.x("insert into products(stream,title,status,idea,created,updated) values('bim','Wall Art Pack','dropped','{}',0,0)")
    core.x("insert into approvals(product_id,status,created) values(?,?,0)", (drop, "dropped"))
    mem = agents.office_memory("bim")
    assert "Dynamo tool built with gemini: average 8.0/10" in mem
    assert "Door Renumber Tool" in mem and "Wall Art Pack" in mem
    assert pid


def _three_ideas():
    return {"ideas": [{"title": t, "buyer": "b", "price_eur": 9} for t in ("Alpha", "Bravo", "Charlie")]}


def test_mira_builds_the_highest_scoring_idea_whatever_the_order(office, monkeypatch):
    agents, core = office.agents, office.core
    core.kv_set("last_proposal", 9e12)
    monkeypatch.setattr(agents, "reddit_signals", lambda *a, **k: "")
    monkeypatch.setattr(agents, "web_research", lambda *a, **k: "")
    shown = {}

    def fake_ask_json(agent, key, system, prompt, **kw):
        if agent == "scout":
            return _three_ideas()
        order = json.loads(prompt.split("(numbered from 0):\n", 1)[1].split("\nDepartment brief", 1)[0])
        shown["order"] = [t["title"] for _, t in order]
        scores = []
        for i, t in order:   # Bravo is clearly best, wherever the shuffle put it
            v = 5 if t["title"] == "Bravo" else 2
            scores.append({"idea": i, "evidence": v, "gap": v, "buildable": v, "owner_fit": v, "price": v,
                           "note": t["title"], "build_notes": "notes for " + t["title"]})
        return {"scores": scores}
    monkeypatch.setattr(agents, "ask_json", fake_ask_json)
    sid = core.streams()[0]["id"]
    agents.scout_and_pick(sid)
    p = core.q("select * from products order by id desc", one=True)
    idea = json.loads(p["idea"])
    assert p["title"] == "Bravo" and idea["build_notes"] == "notes for Bravo"
    assert idea["mira_scores"]["total"] == 35 and idea["mira_scores"]["max"] == 35
    assert sorted(shown["order"]) == ["Alpha", "Bravo", "Charlie"]


def test_old_style_answer_still_works(office, monkeypatch):
    agents, core = office.agents, office.core
    monkeypatch.setattr(agents, "reddit_signals", lambda *a, **k: "")
    monkeypatch.setattr(agents, "web_research", lambda *a, **k: "")
    monkeypatch.setattr(agents, "ask_json", lambda agent, *a, **k: _three_ideas() if agent == "scout"
                        else {"pick": 1, "reason": "old format", "build_notes": "n"})
    agents.scout_and_pick(core.streams()[0]["id"])
    assert core.q("select count(*) n from products", one=True)["n"] == 1
