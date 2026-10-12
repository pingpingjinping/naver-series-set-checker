# WalnutPi: Naver Series flash cookie sale → Series Checker Discord

This optional integration does **not** modify Violet DB sync, set watching, or price watching.

## Flow (Pi timezone: Asia/Seoul)

- **05:55 / 17:55:** run Chromium with the existing logged-in Pi profile. Wait a *real* 60 seconds after the page starts loading and inspect the rendered body text.
- **06:00 / 18:00:** if there is a new '반짝 쿠키 세일', dispatch the result to this repository's `cookie-sale-alert.yml` GitHub Actions workflow.
- GitHub Actions forwards the brief offer info to the existing `SERIES_DISCORD_WEBHOOK` repository secret. **The Discord webhook and login profile/cookies stay in their original locations.**
- No sale → no message; same continuous offer → no repeat message. Login required or check error → warn once until status is healthy.
- The two pairs of Pi timers have `Persistent=false`, so a reboot does not send stale missed event alerts.
- GitHub Actions can start a few minutes late, so Discord delivery is not guaranteed at precisely 06:00/18:00.

This Pi program needs a GitHub PAT with **Actions: write** access to `pingpingjinping/naver-series-set-checker`, expected at:
`/home/pi/.config/naver-series-set-checker/github_token`. The token is read locally, never sent as an alert or committed to GitHub. Reuse the same PAT from the existing Pi Discord price bot if available.

## Prerequisites

- Merge the PR containing this feature into `main`.
- Chromium profile with working Naver login: `/home/pi/.local/share/naver-cookie-check/profile`.
- Chromium and python3 on the Pi.
- `python3-venv` installed. Example: `sudo apt-get install -y python3-venv`.
- Chromium GUI/noVNC is closed, to release its profile lock.

## Install

```bash
REPO="$HOME/.local/share/naver-series-set-checker"
if [ -d "$REPO/.git" ]; then
  git -C "$REPO" pull --ff-only
else
  git clone https://github.com/pingpingjinping/naver-series-set-checker.git "$REPO"
fi
test -s "$HOME/.config/naver-series-set-checker/github_token" && echo "Pi token exists" || echo "Pi token missing — configure Actions:write PAT first"
cd "$REPO"
bash pi/install-cookie-sale.sh
```

The installer deliberately refuses to enable timers if the token or login profile is missing. It installs Playwright's **Python binding only**, using Debian's existing `/usr/bin/chromium`, and doesn't download another browser.

## Check / logs

```bash
systemctl list-timers --all | grep naver-cookie-sale
sudo systemctl start naver-cookie-sale-check.service
sudo journalctl -u naver-cookie-sale-check.service -n 25 --no-pager
sudo systemctl start naver-cookie-sale-notify.service
sudo journalctl -u naver-cookie-sale-notify.service -n 25 --no-pager
```

The checker deliberately waits one minute, so the Pi has time to render the page. The only state stored is a non-sensitive summary in `~/.local/share/naver-cookie-check/watch-state.json`. Do not share profile contents or PAT in support chats. If page markup changes, refine the detector using **redacted** excerpts from the rendered text, not raw cookies, login data, or full HTML.

## Uninstall scheduled checks

```bash
sudo systemctl disable --now naver-cookie-sale-check.timer naver-cookie-sale-notify.timer
```

The dedicated profile and config are intentionally **not** removed when timers are disabled.
