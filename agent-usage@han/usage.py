#!/usr/bin/env python3
"""Read-only usage reporter for the opencode SQLite database.

Outputs a single JSON object on stdout:
{
  "ok": bool, "error": str|null, "active": str|null,
  "today":  {"cost": float, "tokens": int},
  "week":   {"cost": float, "tokens": int},
  "month":  {"cost": float, "tokens": int},
  "total":  {"cost": float, "tokens": int},
  "per_model": [{"model": str, "cost": float, "tokens": int, "sessions": int}],
  "last7":  [{"day": "YYYY-MM-DD", "cost": float, "tokens": int}]
}
"""

import json
import os
import sqlite3
import subprocess

DB_PATH = os.path.expanduser("~/.local/share/opencode/opencode.db")
AGENTS = ("opencode", "claude", "codex")
TS = "COALESCE(time_created, time_updated) / 1000"
TOK = "COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0)"


def model_label(raw):
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


def is_active():
    for agent in AGENTS:
        try:
            r = subprocess.run(
                ["pgrep", "-f", agent], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0 and r.stdout.strip():
                return agent
        except Exception:
            continue
    return None


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
        "last7": [],
    }
    try:
        if not os.path.exists(DB_PATH):
            result["error"] = "opencode database not found"
            print(json.dumps(result))
            return
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        con.execute("PRAGMA busy_timeout = 2000")

        def agg(where):
            row = con.execute(
                f"SELECT COALESCE(SUM(cost), 0), COALESCE(SUM({TOK}), 0)"
                f" FROM session WHERE {where}"
            ).fetchone()
            return {"cost": round(row[0], 4), "tokens": row[1]}

        result["today"] = agg(
            f"date({TS}, 'unixepoch', 'localtime') = date('now', 'localtime')"
        )
        result["week"] = agg(f"{TS} >= unixepoch('now', '-6 days')")
        result["month"] = agg(
            f"strftime('%Y-%m', {TS}, 'unixepoch', 'localtime')"
            f" = strftime('%Y-%m', 'now', 'localtime')"
        )
        result["total"] = agg("1 = 1")

        result["per_model"] = [
            {
                "model": model_label(r[0]),
                "cost": round(r[1], 4),
                "tokens": r[2],
                "sessions": r[3],
            }
            for r in con.execute(
                f"SELECT model, COALESCE(SUM(cost), 0), COALESCE(SUM({TOK}), 0), COUNT(*)"
                f" FROM session"
                f" WHERE date({TS}, 'unixepoch', 'localtime') = date('now', 'localtime')"
                f" GROUP BY model ORDER BY COALESCE(SUM(cost), 0) DESC"
            )
        ]
        result["last7"] = [
            {"day": r[0], "cost": round(r[1], 4), "tokens": r[2]}
            for r in con.execute(
                f"SELECT date({TS}, 'unixepoch', 'localtime'),"
                f" COALESCE(SUM(cost), 0), COALESCE(SUM({TOK}), 0)"
                f" FROM session WHERE {TS} >= unixepoch('now', '-6 days')"
                f" GROUP BY 1 ORDER BY 1"
            )
        ]
        con.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(result))


if __name__ == "__main__":
    main()
