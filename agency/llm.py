"""One door to every AI model.

Each agent has a chain of models (config.toml → [models]). We try them in order and skip any that:
  - has no key / isn't running (Ollama),
  - is cooling down after a rate limit (free tiers run out → we just move on),
  - is paid (Claude) and the euro budget is spent.
So the office keeps working on free models, and only uses paid ones where you allow it.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from .core import CFG, api_spent, day_start, kv_get, kv_set, log, month_start, x
from . import mock


class NoModelAvailable(Exception):
    pass


BudgetExceeded = NoModelAvailable  # backwards compatible name

OPENAI_COMPAT = {
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama":     (os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/v1", None),
}
PAID = {"claude"}
WEB_SEARCH_EUR = 0.01
_cooldown = {}   # provider -> unix time when usable again
_disabled = {}   # provider -> reason (bad key etc.)
_ollama = {"checked": 0, "models": [], "up": False}
_claude = None


# ── availability ──────────────────────────────────────────────────────────
def ollama_models():
    if time.time() - _ollama["checked"] > 60:
        _ollama["checked"] = time.time()
        try:
            base = OPENAI_COMPAT["ollama"][0].rsplit("/v1", 1)[0]
            with urllib.request.urlopen(base + "/api/tags", timeout=2) as r:
                _ollama["models"] = [m["name"] for m in json.load(r).get("models", [])]
            _ollama["up"] = True
        except Exception:
            _ollama["models"], _ollama["up"] = [], False
    return _ollama["models"]


def has_key(provider):
    if provider == "claude":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "ollama":
        ollama_models()
        return _ollama["up"]
    env = OPENAI_COMPAT.get(provider, (None, None))[1]
    return bool(env and os.environ.get(env))


def budget_left():
    b = CFG["budget"]
    return min(b["daily_eur"] - api_spent(day_start()), b["monthly_eur"] - api_spent(month_start()))


def usable(provider, entry=None):
    if provider in _disabled or not has_key(provider):
        return False
    if _cooldown.get(provider, 0) > time.time() or (entry and _cooldown.get(entry, 0) > time.time()):
        return False
    if provider in PAID and (budget_left() <= 0 or not trial_active()):
        return False
    return True


def trial_info():
    """Claude runs as a trial: it switches itself off after N days unless you choose to keep it."""
    days = CFG.get("trial", {}).get("claude_trial_days", 7)
    start = kv_get("claude_trial_start")
    if has_key("claude") and start is None:
        start = time.time()
        kv_set("claude_trial_start", start)
        log("accountant", f"Claude trial started: {days} days, caps €{CFG['budget']['daily_eur']}/day")
    kept = kv_get("claude_keep", False)
    stopped = kv_get("claude_stopped", False)
    left = (start + days * 86400 - time.time()) / 86400 if start else days
    return {"start": start, "days": days, "days_left": max(0.0, left), "kept": kept, "stopped": stopped,
            "active": bool(start) and not stopped and (kept or left > 0)}


def trial_active():
    return trial_info()["active"]


def any_provider():
    return any(has_key(p) for p in list(OPENAI_COMPAT) + ["claude"])


def demo_mode():
    return os.environ.get("DEMO") == "1" or not any_provider()


def chain(model_key):
    c = CFG["models"].get(model_key, [])
    return [c] if isinstance(c, str) else c


def provider_status():
    out = {}
    for p in ["groq", "gemini", "openrouter", "ollama", "claude"]:
        st = "ready" if usable(p) else "off"
        if p in _disabled:
            st = "error: " + _disabled[p]
        elif has_key(p) and any(k.split(":")[0] == p and t > time.time() for k, t in _cooldown.items()):
            t = max(t for k, t in _cooldown.items() if k.split(":")[0] == p)
            st = f"ready, some models resting (limit) ~{int((t - time.time()) / 60) + 1} min"
        elif p == "ollama" and has_key(p) and not [m for m in ollama_models() if "cloud" not in m]:
            st = "running, but NO model downloaded. In PowerShell run: ollama pull qwen3.5:4b"
        elif p in PAID and has_key(p) and budget_left() <= 0:
            st = "budget reached"
        elif p in PAID and has_key(p) and not trial_active():
            st = "trial ended (switched off)" if not kv_get("claude_stopped") else "stopped by you"
        out[p] = {"status": st, "connected": has_key(p),
                  "models": ollama_models() if p == "ollama" else None,
                  "calls_today": kv_get(f"calls:{p}:{int(day_start())}", 0)}
    return out


# ── calling ───────────────────────────────────────────────────────────────
def _count(provider):
    k = f"calls:{provider}:{int(day_start())}"
    kv_set(k, kv_get(k, 0) + 1)


def _ollama_call(model, system, prompt, max_tokens):
    """Native Ollama API: lets us cap memory use and fall back to the CPU if the GPU driver crashes."""
    base = OPENAI_COMPAT["ollama"][0].rsplit("/v1", 1)[0]
    local_cfg = CFG.get("local", {})
    cloud = "cloud" in model

    def call(cpu):
        opts = {"temperature": 0.6, "num_predict": max_tokens}
        if not cloud:
            opts["num_ctx"] = local_cfg.get("num_ctx", 8192)
            if cpu:
                opts["num_gpu"] = 0  # run entirely on the processor...
                opts["num_thread"] = int(local_cfg.get("cpu_threads", 4))   # ...but leave most cores for you
        body = {"model": model, "stream": False, "options": opts,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if not kv_get("ollama_no_think_param", False):
            body["think"] = False   # thinking models (Qwen 3.x) otherwise spend the whole budget thinking
        req = urllib.request.Request(base + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "AgentOffice/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=300 if cloud else 3600) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if "think" in msg.lower() and "think" in body:   # older Ollama / model without thinking support
                kv_set("ollama_no_think_param", True)
                body.pop("think")
                req = urllib.request.Request(base + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json", "User-Agent": "AgentOffice/1.0"})
                with urllib.request.urlopen(req, timeout=300 if cloud else 3600) as r:
                    return json.load(r)
            raise urllib.error.HTTPError(e.url, e.code, msg[:300], e.headers, None)

    cpu = kv_get("ollama_cpu", False) or local_cfg.get("force_cpu", False)
    try:
        data = call(cpu)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        crashed = "terminated" in body or "0xc0000409" in body or "CUDA" in body
        if cloud or cpu or not crashed:
            raise urllib.error.HTTPError(e.url, e.code, body[:300], e.headers, None)
        kv_set("ollama_cpu", True)
        log("accountant", "Graphics card crashed running the local model — switching it to the processor (slower, but stable)")
        data = call(True)
    msg = data.get("message") or {}
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
    if not text and msg.get("thinking"):
        raise RuntimeError("model only produced thinking, no answer (try a bigger budget or a non-thinking model)")
    return text


def _openai_call(provider, model, system, prompt, max_tokens):
    base, env = OPENAI_COMPAT[provider]
    if provider == "ollama" and model == "auto":
        local = [m for m in ollama_models() if "cloud" not in m]
        if not local:
            raise RuntimeError("no local model downloaded yet")
        model = CFG.get("local", {}).get("model") or local[0]
        if model not in local:
            model = local[0]
    cap = CFG.get("limits", {}).get(f"{provider}_max_tokens")
    if cap:
        max_tokens = min(max_tokens, cap)
    if provider == "ollama":
        text = _ollama_call(model, system, prompt, max_tokens)
        if not text:
            raise RuntimeError("empty reply")
        return text, model
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0.6,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": "AgentOffice/1.0 (+personal use)"}   # 'Python-urllib' gets blocked by some firewalls
    if env:
        headers["Authorization"] = "Bearer " + os.environ[env]
    if provider == "openrouter":
        headers["X-Title"] = "Agent Office"
    req = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(), headers)
    timeout = 1800 if provider == "ollama" else 300   # laptops can be slow
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(str(data["error"])[:200])
    text = data["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models
    if not text:
        raise RuntimeError("empty reply")
    return text, model


def _busy(e):
    """Temporary trouble worth retrying: overloaded servers and dropped connections (VPNs do this a lot)."""
    code = getattr(e, "code", None)
    text = (type(e).__name__ + " " + str(e)).lower()
    return (code in (500, 502, 503, 504) or any(k in text for k in (
        "high demand", "overloaded", "unavailable", "remote end closed", "remotedisconnected", "connection reset",
        "connectionreset", "timed out", "timeout", "temporarily", "incompleteread", "broken pipe",
        "eof occurred", "unexpected_eof", "ssl", "connection aborted", "urlopen error")))


def _call_with_retry(provider, model, system, prompt, max_tokens, waits=(5, 20)):
    """Busy errors (503 'high demand') usually clear within seconds: retry a couple of times first."""
    for wait in list(waits) + [None]:
        try:
            return _openai_call(provider, model, system, prompt, max_tokens)
        except Exception as e:
            if wait is None or not _busy(e):
                raise
            time.sleep(wait)


def _claude_call(agent, model, system, prompt, max_tokens, stream, web_search):
    global _claude
    if _claude is None:
        import anthropic
        _claude = anthropic.Anthropic()
    kw = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": prompt}])
    if web_search:
        kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
    try:
        msg = _claude.messages.create(**kw)
    except Exception as e:
        if web_search and "tool" in str(e).lower():
            kw.pop("tools")
            msg = _claude.messages.create(**kw)
        else:
            raise
    price = CFG["pricing"].get(model, {"input": 3.0, "output": 15.0})
    u = msg.usage
    cost = (u.input_tokens * price["input"] + u.output_tokens * price["output"]) / 1e6
    cost += (getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0) * WEB_SEARCH_EUR
    x("insert into ledger(ts,kind,stream,amount_eur,memo) values(?,?,?,?,?)",
      (time.time(), "api_cost", stream, -round(cost, 5), f"{agent} · {model}"))
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _handle_error(provider, e, entry=None):
    key = entry or provider
    code = getattr(e, "code", None)
    detail = ""
    try:
        detail = e.read().decode()[:200] if hasattr(e, "read") else ""
    except Exception:
        pass
    detail = (detail + " " + str(e))[:300].strip()
    name = type(e).__name__
    if code == 429 or "rate" in detail.lower() or "RateLimit" in name or "quota" in detail.lower():
        wait = 15 * 60
        try:
            wait = max(60, int(e.headers.get("retry-after", wait)))
        except Exception:
            pass
        _cooldown[key] = time.time() + min(wait, 6 * 3600)
        log("accountant", f"{key} free limit reached — switching model for a while")
    elif "network settings" in detail.lower():
        _disabled[provider] = "blocked on this network (VPN/proxy/Wi-Fi) — try another network"
        log("accountant", f"{provider} refuses this network — using other models")
    elif "credits" in detail.lower() or "/plans" in detail.lower() or "subscription" in detail.lower():
        _cooldown[key] = time.time() + 24 * 3600
        log("accountant", f"{key} needs a paid plan — skipping it")
    elif provider == "ollama" and (code in (401, 403) or "sign" in detail.lower()):
        _cooldown[key] = time.time() + 6 * 3600
        log("accountant", f"{key}: not signed in to Ollama cloud (run 'ollama signin') — using local model")
    elif code == 401 or "invalid_api_key" in detail or "Invalid API Key" in detail or "Authentication" in name:
        _disabled[provider] = "key rejected: copy it again (no spaces) or create a new one"
        log("accountant", f"{provider}: key rejected")
    elif code == 403 or "PermissionDenied" in name:
        why = re.sub(r"<[^>]+>|\s+", " ", detail)[:90]
        _cooldown[key] = time.time() + 1800
        _disabled[provider] = f"access refused (403), not the key: {why}"
        log("accountant", f"{provider}: access refused (403): {why}")
    elif code == 413:
        _cooldown[key] = time.time() + 1800
        log("accountant", f"{key}: job too big for this free model — using another")
    elif code == 404 or "NotFound" in name:
        _cooldown[key] = time.time() + 3600
        log("accountant", f"{provider}: model not found ({detail[:80]}) — update config.toml [models]")
    else:
        _cooldown[key] = time.time() + 120
        log("accountant", f"{key} hiccup: {detail[:100] or name}")


def ask(agent, model_key, system, prompt, stream=None, max_tokens=4000, web_search=False, mock_key=None):
    # privacy: personal data (e-mails, phone/CPR/IBAN/card/passport numbers) never leaves the laptop
    if CFG.get("privacy", {}).get("redact_prompts", True):
        from .privacy import redact
        system, prompt = redact(system), redact(prompt)
    if demo_mode():
        time.sleep(float(os.environ.get("DEMO_DELAY", "1.5")))   # so you can watch the agents in demo mode
        return mock.reply(mock_key or agent, prompt)
    tried = []
    for entry in chain(model_key):
        provider, model = entry.split(":", 1)
        if not usable(provider, entry):
            continue
        tried.append(provider)
        try:
            _count(provider)
            if provider == "claude":
                text = _claude_call(agent, model, system, prompt, max_tokens, stream, web_search)
            else:
                text, model = _call_with_retry(provider, model, system, prompt, max_tokens)
            kv_set("last_model", {"agent": agent, "model": f"{provider}:{model}", "ts": time.time()})
            return text
        except Exception as e:
            _handle_error(provider, e, entry)
    raise NoModelAvailable("no AI model available right now" + (f" (tried {', '.join(tried)})" if tried else ""))


def extract_json(text):
    """Find the JSON object in a reply, even when the reply (e.g. a listing) contains ```python code blocks."""
    text = text.strip()
    candidates = [text]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)   # every fenced block, not just the first
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start >= 0:
        candidates.append(text[start:max(text.rfind("}"), text.rfind("]")) + 1])
    for c in candidates:
        c = c.strip()
        if not c or c[0] not in "{[":
            continue
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            try:
                return json.loads(c, strict=False)  # tolerate raw newlines inside strings
            except json.JSONDecodeError:
                continue
    raise ValueError("no valid JSON found in the model's reply")


def ask_json(agent, model_key, system, prompt, **kw):
    text = ask(agent, model_key, system, prompt + "\n\nReply with ONLY valid JSON, no commentary.", **kw)
    try:
        return extract_json(text)
    except Exception:
        kw.pop("web_search", None)
        text = ask(agent, model_key, "You fix broken JSON.",
                   f"Return this as valid JSON only, nothing else:\n\n{text[:12000]}", **kw)
        return extract_json(text)


def test_all():
    """Tiny ping to every connected provider, for the Connections card."""
    res = {}
    for p in ["groq", "gemini", "openrouter", "ollama", "claude"]:
        if not has_key(p):
            res[p] = "not connected"
            continue
        _disabled.pop(p, None)
        for k in [k for k in _cooldown if k.split(":")[0] == p]:
            _cooldown.pop(k)
        if p == "ollama" and not CFG.get("local", {}).get("force_cpu", False):
            kv_set("ollama_cpu", False)   # give the graphics card another chance (e.g. after a driver update)
        entries = list(dict.fromkeys(e for k in CFG["models"] for e in chain(k) if e.startswith(p + ":")))
        if not entries:
            res[p] = "connected, but not used in config.toml [models]"
            continue
        results = []
        for entry in entries:   # try every model of this provider, e.g. Flash AND Flash-Lite
            model = entry.split(":", 1)[1]
            try:
                if p == "claude":
                    _claude_call("accountant", model, "Reply with OK.", "Say OK", 10, None, False)
                else:
                    _, model = _call_with_retry(p, model, "Reply with OK.", "Say OK", 300, waits=(3,))
                results.append(f"{model}: OK")
            except Exception as e:
                _handle_error(p, e, entry)
                why = _disabled.get(p) or ("busy right now (Google's side), will retry" if _busy(e)
                                           else f"{getattr(e, 'code', '')} {str(e)[:80]}")
                results.append(f"{model}: {why}")
                if p in _disabled:
                    break
        ok = any(r.endswith(": OK") for r in results)
        res[p] = ("OK · " if ok else "failed · ") + " | ".join(results)
    return res
