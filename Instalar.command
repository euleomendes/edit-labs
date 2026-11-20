#!/bin/bash
# Double-click once on a new computer to install what the video effects need.
cd "$(dirname "$0")"
bash setup.sh
echo
read -n 1 -s -r -p "Press any key to close this window."
