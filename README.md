# Agent Usage (GNOME Shell extension)

Track how much your agentic AI usage costs — right from your GNOME top bar.

A GNOME Shell panel indicator that shows the dollar cost and token usage of your
AI agents, read directly from their local data stores. No API keys, no network,
no credentials — everything is computed locally.

## Supported agents

| Agent | Data source | Cost | Notes |
|---|---|---|---|
| opencode | `~/.local/share/opencode/opencode.db` (SQLite) | exact — computed by opencode from its models.dev price catalog | |
| Claude Code | `~/.claude/projects/**/*.jsonl` | exact — `usage.costUSD` recorded per message | |
| Codex CLI | `~/.codex/sessions/**/*.jsonl` | **estimated** — Codex does not record cost, so `usage.py` multiplies tokens by a built-in price table | see disclaimer below |

Agents with no data on your machine are simply skipped. Each source is read
**read-only** and isolated — a broken transcript never affects the others.

## Features

- **Panel indicator** showing today's cost (e.g. `$0.42`), token count for free
  models, or `no usage` when there is none
- **Hover tooltip** with today / 7 days / month summary
- **Dropdown menu** with today / last 7 days / this month / all-time totals
- **Per-model and per-source breakdown** for today
- **Last-7-days history** with daily cost and tokens
- **Live activity** indicator listing every agent currently running
- 60-second auto-refresh, plus refresh on menu open
- Source read errors surface in the menu (`⚠ source: …`) instead of failing silently

## How it works

```
agent transcripts →  usage.py (adapters + aggregation)  →  extension.js  →  panel
```

1. Each agent keeps local records of its sessions and usage:
   - opencode stores `cost` and token buckets per session in SQLite,
     computed from its models.dev price catalog.
   - Claude Code stores per-message `usage` (tokens + `costUSD`) in JSONL
     transcripts.
   - Codex stores per-message token usage in JSONL transcripts (no cost).
2. `usage.py` opens each source **read-only**, normalizes everything into one
   record shape, and aggregates cost/tokens by day in a single pass.
3. `extension.js` (GJS, GNOME 45+ ESM) runs `usage.py` on a timer, parses the
   JSON, and renders the panel label and popup menu.

The extension performs no pricing math for opencode or Claude Code — it
displays the dollar amounts those tools already recorded, so historical
sessions stay frozen at the prices in effect when they ran.

### Codex cost disclaimer

Codex transcripts record per-request token usage (as `token_count` events) but
no cost. `usage.py` estimates cost with a small built-in price table
(`CODEX_PRICES` in `usage.py`, longest-prefix matched against the model name,
falling back to conservative defaults). This is an **estimate, not a bill** —
update the table if your model's pricing changes.

### Counting semantics

`calls` counts one usage record per agent: a session row for opencode, and one
API call for Claude Code / Codex messages. Claude Code sub-agent usage
(`usage.iterations`) is intentionally not included — the top-level per-message
usage is used, matching the behavior of other usage trackers.

## Requirements

- GNOME Shell 50 (tested; see below for other versions)
- Python 3 (standard library only)
- At least one supported agent (opencode, Claude Code, or Codex)

## Installation

```sh
mkdir -p ~/.local/share/gnome-shell/extensions
ln -sfn "$PWD/agent-usage@han" ~/.local/share/gnome-shell/extensions/agent-usage@han
```

Then enable it:

```sh
gnome-extensions enable agent-usage@han
```

**Note:** on Wayland, the shell reads the extension directory only at startup —
you must **log out and log back in** (or use the GNOME on Xorg session, where
`Alt+F2` → `r` restarts the shell instantly) for a newly added extension to load.

## Usage

- The panel button shows today's total: `$0.42` when there's a cost, `42K tok`
  when the models used today are free, `no usage` when nothing is recorded.
- Hovering shows today / 7 days / month in a tooltip.
- Click it for the full breakdown: today, 7 days, month, all time, per-model,
  per-source, last-7-days history, and a manual refresh item.
- The data updates automatically every 60 seconds.

## Development

Iterating on the extension on Wayland normally requires a logout/login for
every change. With GNOME Shell's "unsafe mode" you can reload instantly:

1. **Once per login:** press `Alt+F2`, type `lg`, press Enter (opens Looking
   Glass), toggle the **unsafe-mode** flag in the General section, close it.
2. Edit any file in `agent-usage@han/`.
3. Reload without restarting:

   ```sh
   ./dev-reload.sh
   ```

4. Check errors: `journalctl --since '10 seconds ago' -o cat | grep agent-usage`

`dev-reload.sh` verifies unsafe mode first and prints these steps if it's off.

> **Security:** unsafe mode lets any process on the session bus execute code
> inside GNOME Shell (that's how the reload works). Use it only on a machine
> you trust; it resets on every login.

## Files

| File                        | Purpose                                                   |
|-----------------------------|-----------------------------------------------------------|
| `agent-usage@han/extension.js` | The extension: panel button, popup menu, refresh timer  |
| `agent-usage@han/usage.py`     | Read-only multi-source aggregator, prints JSON to stdout |
| `agent-usage@han/metadata.json`| Extension metadata (uuid, shell-version)                 |
| `dev-reload.sh`                | Development helper: reload the extension without restart |

## Troubleshooting

- **Extension doesn't appear after install** — log out and back in (Wayland).
- **`Tried to construct an object without a GType`** — update to a GJS version
  that requires registered GObject subclasses (already handled in the code).
- **No data shown** — confirm one of the supported agents has data on this
  machine, and check `python3 agent-usage@han/usage.py` output.
- Check the shell log for errors: `journalctl -b -o cat | grep agent-usage`

## Roadmap

- Gemini CLI support (protobuf-backed, format is unstable across versions)
- End-of-session notifications with session cost
- Per-project breakdown
- Configurable refresh interval

## License

MIT — see [LICENSE](LICENSE).
