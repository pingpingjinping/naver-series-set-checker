#!/usr/bin/env python3
"""Check Naver Series flash cookie offers on WalnutPi, relay metadata via GitHub Actions.

No browser cookies or raw page HTML ever leave the Pi.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

URL = "https://series.naver.com/my/cookie/sellingList.series"
REPO = "pingpingjinping/naver-series-set-checker"
WORKFLOW = "cookie-sale-alert.yml"
ROOT = Path.home() / ".local/share/naver-cookie-check"
PROFILE = ROOT / "profile"
STATE_FILE = ROOT / "watch-state.json"
TOKEN_FILE = Path.home() / ".config/naver-series-set-checker/github_token"
RENDER_WAIT_MS = 60_000
CHECK_TIMEOUT_MS = 105_000


def write_state(state: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o700)
    temp = STATE_FILE.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(temp, 0o600)
    os.replace(temp, STATE_FILE)


def read_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def classify_page(current_url: str, body: str) -> tuple[str, str, str]:
    """Return status, short non-sensitive summary, and offer signature."""
    collapsed = re.sub(r"\s+", " ", body)
    final = urlparse(current_url)

    if final.netloc.lower().endswith("nid.naver.com") or re.search(
        r"(로그인[이 ]+(?:필요|만료)|로그인\s*(?:후|해\s*주세요)|다시\s*로그인)", collapsed
    ):
        return "login_required", "네이버 로그인이 필요합니다.", ""

    if final.netloc.lower() != "series.naver.com" or not final.path.endswith(
        "/my/cookie/sellingList.series"
    ):
        return "error", "쿠키 충전 페이지로 이동하지 못했습니다.", ""

    # Detect the targeted short-lived promotion, not generic bonus cookies.
    promotion = re.search(r"반짝\s*쿠키\s*세일|놓치면\s*사라지는", collapsed)
    if promotion:
        # Keep matches close to the promotion: avoid ordinary product rows.
        segment = collapsed[promotion.start() : promotion.start() + 900]
        deals = re.findall(
            r"쿠키\s*([\d,]+)\s*개\s*\+\s*보너스\s*([\d,]+)\s*개",
            segment,
        )
        offers = []
        for standard, bonus in deals[:4]:
            try:
                base, extra = int(standard.replace(",", "")), int(bonus.replace(",", ""))
            except ValueError:
                continue
            if base > 0:
                offers.append(f"쿠키 {base}개 + 보너스 {extra}개 ({extra / base * 100:g}%)")
        # The banner itself counts as an offer even if the product text format changes.
        summary = " / ".join(dict.fromkeys(offers)) or "반짝 쿠키 세일 진행 중"
        signature = hashlib.sha256(summary.encode("utf-8")).hexdigest()[:24]
        return "sale", summary, signature

    # Guard against a login/interstitial page with a generic site header.
    if "쿠키 충전" in collapsed and len(collapsed) >= 150:
        return "no_sale", "", ""

    return "error", "충전 페이지 본문 확인에 실패했습니다.", ""


def check() -> None:
    previous = read_state()
    now = int(time.time())
    status, summary, signature = "error", "페이지 확인 중 오류가 발생했습니다.", ""

    try:
        from playwright.sync_api import sync_playwright

        if not PROFILE.exists():
            raise RuntimeError("Saved Chromium profile is missing.")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE),
                executable_path="/usr/bin/chromium",
                headless=True,
                args=["--disable-gpu", "--disable-dev-shm-usage", "--disable-extensions"],
                timeout=90_000,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(URL, wait_until="domcontentloaded", timeout=CHECK_TIMEOUT_MS)
                # This is real wall-clock time, not Chromium virtual-time budget.
                page.wait_for_timeout(RENDER_WAIT_MS)
                body = page.locator("body").inner_text(timeout=20_000)
                status, summary, signature = classify_page(page.url, body)
            finally:
                context.close()
    except Exception as exc:
        # Only error type, never browser profile contents, cookies or HTML.
        print(f"check failed ({type(exc).__name__})", file=sys.stderr)

    if status == "no_sale":
        previous["notified_offer"] = None
        previous["notified_warning"] = None
    if status == "sale":
        previous["notified_warning"] = None

    previous["last_check"] = {
        "checked_at": int(time.time()),
        "status": status,
        "summary": summary,
        "offer_signature": signature,
    }
    write_state(previous)
    print(f"check result: {status}; {summary or 'no active offer'}")


def dispatch(kind: str, details: str) -> None:
    token = (os.environ.get("GITHUB_TOKEN") or TOKEN_FILE.read_text(encoding="utf-8")).strip()
    if not token:
        raise RuntimeError("Empty GitHub token")
    data = json.dumps(
        {"ref": "main", "inputs": {"kind": kind, "details": details[:400]}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
        data=data,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "WalnutPi-Cookie-Sale-Checker",
        },
    )
    with urlopen(request, timeout=25) as response:
        if response.status != 204:
            raise RuntimeError(f"GitHub dispatch returned HTTP {response.status}")


def notify() -> None:
    # Allow the slow Pi's 05:55 / 17:55 check to finish before the 06:00 / 18:00 dispatch.
    state, record = {}, {}
    for attempt in range(36):
        state = read_state()
        record = state.get("last_check") or {}
        age = int(time.time()) - int(record.get("checked_at") or 0)
        if 0 <= age <= 12 * 60:
            break
        if attempt < 35:
            time.sleep(5)
    else:
        print("No fresh check found within 3 minutes; no alert dispatched.")
        return

    status = record.get("status")
    if status == "no_sale":
        print("No flash sale; no notification.")
        return
    if status == "sale":
        signature = record.get("offer_signature")
        if signature and state.get("notified_offer") == signature:
            print("Previously notified offer; suppressing duplicate.")
            return
        kind, details = "sale", str(record.get("summary") or "반짝 쿠키 세일 진행 중")
    elif status in ("login_required", "error"):
        if state.get("notified_warning") == status:
            print(f"Warning already sent for {status}; suppressing duplicate.")
            return
        kind, details = status, str(record.get("summary") or "검사 실패")
    else:
        print("Unrecognized check status; no notification.")
        return

    try:
        dispatch(kind, details)
    except (OSError, HTTPError, URLError, RuntimeError) as exc:
        print(f"Failed to trigger Series Checker notification ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(1)

    if kind == "sale":
        state["notified_offer"] = record.get("offer_signature")
    else:
        state["notified_warning"] = kind
    write_state(state)
    print(f"Series Checker workflow triggered: {kind}")


def selftest() -> None:
    cases = [
        (URL, "쿠키 충전 일반 쿠키 30개 " * 12, "no_sale"),
        (URL, "놓치면 사라지는 반짝 쿠키 세일 쿠키 30개 + 보너스 3개 10%", "sale"),
        ("https://nid.naver.com/nidlogin.login", "네이버 로그인", "login_required"),
        (URL, "오류", "error"),
    ]
    for url, text, expected in cases:
        got = classify_page(url, text)[0]
        if got != expected:
            raise AssertionError(f"Expected {expected}, got {got}")
    print("4 parsing self-tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["check", "notify", "selftest"])
    args = parser.parse_args()
    if args.mode == "check":
        check()
    elif args.mode == "notify":
        notify()
    else:
        selftest()
