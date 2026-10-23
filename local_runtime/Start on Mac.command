#!/bin/bash
cd "$(dirname "$0")" || exit 1

failed() {
  echo "$1"
  read -r -p "Press Return to close. "
  exit 1
}

if [ ! -f .venv/bin/python ]; then
  cr_python=""
  for candidate in python3.12 python3 python3.11 python3.10; do
    resolved=$(command -v "$candidate") || continue
    if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      cr_python="$resolved"
      break
    fi
  done
  if [ -z "$cr_python" ]; then
    failed "Install Python 3.12 from https://www.python.org/downloads/ (Python 3.10 or newer is required), then open this file again."
  fi
  echo "Creating your studio environment with: $cr_python"
  "$cr_python" --version
  "$cr_python" -m venv .venv || failed "Python could not create the studio environment. Check the error above, then reopen this launcher."
fi

.venv/bin/python -c 'import sys; print("Studio Python:", sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || failed "This studio's .venv uses Python older than 3.10. Install Python 3.12, rename the .venv folder to .venv-old, then reopen this launcher. Your output folder and saved accounts stay in place."

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
