#!/usr/bin/env bash
# dev-reload.sh — reload the agent-usage@han extension without a shell restart.
#
# Requires GNOME Shell "unsafe mode", enabled once per login:
#   1. Press Alt+F2, type "lg", press Enter  (opens Looking Glass)
#   2. Toggle the "unsafe-mode" flag          (General section)
#   3. Close Looking Glass
#
# Then edit any file and run:  ./dev-reload.sh
#
# SECURITY: unsafe mode lets any process on the session bus run code inside
# GNOME Shell. Use it only on your own development machine.

set -e

UUID="agent-usage@han"
SHELL_PID=$(pgrep -x gnome-shell | head -1 || true)

if [ -z "$SHELL_PID" ]; then
    echo "gnome-shell is not running"
    exit 1
fi

RESULT=$(gdbus call --session \
    --dest org.gnome.Shell \
    --object-path /org/gnome/Shell \
    --method org.gnome.Shell.Eval '1+1' 2>/dev/null || true)

if [ "$RESULT" != "(true, '2')" ]; then
    cat <<'EOF'
unsafe mode is OFF. Enable it once per login:
  1. Alt+F2 -> type "lg" -> Enter   (opens Looking Glass)
  2. toggle the "unsafe-mode" flag
  3. close Looking Glass
Then re-run ./dev-reload.sh
EOF
    exit 1
fi

gdbus call --session \
    --dest org.gnome.Shell \
    --object-path /org/gnome/Shell \
    --method org.gnome.Shell.Eval \
    "Main.extensionManager.reloadExtension('$UUID')" >/dev/null

echo "reloaded $UUID"

sleep 1

ERRORS=$(journalctl --since '5 seconds ago' -o cat 2>/dev/null | grep "agent-usage" || true)
if [ -n "$ERRORS" ]; then
    echo "errors in the shell log:"
    echo "$ERRORS"
else
    echo "no errors in the shell log"
fi
