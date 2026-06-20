#!/usr/bin/env bash
# deploy-mac.sh — macOS bootstrap for devtool
# Downloads install.py from GitHub and runs it.

set -e

CYAN='\033[0;36m'; RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
echo -e "${CYAN}[devtool] macOS${NC}"

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
    echo -e "${RED}[!] Python 3 not found. Run: brew install python${NC}"
    exit 1
fi
echo -e "${GREEN}[+]${NC} Python: $(command -v "$PY")"

URL="https://raw.githubusercontent.com/jimprivate/devtool/master/install.py"
TMP="/tmp/devtool-install.py"
echo "[+] Fetching install.py..."
curl -fsSL "$URL" -o "$TMP"
"$PY" "$TMP"
rm -f "$TMP"

echo -e "${CYAN}[devtool] Done.${NC}"
