#!/usr/bin/env python3
"""Read-only multi-source usage reporter for agentic AI tools.

Sources:
  - opencode     SQLite database at ~/.local/share/opencode/opencode.db
  - Claude Code  JSONL transcripts at ~/.claude/projects/**/*.jsonl
  - Codex CLI    JSONL transcripts at ~/.codex/sessions/**/*.jsonl

Outputs a single JSON object on stdout:
{
  "ok": bool, "error": str|null, "active": [str],
  "today":  {"cost": float, "tokens": int},
  "week":   {"cost": float, "tokens": int},
  "month":  {"cost": float, "tokens": int},
  "total":  {"cost": float, "tokens": int},
  "per_model": [{"model": str, "cost": float, "tokens": int, "sessions": int}],
  "sources": [{"source": str, "cost": float, "tokens": int, "sessions": int}],
  "last7":  [{"day": "YYYY-MM-DD", "cost": float, "tokens": int}],
  "errors": [{"source": str, "error": str}]
}

Never writes to any source. Each source is isolated: a failure in one does
not affect the others.
"""

import datetime
import glob
import json
import os
import sqlite3
import subprocess
import time

OPENCODE_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
CLAUDE_DIR = os.path.expanduser("~/.claude/projects")
CODEX_DIR = os.path.expanduser("~/.codex/sessions")
AGENTS = ("opencode", "claude", "codex")

# Estimated per-token prices for Codex models (USD per 1M tokens).
# Codex transcripts do not record cost, so this is an estimate, not a bill.
CODEX_PRICES = {
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5-pro": (1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "o3": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
}
CODEX_DEFAULT_PRICE = (0.25, 2.00)


# ---------------------------------------------------------------- sources

