"""Model routing: free models first, automatic fallback, cool-downs, paid-model budget and trial window."""
import io
import time
import urllib.error

import pytest


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("https://example.invalid", code, "error", {}, io.BytesIO(body))


def test_extract_json_handles_code_blocks_inside_values(office):
    from agency.llm import extract_json
    assert extract_json('{"d": "```python\\nprint(1)\\n```", "p": 9}')["p"] == 9
    assert extract_json('Sure:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```python\nx=1\n```\n{"title": "after code"}') == {"title": "after code"}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_rate_limited_model_falls_back_to_next(office, monkeypatch):
    llm = office.llm
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    monkeypatch.setattr(llm, "demo_mode", lambda: False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = []

    def fake(provider, model, system, prompt, max_tokens):
        calls.append(provider)
        if provider == "groq":
            raise http_error(429, b'{"error": "rate limit"}')
        return "answer", model
    monkeypatch.setattr(llm, "_openai_call", fake)
    llm.CFG["models"]["t"] = ["groq:a", "gemini:b"]
    assert llm.ask("master", "t", "sys", "prompt") == "answer"
    assert calls == ["groq", "gemini"]
    assert not llm.usable("groq", "groq:a")          # resting after the rate limit
    assert llm.usable("gemini", "gemini:b")


def test_busy_errors_are_retried_before_switching(office, monkeypatch):
    llm = office.llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    attempts = []

    def flaky(provider, model, system, prompt, max_tokens):
        attempts.append(1)
        if len(attempts) < 3:
            raise http_error(503, b'{"error": {"message": "high demand"}}')
        return "ok", model
    monkeypatch.setattr(llm, "_openai_call", flaky)
    assert llm._call_with_retry("gemini", "m", "s", "p", 10) == ("ok", "m")
    assert len(attempts) == 3


def test_personal_data_is_scrubbed_from_prompts(office, monkeypatch):
    llm = office.llm
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(llm, "demo_mode", lambda: False)
    sent = {}
    monkeypatch.setattr(llm, "_openai_call", lambda p, m, system, prompt, mt: (sent.update(prompt=prompt) or ("ok", m)))
    llm.CFG["models"]["t"] = ["groq:a"]
    llm.ask("master", "t", "sys", "Guest anna@gmail.com, CPR 120389-1234")
    assert "anna@gmail.com" not in sent["prompt"] and "120389-1234" not in sent["prompt"]


def test_paid_model_stops_after_trial_window(office, monkeypatch):
    llm = office.llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm.usable("claude")
    office.core.kv_set("claude_trial_start", time.time() - 8 * 86400)
    assert not llm.usable("claude")
    office.core.kv_set("claude_keep", True)
    assert llm.usable("claude")


def test_paid_model_respects_budget_cap(office, monkeypatch):
    llm = office.llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    office.core.x("insert into ledger(ts,kind,stream,amount_eur,memo) values(?,?,?,?,?)",
                  (time.time(), "api_cost", None, -999.0, "test"))
    assert not llm.usable("claude")
