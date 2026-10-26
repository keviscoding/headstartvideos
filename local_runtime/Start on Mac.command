#!/bin/bash
cd "$(dirname "$0")" || exit 1

failed() {
  if [ -n "$1" ]; then
    echo "$1"
  fi
  echo ""
  read -r -p "Press Return to close. "
  exit 1
}

# Python is the one tool Studio can't install for itself (it runs on it). FFmpeg, Node.js and Claude Code are
# installed from Studio's setup page (Fix it), so these messages only ever ask for Python.
no_python() {
  echo ""
  echo "ChannelRecipe Studio needs Python 3.10 or newer, and this Mac doesn't have it yet."
  if [ -n "$1" ]; then
    echo "(This Mac has Python $1, which is too old for Studio.)"
  fi
  echo "It's the one thing you install yourself:"
  echo ""
  echo "  1. Your browser is opening python.org. Download the newest Python 3 for macOS (3.13 or newer)."
  echo "     If it didn't open, go to https://www.python.org/downloads/macos/"
  echo "  2. Open the downloaded file and click Continue until it says the installation was successful."
  echo "     It asks for your Mac password: that's Apple's installer, not Studio."
  echo "  3. Double-click \"Start on Mac.command\" again."
  echo ""
  echo "FFmpeg, Node.js and Claude Code don't need installing by hand: Studio's setup page installs them, one click each."
  open "https://www.python.org/downloads/macos/" >/dev/null 2>&1
  failed ""
}

if [ ! -f .venv/bin/python ]; then
  cr_python=""
  cr_too_old=""
  for candidate in python3.13 python3.12 python3 python3.11 python3.10; do
    resolved=$(command -v "$candidate") || continue
    if found=$("$resolved" -c 'import sys; print(sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null); then
      cr_python="$resolved"
      break
    fi
    [ -z "$cr_too_old" ] && cr_too_old="$found"
  done
  if [ -z "$cr_python" ]; then
    no_python "$cr_too_old"
  fi
  echo "Creating your studio environment with: $cr_python"
  "$cr_python" --version
  "$cr_python" -m venv .venv || failed "Python could not create the studio environment. Check the error above. Reinstalling Python from https://www.python.org/downloads/macos/ usually fixes it; then open this launcher again."
fi

.venv/bin/python -c 'import sys; print("Studio Python:", sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || failed "This Studio folder's Python environment (.venv) was made with a Python older than 3.10. To fix it: 1. Install Python 3.13 or newer from https://www.python.org/downloads/macos/ 2. In this folder, rename the .venv folder to .venv-old (it's hidden in Finder: press Cmd+Shift+. to show it). 3. Open this launcher again. Your output folder and saved accounts stay in place."

if [ ! -f .venv/.cr-installed ]; then
  echo "Setting up ChannelRecipe. This first download may take a few minutes."
  .venv/bin/python -m pip install -r requirements.txt || failed "The studio libraries could not finish installing. Check the error above and your connection, then reopen this launcher."
  .venv/bin/python -m playwright install chromium || failed "The graphics browser could not finish installing. Check the error above and your connection, then reopen this launcher."
  touch .venv/.cr-installed
fi
# The graphics browser is checked on every start, not only the first: a missing one is installed again here.
if ! .venv/bin/python -c 'import os, sys
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
ok = os.path.exists(p.chromium.executable_path)
p.stop()
sys.exit(0 if ok else 1)' >/dev/null 2>&1; then
  echo "Installing the graphics browser. This can take a few minutes."
  .venv/bin/python -m playwright install chromium || echo "The graphics browser could not install. Studio's setup page has a Fix it button for it."
fi
.venv/bin/python cr_studio.py
cr_result=$?
read -r -p "Press Return to close. "
exit "$cr_result"
