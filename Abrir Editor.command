#!/bin/bash
# Double-click to open Edit Labs. Keep this window open while you edit; close it to stop.
cd "$(dirname "$0")"
export PATH="$PWD/.runtime/env/bin:$HOME/.edite-reels/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
python3 server.py --open
