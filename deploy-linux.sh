#!/usr/bin/env bash
# deploy-linux.sh — Linux bootstrap trigger for devtool
# Responsibility: find Python, hand off to install.py on GitHub.
# Change frequency: NEVER (static, lives on your own server)
# install logic lives at: github.com/jimprivate/devtool/master/install.py

set -e

CYAN='\033[0;36m'
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${CYAN}[devtool] Linux${NC}"

# Find Python
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo -e "${RED}[!] Python 3 not found.${NC}"
    echo "    Ubuntu/Debian:  sudo apt install python3"
    echo "    Fedora:         sudo dnf install python3"
    echo "    Arch:           sudo pacman -S python"
    exit 1
fi
echo -e "${GREEN}[+]${NC} Python: $(command -v "$PY")"

# Download and run bootstrap from GitHub
URL="https://raw.githubusercontent.com/jimprivate/devtool/master/install.py"
TMP="/tmp/devtool-install.py"
echo "[+] Fetching bootstrap from GitHub..."
curl -fsSL "$URL" -o "$TMP"
"$PY" "$TMP"
rm -f "$TMP"

echo -e "${CYAN}[devtool] Done.${NC}"
