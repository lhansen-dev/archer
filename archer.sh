#!/bin/sh
# Launcher: symlinked to ~/.local/bin/archer
here=$(dirname "$(readlink -f "$0")")
PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m archer "$@"
