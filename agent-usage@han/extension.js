import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const REFRESH_SECONDS = 60;

function formatTokens(n) {
    if (n >= 1_000_000_000)
        return `${(n / 1_000_000_000).toFixed(2)}B`;
    if (n >= 1_000_000)
        return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000)
        return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function formatMoney(cost) {
    if (cost > 0 && cost < 0.01)
        return `$${cost.toFixed(4)}`;
    return `$${cost.toFixed(2)}`;
}

function formatDay(day) {
    return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
    });
}

function formatTime(ts) {
    return new Date(ts).toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
    });
}

class AgentUsageButton extends PanelMenu.Button {
    static {
        GObject.registerClass(this);
    }

    constructor(extension) {
        super(0.0, 'Agent Usage', false);
        this._extension = extension;
        this._label = new St.Label({
            text: '…',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._label);
        this._content = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._content);
        this.menu.connect('open-state-changed', (menu, open) => {
            if (open)
                this._maybeRefresh();
        });
        this._pending = null;
        this._lastRefresh = null;
        this._resetArmed = false;
        this._resetArmTimeout = null;
        this._refresh();
    }

    _maybeRefresh() {
        if (this._lastRefresh !== null && Date.now() - this._lastRefresh < 5000)
            return;
        this._refresh();
    }

