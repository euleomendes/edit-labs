#!/bin/bash
# One-time setup on a new computer: installs what the video effects need.
# Safe to run again. The editor itself, the sounds and the effect previews are already in this folder.
set -e
cd "$(dirname "$0")"
export PATH="$HOME/.edite-reels/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "== Visual Builder setup =="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is missing. Install it from https://www.python.org/downloads/ and run this again."; exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing ffmpeg with Homebrew..."; brew install ffmpeg
  else
    echo "ffmpeg is missing. Install Homebrew (https://brew.sh), then run: brew install ffmpeg"; exit 1
  fi
fi

mkdir -p libraries work
if [ ! -d libraries/claude-video-effects ]; then
  echo "Downloading the 51 video effects (github.com/Vibha-Ramprakash/claude-video-effects)..."
  git clone --depth 1 https://github.com/Vibha-Ramprakash/claude-video-effects libraries/claude-video-effects
fi

echo "Installing the effects renderer (first time takes 1-3 minutes)..."
bash libraries/claude-video-effects/scripts/setup.sh

echo
echo "Done. Double-click 'Abrir Editor.command' to start."
