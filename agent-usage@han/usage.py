#!/usr/bin/env python3
"""Read-only multi-source usage reporter for agentic AI tools.

Sources:
  - opencode     SQLite database at ~/.local/share/opencode/opencode.db
  - Claude Code  JSONL transcripts at ~/.claude/projects/**/*.jsonl
  - Codex CLI    JSONL transcripts at ~/.codex/sessions/**/*.jsonl

Outputs a single JSON object on stdout:
{
  "version": int, "ok": bool, "error": str|null, "active": [str],
  "today":  {"cost": float, "tokens": int},
  "week":   {"cost": float, "tokens": int},
  "month":  {"cost": float, "tokens": int},
  "total":  {"cost": float, "tokens": int},
  "per_model": [{"model": str, "cost": float, "tokens": int, "calls": int}],
  "sources": [{"source": str, "cost": float, "tokens": int, "calls": int}],
  "last7":  [{"day": "YYYY-MM-DD", "cost": float, "tokens": int}],
  "errors": [{"source": str, "error": str}]
}

The "today" bucket only counts usage after the stored reset baseline when it
falls within the current local day; all other buckets stay calendar-based.

CLI:
  usage.py             report usage
  usage.py --reset     store a "since reset" baseline at the current time

Never writes to any source. State and scan caches live under the user's XDG
data/cache directories. Each source is isolated: a failure in one does not
affect the others.
"""

import datetime
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time

OPENCODE_DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
CLAUDE_DIR = os.path.expanduser("~/.claude/projects")
CODEX_DIR = os.path.expanduser("~/.codex/sessions")
AGENTS = ("opencode", "claude", "codex")

SCAN_WINDOW_DAYS = 40
XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
XDG_CACHE_HOME = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
STATE_FILE = os.path.join(XDG_DATA_HOME, "agent-usage@han", "state.json")
CACHE_FILE = os.path.join(XDG_CACHE_HOME, "agent-usage@han", "scan-cache.json")

# Estimated per-token prices for Codex models (USD per 1M tokens).
# Codex transcripts do not record cost, so this is an estimate, not a bill.
CODEX_PRICES = {
    "gpt-5-pro": (2.50, 12.50),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.5": (75.00, 150.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
    "o3": (1.10, 4.40),
}
CODEX_DEFAULT_PRICE = (0.25, 2.00)


# ---------------------------------------------------------------- helpers

