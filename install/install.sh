#!/bin/sh
# Install the Colonization 3.0 fixes (install.py, Python 3): asks for your game folder.
d=$(dirname "$0")
if [ -f "$d/install/install.py" ]; then exec python3 "$d/install/install.py" "$@"; fi
exec python3 "$d/install.py" "$@"
