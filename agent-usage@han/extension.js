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
    if (n >= 1_000_000)
        return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000)
        return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function formatMoney(cost) {
    return `$${cost.toFixed(2)}`;
}

function formatDay(day) {
    return new Date(`${day}T00:00:00`).toLocaleDateString(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
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
                this._refresh();
        });
        this._pending = null;
        this._refresh();
    }

    _runHelper() {
        if (this._pending)
            return this._pending;
        const proc = Gio.Subprocess.new(
            ['python3', `${this._extension.path}/usage.py`],
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
            this._render(data);
        } catch (e) {
            this._label.text = '…';
            log(`agent-usage: ${e}`);
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
        const {today, week, month, total, per_model, last7, active} = data;

        this._label.text = today.cost > 0
            ? formatMoney(today.cost)
            : `${formatTokens(today.tokens)} tok`;

        this._content.removeAll();

        this._content.addMenuItem(this._row(
            `Today        ${formatMoney(today.cost)} · ${formatTokens(today.tokens)} tok`, true));
        this._content.addMenuItem(this._row(
            `This 7 days  ${formatMoney(week.cost)} · ${formatTokens(week.tokens)} tok`));
        this._content.addMenuItem(this._row(
            `This month   ${formatMoney(month.cost)} · ${formatTokens(month.tokens)} tok`));
        this._content.addMenuItem(this._row(
            `All time     ${formatMoney(total.cost)} · ${formatTokens(total.tokens)} tok`));

        this._content.addMenuItem(this._separator());
        this._content.addMenuItem(this._row(
            active ? `● ${active} — active now` : 'Idle — no agent session running'));

        if (per_model.length > 0) {
            this._content.addMenuItem(this._separator());
            this._content.addMenuItem(this._row('Today by model', true));
            for (const m of per_model) {
                const amount = m.cost > 0 ? formatMoney(m.cost) : `${formatTokens(m.tokens)} tok`;
                this._content.addMenuItem(this._row(
                    `  ${m.model}  ${amount} · ${m.sessions} session${m.sessions === 1 ? '' : 's'}`));
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

        this._content.addMenuItem(this._separator());
        const refreshItem = new PopupMenu.PopupMenuItem('Refresh');
        refreshItem.connect('activate', () => this._refresh());
        this._content.addMenuItem(refreshItem);
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
            this._button.destroy();
            this._button = null;
        }
    }
}
