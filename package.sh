#!/bin/bash
# Build the EGO-upload zip.
#
# Important: extensions.gnome.org requires extension.js and metadata.json at
# the ZIP ROOT — do NOT wrap them in an "agent-usage@han/" folder, otherwise
# upload fails with "Missing extension.js".
set -euo pipefail

cd "$(dirname "$0")"
rm -f agent-usage@han.zip
cd agent-usage@han
zip -r ../agent-usage@han.zip extension.js metadata.json usage.py LICENSE
echo "Built agent-usage@han.zip"