def _ts_ms(value):
    """Normalize a timestamp (ms, seconds, or ISO-8601 string) to ms."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            text = value.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return None
    if value < 1e12:
        value *= 1000
    return int(value)


def opencode_rows():
    rows = []
    if not os.path.exists(OPENCODE_DB):
        return rows
    con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout = 2000")
    for created, updated, cost, tin, tout, tcache, model in con.execute(
        "SELECT time_created, time_updated, cost,"
        " COALESCE(tokens_input, 0), COALESCE(tokens_output, 0),"
        " COALESCE(tokens_cache_read, 0), model"
        " FROM session"
    ):
        ts = created or updated
        if not ts:
            continue
        rows.append({
            "source": "opencode",
            "ts": _ts_ms(ts),
            "cost": cost or 0.0,
            "tin": tin,
            "tout": tout,
            "tcache": tcache,
            "model": opencode_model_label(model),
        })
    con.close()
    return rows


def opencode_model_label(raw):
    if not raw:
        return "unknown"
    try:
        m = json.loads(raw)
        label = m.get("id") or raw
        variant = m.get("variant")
        if variant and variant != "default":
            label = f"{label}\u00b7{variant}"
        return label
    except (ValueError, AttributeError):
        return raw


def claude_rows():
    rows = []
    if not os.path.isdir(CLAUDE_DIR):
        return rows
    cutoff = (time.time() - 40 * 86400) * 1000
    for path in glob.glob(os.path.join(CLAUDE_DIR, "**", "*.jsonl"), recursive=True):
        if os.path.getmtime(path) * 1000 < cutoff:
            continue
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                message = ev.get("message") or {}
                usage = message.get("usage")
                if not usage:
                    continue
                # Real transcripts carry costUSD at the event top level
                # (e.g. {"timestamp":..., "message":{...,"usage":{...}}, "costUSD":0.001})
                cost = ev.get("costUSD")
                if cost is None:
                    cost = usage.get("costUSD", 0.0)
                rows.append({
                    "source": "claude",
                    "ts": _ts_ms(ev.get("timestamp")),
                    "cost": cost or 0.0,
                    "tin": usage.get("input_tokens", 0),
                    "tout": usage.get("output_tokens", 0),
                    "tcache": usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0),
                    "model": message.get("model") or "claude",
                })
    return rows


def codex_rows():
    rows = []
    if not os.path.isdir(CODEX_DIR):
        return rows
    cutoff = (time.time() - 40 * 86400) * 1000
    for path in glob.glob(os.path.join(CODEX_DIR, "**", "*.jsonl"), recursive=True):
        if os.path.getmtime(path) * 1000 < cutoff:
            continue
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                ts = _ts_ms(ev.get("timestamp"))
                event_type = ev.get("type")
                if event_type == "token_count":
                    # Current format: per-request usage in payload.info
                    # (e.g. {"type":"token_count","timestamp":...,"payload":
                    #  {"info":{"model":...,"input_tokens":N,"output_tokens":N,
                    #   "cache_read_input_tokens":N},"total":{...}}})
                    info = (ev.get("payload") or {}).get("info") or {}
                    tin = info.get("input_tokens", 0)
                    tout = info.get("output_tokens", 0) \
                        + info.get("reasoning_output_tokens", 0)
                    if tin == 0 and tout == 0:
                        continue
                    tcache = info.get("cache_read_input_tokens", 0) \
                        or info.get("cached_input_tokens", 0)
                    model = info.get("model") or info.get("model_name") or "unknown"
                elif event_type == "message":
                    # Legacy format: usage on the assistant message
                    # (e.g. {"type":"message","message":{...,"usage":
                    #  {"prompt_tokens":N,"completion_tokens":N,"model":M}}})
                    usage = (ev.get("message") or {}).get("usage")
                    if not usage:
                        continue
                    tin = usage.get("prompt_tokens", 0)
                    tout = usage.get("completion_tokens", 0)
                    if tin == 0 and tout == 0:
                        continue
                    tcache = usage.get("cache_read_input_tokens", 0)
                    model = usage.get("model") \
                        or (ev.get("message") or {}).get("model") or "unknown"
                else:
                    continue
                rows.append({
                    "source": "codex",
                    "ts": ts,
                    "cost": codex_cost(model, tin, tout),
                    "tin": tin,
                    "tout": tout,
                    "tcache": tcache,
                    "model": model,
                })
    return rows


def codex_cost(model, tin, tout):
    """Estimated cost for a Codex message using the built-in price table."""
    rate = CODEX_DEFAULT_PRICE
    for prefix, price in CODEX_PRICES.items():
        if model.startswith(prefix):
            rate = price
            break
    return tin * rate[0] / 1e6 + tout * rate[1] / 1e6


# ---------------------------------------------------------------- activity

def is_active():
    active = []
    for agent in AGENTS:
        try:
            r = subprocess.run(
                ["pgrep", "-f", agent], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and r.stdout.strip():
                active.append(agent)
        except Exception:
            continue
    return active


# ------------------------------------------------------------ aggregation

def aggregate(rows):
    result = {
        "today": {"cost": 0.0, "tokens": 0},
        "week": {"cost": 0.0, "tokens": 0},
        "month": {"cost": 0.0, "tokens": 0},
        "total": {"cost": 0.0, "tokens": 0},
        "per_model": {},
        "sources": {},
        "last7": {},
    }
    now = time.time()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    week_cutoff = now - 6 * 86400
    month_prefix = datetime.datetime.now().strftime("%Y-%m")
    for r in rows:
        ts = r["ts"]
        if not ts:
            continue
        tokens = r["tin"] + r["tout"]
        cost = r["cost"]

        result["total"]["cost"] += cost
        result["total"]["tokens"] += tokens
        if ts >= week_cutoff * 1000:
            result["week"]["cost"] += cost
            result["week"]["tokens"] += tokens
        day = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        if day == today:
            result["today"]["cost"] += cost
            result["today"]["tokens"] += tokens
            key = r["model"] or "unknown"
            m = result["per_model"].setdefault(
                key, {"model": key, "cost": 0.0, "tokens": 0, "sessions": 0}
            )
            m["cost"] += cost
            m["tokens"] += tokens
            m["sessions"] += 1
            s = result["sources"].setdefault(
                r["source"], {"source": r["source"], "cost": 0.0, "tokens": 0, "sessions": 0}
            )
            s["cost"] += cost
            s["tokens"] += tokens
            s["sessions"] += 1
        if month_prefix == day[:7]:
            result["month"]["cost"] += cost
            result["month"]["tokens"] += tokens
        if ts >= week_cutoff * 1000:
            d = result["last7"].setdefault(day, {"day": day, "cost": 0.0, "tokens": 0})
            d["cost"] += cost
            d["tokens"] += tokens
    return result


def main():
    result = {
        "ok": True,
        "error": None,
        "active": is_active(),
        "today": {"cost": 0.0, "tokens": 0},
        "week": {"cost": 0.0, "tokens": 0},
        "month": {"cost": 0.0, "tokens": 0},
        "total": {"cost": 0.0, "tokens": 0},
        "per_model": [],
        "sources": [],
        "last7": [],
        "errors": [],
    }
    try:
        rows = []
        for name, fn in (("opencode", opencode_rows),
                         ("claude", claude_rows),
                         ("codex", codex_rows)):
            try:
                rows.extend(fn())
            except Exception as e:
                result["errors"].append({"source": name, "error": f"{type(e).__name__}: {e}"})

        agg = aggregate(rows)
        for key in ("today", "week", "month", "total"):
            result[key] = {
                "cost": round(agg[key]["cost"], 4),
                "tokens": agg[key]["tokens"],
            }
        result["per_model"] = sorted(
            agg["per_model"].values(), key=lambda m: -m["cost"])
        result["sources"] = sorted(
            agg["sources"].values(), key=lambda s: -s["cost"])
        result["last7"] = [
            {
                "day": d["day"],
                "cost": round(d["cost"], 4),
                "tokens": d["tokens"],
            }
            for d in sorted(agg["last7"].values(), key=lambda d: d["day"])
        ]
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(result))


if __name__ == "__main__":
    main()
