# Agent Usage (GNOME Shell extension)

Track how much your agentic AI usage costs — right from your GNOME top bar.

A GNOME Shell panel indicator that shows the dollar cost and token usage of your
[opencode](https://opencode.ai) sessions, read directly from opencode's local
database. No API keys, no network, no credentials — everything is computed
locally from the data opencode already stores.

## Features

- **Panel indicator** showing today's cost (e.g. `$0.42`) or token count for free models
- **Dropdown menu** with today / last 7 days / this month / all-time totals
- **Per-model breakdown** for today's sessions
- **Last-7-days history** with daily cost and tokens
- **Live activity** indicator when an agent session is running
- 60-second auto-refresh, plus refresh on menu open

## How it works

```
opencode sessions  →  ~/.local/share/opencode/opencode.db  →  usage.py  →  extension.js  →  panel
```

1. opencode writes one row per session into a local SQLite database, including
   `cost` (computed from its models.dev price catalog) and per-bucket token counts.
2. `usage.py` opens that database **read-only** and aggregates cost/tokens by day.
3. `extension.js` (GJS, GNOME 45+ ESM) runs `usage.py` on a timer, parses the JSON,
   and renders the panel label and popup menu.

The extension performs no pricing math itself — it displays the exact dollar
amounts opencode already computed, so historical sessions stay frozen at the
prices in effect when they ran.

## Requirements

- GNOME Shell 50 (tested; see below for other versions)
- Python 3 (with the standard library `sqlite3` module — present on all mainstream distros)
- opencode (data source)

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
  when the model is free.
- Click it for the full breakdown: today, 7 days, month, all time, per-model,
  last-7-days history, and a manual refresh item.
- The data updates automatically every 60 seconds.

## Files

| File            | Purpose                                              |
|-----------------|------------------------------------------------------|
| `agent-usage@han/extension.js` | The extension: panel button, popup menu, refresh timer |
| `agent-usage@han/usage.py`     | Read-only SQLite aggregator, prints JSON to stdout   |
| `agent-usage@han/metadata.json`| Extension metadata (uuid, shell-version)             |

## Troubleshooting

- **Extension doesn't appear after install** — log out and back in (Wayland).
- **`Tried to construct an object without a GType`** — update to a GJS version
  that requires registered GObject subclasses (already handled in the code).
- **No data shown** — confirm the database exists at
  `~/.local/share/opencode/opencode.db`, and check `python3 usage.py` output.
- Check the shell log for errors: `journalctl -b -o cat | grep agent-usage`

## Roadmap (v2 ideas)

- Support Claude Code / Codex JSONL transcripts
- End-of-session notifications with session cost
- Per-project breakdown
- Configurable refresh interval

## License

MIT — see [LICENSE](LICENSE).