    _runHelper(extraArgs = []) {
        if (this._pending)
            return this._pending;
        const proc = Gio.Subprocess.new(
            ['python3', `${this._extension.path}/usage.py`, ...extraArgs],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE,
        );
        const task = new Promise((resolve, reject) => {
            let settled = false;
            const finish = (fn, arg) => {
                if (settled)
                    return;
                settled = true;
                this._pending = null;
                fn(arg);
            };
            const timeoutId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 10, () => {
                finish(reject, new Error('usage.py timed out'));
                return GLib.SOURCE_REMOVE;
            });
            proc.communicate_utf8_async(null, null, (subprocess, res) => {
                GLib.source_remove(timeoutId);
                try {
                    const [ok, stdout] = subprocess.communicate_utf8_finish(res);
                    if (!ok)
                        throw new Error('usage.py exited with an error');
                    const data = JSON.parse(stdout.trim());
                    if (!data.ok)
                        throw new Error(data.error || 'usage.py reported an error');
                    finish(resolve, data);
                } catch (e) {
                    finish(reject, e);
                }
            });
        });
        this._pending = task;
        return task;
    }

    async _refresh() {
        try {
            const data = await this._runHelper();
            if (data.version !== 4)
                log(`agent-usage: unexpected schema version ${data.version}`);
            this._lastRefresh = Date.now();
            this._render(data);
        } catch (e) {
            log(`agent-usage: ${e}`);
        }
    }

    async _resetToday() {
        try {
            await this._runHelper(['--reset']);
            await this._refresh();
        } catch (e) {
            log(`agent-usage: reset failed: ${e}`);
        }
    }

    _row(text, bold = false) {
        const item = new PopupMenu.PopupMenuItem(text, {reactive: false});
        if (bold)
            item.label.clutter_text.weight = Pango.Weight.BOLD;
        return item;
    }

    _separator() {
        return new PopupMenu.PopupSeparatorMenuItem();
    }

    _render(data) {
        const {today, today_cutoff, week, month, total, per_model, last7,
            active, sources, errors} = data;
        const activeList = Array.isArray(active) ? active : active ? [active] : [];
        const errorList = Array.isArray(errors) ? errors : [];
        const todayNote = today_cutoff ? ` (since ${formatTime(today_cutoff)})` : '';

        this._label.text = today.cost > 0
            ? formatMoney(today.cost)
            : today.tokens > 0
                ? `${formatTokens(today.tokens)} tok`
                : formatMoney(0);

        this.tooltip_text =
            `Today${todayNote} ${formatMoney(today.cost)} · ${formatTokens(today.tokens)} tok\n` +
            `7 days ${formatMoney(week.cost)} · ${formatTokens(week.tokens)} tok\n` +
            `Month ${formatMoney(month.cost)} · ${formatTokens(month.tokens)} tok`;

        // Cancel any pending reset-confirmation timer: the menu items are
        // destroyed below, and the timer must never touch a destroyed actor.
        this._clearResetArm();

        this._content.removeAll();

        const nothing = today.cost === 0 && today.tokens === 0 &&
            week.cost === 0 && total.cost === 0;

        if (nothing) {
            this._content.addMenuItem(this._row('No usage recorded yet', true));
            this._content.addMenuItem(this._row('Run opencode, Claude Code, or Codex ' +
                'to start tracking'));
            this._content.addMenuItem(this._separator());
            const refreshItem = new PopupMenu.PopupMenuItem('Refresh');
            refreshItem.connect('activate', () => this._refresh());
            this._content.addMenuItem(refreshItem);
            return;
        }

        this._content.addMenuItem(this._row(
            `Today        ${formatMoney(today.cost)} · ${formatTokens(today.tokens)} tok${todayNote}`, true));
        this._content.addMenuItem(this._row(
            `This 7 days  ${formatMoney(week.cost)} · ${formatTokens(week.tokens)} tok`));
        this._content.addMenuItem(this._row(
            `This month   ${formatMoney(month.cost)} · ${formatTokens(month.tokens)} tok`));
        this._content.addMenuItem(this._row(
            `All time     ${formatMoney(total.cost)} · ${formatTokens(total.tokens)} tok`));

        this._content.addMenuItem(this._separator());
        this._content.addMenuItem(this._row(
            activeList.length > 0
                ? `● ${activeList.join(', ')} — active now`
                : 'Idle — no agent session running'));

        if (per_model.length > 0) {
            this._content.addMenuItem(this._separator());
            this._content.addMenuItem(this._row('Today by model', true));
            for (const m of per_model) {
                const amount = m.cost > 0 ? formatMoney(m.cost) : `${formatTokens(m.tokens)} tok`;
                this._content.addMenuItem(this._row(
                    `  ${m.model}  ${amount} · ${m.calls} call${m.calls === 1 ? '' : 's'}`));
            }
        }

        if (sources.length > 0) {
            this._content.addMenuItem(this._separator());
            this._content.addMenuItem(this._row('Today by source', true));
            for (const s of sources) {
                const amount = s.cost > 0 ? formatMoney(s.cost) : `${formatTokens(s.tokens)} tok`;
                this._content.addMenuItem(this._row(
                    `  ${s.source}  ${amount} · ${s.calls} call${s.calls === 1 ? '' : 's'}`));
            }
        }

        if (last7.length > 0) {
            this._content.addMenuItem(this._separator());
            this._content.addMenuItem(this._row('Last 7 days', true));
            for (const d of last7) {
                this._content.addMenuItem(this._row(
                    `  ${formatDay(d.day)}  ${formatMoney(d.cost)} · ${formatTokens(d.tokens)} tok`));
            }
        }

        if (errorList.length > 0) {
            this._content.addMenuItem(this._separator());
            for (const e of errorList) {
                const message = String(e.error || 'unknown error').slice(0, 60);
                this._content.addMenuItem(this._row(`⚠ ${e.source}: ${message}`));
            }
        }

        this._content.addMenuItem(this._separator());
        const asOf = new Date().toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
        });
        this._content.addMenuItem(this._row(`As of ${asOf}`));

        this._content.addMenuItem(this._separator());
        const resetItem = new PopupMenu.PopupMenuItem('Reset today');
        resetItem.connect('activate', () => {
            if (!this._resetArmed) {
                this._resetArmed = true;
                resetItem.label.text = 'Reset today — click again to confirm';
                this._resetArmTimeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
                    if (!this._resetArmed)
                        return GLib.SOURCE_REMOVE;
                    this._resetArmed = false;
                    this._resetArmTimeout = null;
                    resetItem.label.text = 'Reset today';
                    return GLib.SOURCE_REMOVE;
                });
                return;
            }
            this._resetArmed = false;
            this._resetArmTimeout = null;
            resetItem.label.text = 'Reset today';
            this._resetToday();
        });
        this._content.addMenuItem(resetItem);

        const refreshItem = new PopupMenu.PopupMenuItem('Refresh');
        refreshItem.connect('activate', () => this._refresh());
        this._content.addMenuItem(refreshItem);
    }

    _clearResetArm() {
        if (this._resetArmTimeout !== null) {
            GLib.source_remove(this._resetArmTimeout);
            this._resetArmTimeout = null;
        }
        this._resetArmed = false;
    }
}

export default class AgentUsageExtension extends Extension {
    _timer = null;
    _button = null;

    enable() {
        this._button = new AgentUsageButton(this);
        Main.panel.addToStatusArea('agent-usage', this._button, 0, 'right');
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, REFRESH_SECONDS, () => {
            this._button._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._timer !== null) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        if (this._button !== null) {
            this._button._clearResetArm();
            // The menu actor lives in Main.uiGroup, not in the button —
            // destroying only the button would leak it on every reload.
            this._button.menu?.destroy();
            this._button.destroy();
            this._button = null;
        }
    }
}
