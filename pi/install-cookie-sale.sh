#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/pi/.local/share/naver-series-set-checker"
DATA_DIR="/home/pi/.local/share/naver-cookie-check"
TOKEN_FILE="/home/pi/.config/naver-series-set-checker/github_token"
THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -un)" != "pi" ]]; then
  echo "Run as pi (not sudo): bash pi/install-cookie-sale.sh" >&2
  exit 1
fi
if [[ ! -d "$DATA_DIR/profile" ]]; then
  echo "Existing Naver-login Chromium profile not found: $DATA_DIR/profile" >&2
  exit 1
fi
if ! command -v chromium >/dev/null || ! command -v python3 >/dev/null; then
  echo "Chromium or python3 is missing." >&2
  exit 1
fi
if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "GitHub token file not found: $TOKEN_FILE" >&2
  echo "This is a PRIVATE Pi-side token with Actions: write permission, not the Discord webhook." >&2
  echo "Do not paste the token into chat or commit it." >&2
  exit 1
fi
if [[ ! -f "$REPO_DIR/pi/cookie_sale_checker.py" ]]; then
  echo "Repository isn't located at $REPO_DIR" >&2
  echo "Clone/pull the merged Series Checker repository there first." >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" "$DATA_DIR/profile"
python3 -m venv "$DATA_DIR/venv"
"$DATA_DIR/venv/bin/pip" --disable-pip-version-check install playwright
"$DATA_DIR/venv/bin/python" "$REPO_DIR/pi/cookie_sale_checker.py" selftest

sudo install -m 0644 "$THIS_DIR/systemd/naver-cookie-sale-check.service" /etc/systemd/system/
sudo install -m 0644 "$THIS_DIR/systemd/naver-cookie-sale-check.timer" /etc/systemd/system/
sudo install -m 0644 "$THIS_DIR/systemd/naver-cookie-sale-notify.service" /etc/systemd/system/
sudo install -m 0644 "$THIS_DIR/systemd/naver-cookie-sale-notify.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now naver-cookie-sale-check.timer naver-cookie-sale-notify.timer

echo
echo "Timers enabled (Violet DB services untouched):"
systemctl list-timers --all --no-pager | grep 'naver-cookie-sale-' || true
echo
echo "To test now (the page check intentionally waits at least 60 seconds):"
echo "  sudo systemctl start naver-cookie-sale-check.service"
echo "  sudo journalctl -u naver-cookie-sale-check.service -n 25 --no-pager"
echo "  sudo systemctl start naver-cookie-sale-notify.service"
echo "  sudo journalctl -u naver-cookie-sale-notify.service -n 25 --no-pager"