def _to_float(value, default=0.0):
    """Coerce a value to float, tolerating strings and missing values."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _ts_ms(value, fallback_ms=None):
    """Normalize a timestamp (ms, seconds, or ISO-8601 string) to ms."""
    if value is None:
        return fallback_ms
    if isinstance(value, str):
        try:
            text = value.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.datetime.fromisoformat(text).timestamp() * 1000)
        except ValueError:
            return fallback_ms
    if value < 1e12:
        value *= 1000
    return int(value)


def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {"claude": {}, "codex": {}}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def _prune_cache_entries(entries, cutoff_ms):
    stale = [
        path for path, entry in entries.items()
        if (entry.get("m") or 0) // 1_000_000 < cutoff_ms
    ]
    for path in stale:
        del entries[path]
    if len(entries) > 2000:
        for path in sorted(entries, key=lambda p: entries[p].get("m", 0))[: len(entries) - 2000]:
            del entries[path]


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("reset_ts_ms")
    except (OSError, ValueError):
        return None


def _write_state(reset_ts_ms):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"reset_ts_ms": reset_ts_ms}, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- sources

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
            "cost": _to_float(cost),
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


def _claude_file_rows(path, mtime_ms):
    rows = []
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
                "ts": _ts_ms(ev.get("timestamp"), mtime_ms),
                "cost": _to_float(cost),
                "tin": usage.get("input_tokens", 0),
                "tout": usage.get("output_tokens", 0),
                "tcache": usage.get("cache_read_input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0),
                "model": message.get("model") or "claude",
            })
    return rows


def _codex_file_rows(path, mtime_ms):
    rows = []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            ts = _ts_ms(ev.get("timestamp"), mtime_ms)
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


def _scan_jsonl_dir(root, cache_key, parser):
    """Scan a JSONL directory, caching per-file results by mtime.

    Returns (rows, errors). A single unreadable or corrupt file never
    discards the rest of the source.
    """
    rows = []
    errors = []
    if not os.path.isdir(root):
        return rows, errors
    cache = _load_cache()
    entries = cache.setdefault(cache_key, {})
    cutoff = (time.time() - SCAN_WINDOW_DAYS * 86400) * 1000
    changed = {}
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            st = os.stat(path)
        except OSError as e:
            errors.append(f"{path}: {e}")
            continue
        mtime_ns = st.st_mtime_ns
        if mtime_ns // 1_000_000 < cutoff:
            continue
        entry = entries.get(path)
        if entry and entry.get("m") == mtime_ns:
            rows.extend(entry["r"])
            continue
        try:
            file_rows = parser(path, mtime_ns // 1_000_000)
        except Exception as e:
            errors.append(f"{path}: {type(e).__name__}: {e}")
            continue
        changed[path] = {"m": mtime_ns, "r": file_rows}
        rows.extend(file_rows)
    if changed:
        entries.update(changed)
        _prune_cache_entries(entries, cutoff)
        _save_cache(cache)
    return rows, errors


def claude_rows():
    return _scan_jsonl_dir(CLAUDE_DIR, "claude", _claude_file_rows)


def codex_rows():
    return _scan_jsonl_dir(CODEX_DIR, "codex", _codex_file_rows)


def codex_cost(model, tin, tout):
    """Estimated cost for a Codex message using the built-in price table."""
    rate = CODEX_DEFAULT_PRICE
    best_len = -1
    for prefix, price in CODEX_PRICES.items():
        if model.startswith(prefix) and len(prefix) > best_len:
            best_len = len(prefix)
            rate = price
    return tin * rate[0] / 1e6 + tout * rate[1] / 1e6


# ---------------------------------------------------------------- activity

def is_active():
    active = []
    try:
        r = subprocess.run(
            ["pgrep", "-af", "opencode|claude|codex"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            for agent in AGENTS:
                for line in r.stdout.splitlines():
                    if agent in line:
                        active.append(agent)
                        break
    except Exception:
        pass
    return active


# ------------------------------------------------------------ aggregation

def aggregate(rows, baseline_ms=None):
    """Aggregate normalized rows into day/period buckets.

    When baseline_ms falls within the current local day, the "today" bucket
    (and today's per-model/per-source breakdowns) only count usage after it.
    All other buckets stay calendar-based.
    """
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
    now_ms = int(now * 1000)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    day_start_ms = int(datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    today_cutoff = day_start_ms
    if baseline_ms and day_start_ms < baseline_ms <= now_ms:
        today_cutoff = baseline_ms
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
        if day == today and ts >= today_cutoff:
            result["today"]["cost"] += cost
            result["today"]["tokens"] += tokens
            key = r["model"] or "unknown"
            m = result["per_model"].setdefault(
                key, {"model": key, "cost": 0.0, "tokens": 0, "calls": 0}
            )
            m["cost"] += cost
            m["tokens"] += tokens
            m["calls"] += 1
            s = result["sources"].setdefault(
                r["source"], {"source": r["source"], "cost": 0.0, "tokens": 0, "calls": 0}
            )
            s["cost"] += cost
            s["tokens"] += tokens
            s["calls"] += 1
        if month_prefix == day[:7]:
            result["month"]["cost"] += cost
            result["month"]["tokens"] += tokens
        if ts >= week_cutoff * 1000:
            d = result["last7"].setdefault(day, {"day": day, "cost": 0.0, "tokens": 0})
            d["cost"] += cost
            d["tokens"] += tokens
    return result


def main():
    reset = "--reset" in sys.argv
    result = {
        "version": 3,
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
                got = fn()
                if isinstance(got, tuple):
                    source_rows, file_errors = got
                    rows.extend(source_rows)
                    for err in file_errors:
                        result["errors"].append({"source": name, "error": err})
                else:
                    rows.extend(got)
            except Exception as e:
                result["errors"].append({"source": name, "error": f"{type(e).__name__}: {e}"})

        if reset:
            _write_state(int(time.time() * 1000))
        baseline = _load_state()

        agg = aggregate(rows, baseline)
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
