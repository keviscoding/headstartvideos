#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null; then
  echo "Install Python 3.12 from https://www.python.org/downloads/ then open this file again."
  read -r -p "Press Return to close. "
  exit 1
fi
if [ ! -f .venv/bin/python ]; then
  python3 -m venv .venv || exit 1
fi
if [ ! -f .venv/.cr-installed ]; then
  echo "Setting up ChannelRecipe. This first download may take a few minutes."
  .venv/bin/python -m pip install -r requirements.txt || exit 1
  .venv/bin/python -m playwright install chromium || exit 1
  touch .venv/.cr-installed
fi
.venv/bin/python cr_studio.py
read -r -p "Press Return to close. "
