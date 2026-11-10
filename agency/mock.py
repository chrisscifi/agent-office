"""Fake, free replies so you can watch the office work before adding an API key."""
import json
import random

IDEAS = [
    ("Night Audit Checklist Kit (EN/RO/RU/FR)", 12),
    ("Dynamo Room Renumbering Script + Guide", 15),
    ("Which Statistical Test? Decision Cheat Sheet", 6),
    ("Revit View Naming Standard Kit", 9),
    ("Shift Handover Template Pack for Hostels", 8),
    ("Pandas EDA Starter Script for Small Shops", 11),
]


def reply(key, prompt):
    if key == "scout":
        picks = random.sample(IDEAS, 3)
        return json.dumps({"ideas": [{"title": t, "buyer": "demo buyer", "problem": "demo problem",
                                      "format": "spreadsheet", "files": "Start Here + 2 files",
                                      "evidence": "demo mode: no real research done (assumed)", "sources": [],
                                      "free_alternative": "demo", "why_pay_anyway": "demo",
                                      "price_eur": p, "effort": "S"} for t, p in picks]})
    if key == "master_pick":
        return json.dumps({"scores": [{"idea": i, "evidence": random.randint(1, 5), "gap": random.randint(1, 5),
                                       "buildable": random.randint(2, 5), "owner_fit": random.randint(2, 5),
                                       "price": random.randint(2, 5), "note": "demo scoring",
                                       "build_notes": "keep it short"} for i in range(3)]})
    if key == "builder":
        return ("=== DOC: Start Here ===\n# Demo product\n\nMade in **demo mode**. Connect an AI and Bo writes the real thing.\n=== END ===\n"
                "=== DOC: Checklist ===\n# Checklist\n\n- [ ] Step one\n- [ ] Step two\n=== END ===\n"
                "=== SHEET: Calculator ===\n" + json.dumps({"title": "Demo calculator", "instructions": "Type amounts in yellow cells.",
                "sheets": [{"name": "Calc", "columns": [{"header": "Item"}, {"header": "Amount", "format": "eur", "input": True},
                {"header": "With VAT", "format": "eur"}], "rows": [["Example", 100, "=B2*1.25"], ["", "", "=B3*1.25"]]}]})
                + "\n=== END ===")
    if key == "marketer":
        return json.dumps({"title": "Demo listing title", "subtitle": "Demo subtitle",
                           "description": "Demo description — written by the Marketer once you add an API key.",
                           "price_eur": 9, "tags": ["demo", "template"],
                           "posts": [{"where": "LinkedIn", "text": "Demo post"}]})
    if key == "qa":
        return json.dumps({"score": random.choice([6, 7, 8, 9]), "verdict": "demo QA", "fix_notes": "demo: add an example"})
    if key == "dept":
        return json.dumps({"name": random.choice(["Wedding Planner Toolkits", "Landlord Admin Kits", "Café Operations Tools"]),
                           "channel": "Gumroad (digital download)", "brief": "demo brief", "research_queries": ["demo"],
                           "why": "demo reason"})
    if key == "memo":
        return "Demo memo: everything is quiet. Add an API key to start for real."
    return "{}"
