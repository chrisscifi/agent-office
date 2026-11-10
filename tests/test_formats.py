"""Finished-product pipeline: spreadsheets, documents, Dynamo graphs, browser tools, Python projects."""
import json

from openpyxl import load_workbook

SPEC = {"title": "Cash float", "instructions": "Type counts in yellow cells.",
        "sheets": [{"name": "Float", "columns": [{"header": "Note"}, {"header": "Value", "format": "dkk"},
                                                {"header": "Count", "format": "integer", "input": True},
                                                {"header": "Total", "format": "dkk"}],
                    "rows": [["1000 kr", 1000, 2, "=B2*C2"], ["Total", "", "", "=SUM(D2:D2)"]]},
                   {"name": "Log", "columns": [{"header": "Date", "format": "date", "input": True},
                                               {"header": "Counted"}, {"header": "Status", "input": True}],
                    "rows": [["2026-11-05", "=Float!D3", "OK"]],
                    "validation": [{"column": 2, "options": ["OK", "Check"]}]}]}


def test_spreadsheet_has_formulas_inputs_and_dropdowns(office, tmp_path):
    from agency.formats import spec_problems, spec_to_xlsx
    assert spec_problems(SPEC) == []
    out = tmp_path / "t.xlsx"
    spec_to_xlsx(SPEC, out)
    wb = load_workbook(out)
    assert wb.sheetnames == ["How to use", "Float", "Log"]
    assert wb["Float"]["D2"].value == "=B2*C2"
    assert wb["Log"]["B2"].value == "=Float!D3"
    assert wb["Log"].data_validations.dataValidation


def test_broken_spreadsheet_is_caught(office):
    from agency.formats import spec_problems
    bad = {"sheets": [{"name": "A", "columns": [{"header": "x"}], "rows": [["=SUM(B2:B3"], ["=Missing!A1"]]}]}
    problems = " ".join(spec_problems(bad))
    assert "unbalanced" in problems and "missing sheet" in problems


def test_documents_become_pdf_and_word_even_with_tricky_tables(office, tmp_path):
    from agency.formats import basic_pdf, md_to_docx
    text = "# SOP\n| Step | Note |\n|---|---|\n| **1** | Use `=SUM()` → **€** |\n\n- [ ] Done\n\nВаш номер готов ✓"
    basic_pdf(text, tmp_path / "a.pdf", "SOP")
    md_to_docx(text, tmp_path / "a.docx")
    assert (tmp_path / "a.pdf").stat().st_size > 1000 and (tmp_path / "a.docx").stat().st_size > 1000


def test_dynamo_tool_checks_and_graph(office, tmp_path):
    from agency.formats import dynamo_problems, spec_to_dyn
    good = {"title": "Renumber", "inputs": [{"name": "Prefix", "type": "string"},
                                            {"name": "Apply changes", "type": "boolean", "default": False}],
            "python": "import clr\nclr.AddReference('RevitServices')\n"
                      "from RevitServices.Transactions import TransactionManager\nprefix, apply = IN[0], IN[1]\n"
                      "if apply:\n    TransactionManager.Instance.EnsureInTransaction(doc)\nOUT = ['done']"}
    assert dynamo_problems(good) == []
    spec_to_dyn(good, tmp_path / "t.dyn")
    g = json.loads((tmp_path / "t.dyn").read_text())
    ports = {p["Id"] for n in g["Nodes"] for p in n["Inputs"] + n["Outputs"]}
    assert all(c["Start"] in ports and c["End"] in ports for c in g["Connectors"])
    assert all(n.get("Engine") == "PythonNet3" for n in g["Nodes"] if "Engine" in n)   # Dynamo 3.x/4.x engine
    bad = dict(good, python="Transaction(doc).Start()\nfor i in range(3)\n  pass", inputs=good["inputs"][:1])
    assert any("syntax" in p for p in dynamo_problems(bad))
    pyrevit = dict(good, python=good["python"] + "\ndoc = __revit__.ActiveUIDocument.Document")
    assert any("pyRevit" in p for p in dynamo_problems(pyrevit))
    old_api = dict(good, python=good["python"] + "\nids = [d.Id.IntegerValue for d in []]\nTransactionManager.Instance.TransactionTaskDone(doc)")
    found = " ".join(dynamo_problems(old_api))
    assert ".Value" in found and "no arguments" in found


def test_browser_tools_must_work_offline(office):
    from agency.formats import app_problems
    ok = "<!doctype html><html><body><input id=a><script>function f(){}</script>" + "x" * 1600 + "</body></html>"
    assert app_problems(ok) == []
    assert any("internet" in p for p in app_problems("<html><script src='https://cdn.example.com/x.js'></script><input>"))


def test_python_projects_must_run_first_time(office):
    from agency.formats import python_project_problems
    good = {"clean.py": "import pandas as pd\ndf = pd.read_csv('data/sample.csv')\nprint(df)\n",
            "requirements.txt": "pandas==2.2.3\n", "README.md": "pip install -r requirements.txt\npython clean.py",
            "data/sample.csv": "a,b\n1,2"}
    assert python_project_problems(good) == []
    bad = {"clean.py": "import pandas as pd\n# TODO implement this\ndf = pd.read_csv('x.csv')\n"}
    problems = " ".join(python_project_problems(bad))
    assert "requirements" in problems and "TODO" in problems and "README" in problems and "sample data" in problems
