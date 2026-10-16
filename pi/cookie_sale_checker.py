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


def classify_page(current_url: str, body: str) -> tuple[str, str, list[dict]]:
    """Parse only event-applied offers; distinguish purchase-complete from an active price."""
    collapsed = re.sub(r"\s+", " ", body)
    final = urlparse(current_url)

    if final.netloc.lower().endswith("nid.naver.com") or re.search(
        r"(로그인[이 ]+(?:필요|만료)|로그인\s*(?:후|해\s*주세요)|다시\s*로그인)", collapsed
    ):
        return "login_required", "네이버 로그인이 필요합니다.", []

    if final.netloc.lower() != "series.naver.com" or not final.path.endswith(
        "/my/cookie/sellingList.series"
    ):
        return "error", "쿠키 충전 페이지로 이동하지 못했습니다.", []

    promotion = re.search(r"반짝\s*쿠키\s*세일|놓치면\s*사라지는", collapsed)
    if promotion:
        # Ordinary products also cost 3,000/10,000 won; do not inspect their buttons.
        region = collapsed[promotion.end():]
        boundary = re.search(r"이벤트\s*안내|이벤트\s*미적용\s*상품", region)
        if boundary:
            region = region[:boundary.start()]

        pattern = re.compile(
            r"이벤트\s*적용\s*상품\s*쿠키\s*([\d,]+)\s*개\s*\+\s*보너스\s*([\d,]+)\s*개"
        )
        matches = list(pattern.finditer(region))
        if not matches:
            return "error", "반짝 세일 상품 구성을 확인하지 못했습니다.", []

        offers: list[dict] = []
        for i, match in enumerate(matches):
            base = int(match.group(1).replace(",", ""))
            bonus = int(match.group(2).replace(",", ""))
            if base <= 0 or bonus <= 0:
                continue

            # Each purchase button belongs only to the closest event-applied product.
            next_start = matches[i + 1].start() if i + 1 < len(matches) else len(region)
            item_text = region[match.end():next_start]
            expected_price = base * 100
            prices = {
                int(value.replace(",", ""))
                for value in re.findall(r"(?<![\d,])([\d][\d,]*)\s*원", item_text)
            }
            if re.search(r"구매\s*완료", item_text):
                purchase_state = "purchased"
            elif expected_price in prices:
                purchase_state = "available"
            else:
                # Don't interpret an unfamiliar button (or an ordinary product price)
                # as a purchasable sale and accidentally send a duplicate alert.
                purchase_state = "unknown"

            key = f"{base}+{bonus}"
            label = f"쿠키 {base}개 + 보너스 {bonus}개 ({bonus / base * 100:g}%)"
            offers.append({
                "key": key,
                "base": base,
                "bonus": bonus,
                "label": label,
                "purchase_state": purchase_state,
                "price": expected_price,
            })

        if not offers:
            return "error", "반짝 세일 상품 구성을 확인하지 못했습니다.", []
        details = []
        for offer in offers:
            state = offer["purchase_state"]
            button = (
                "구매 완료" if state == "purchased"
                else f'{offer["price"]:,}원' if state == "available"
                else "구매 상태 미확인"
            )
            details.append(f'{offer["label"]} [{button}]')
        return "sale", " / ".join(details), offers

    purchase_page = (
        "쿠키 충전" in collapsed
        or all(word in collapsed for word in ("쿠키", "구매", "보너스"))
    )
    if len(collapsed) >= 150 and purchase_page:
        return "no_sale", "", []

    return "error", "충전 페이지 본문 확인에 실패했습니다.", []


def advance_offer_history(state: dict, offers: list[dict]) -> None:
    """Persist purchase transitions across no-sale days and repeated offer types."""
    history = state.setdefault("offer_history", {})
    for offer in offers:
        key = offer["key"]
        entry = history.setdefault(
            key, {"generation": 0, "notified_generation": -1, "last_known": "unknown"}
        )
        # Migrate a previous-version alert if the legacy summary matches exactly.
        # Keep legacy records intact until the new parser has observed a real sale.
        old_digest = hashlib.sha256(offer["label"].encode("utf-8")).hexdigest()[:24]
        if (
            entry.get("notified_generation", -1) == -1
            and state.get("notified_offer") == old_digest
        ):
            entry["notified_generation"] = 0

        purchase_state = offer["purchase_state"]
        if purchase_state == "available" and entry["last_known"] == "purchased":
            entry["generation"] += 1
        if purchase_state in ("available", "purchased"):
            entry["last_known"] = purchase_state


def pending_available_offers(state: dict, offers: list[dict]) -> list[dict]:
    """Only alert new package types or purchased-to-available transitions."""
    history = state.get("offer_history", {})
    pending = []
    for offer in offers:
        entry = history.get(offer["key"], {})
        if (
            offer["purchase_state"] == "available"
            and entry.get("generation", 0) > entry.get("notified_generation", -1)
        ):
            pending.append(offer)
    return pending


def mark_offers_notified(state: dict, offers: list[dict]) -> None:
    history = state.setdefault("offer_history", {})
    for offer in offers:
        entry = history[offer["key"]]
        entry["notified_generation"] = entry["generation"]

def check() -> None:
    previous = read_state()
    status, summary, offers = "error", "페이지 확인 중 오류가 발생했습니다.", []

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
                # Real wall-clock wait, essential for WalnutPi's slow page rendering.
                page.wait_for_timeout(RENDER_WAIT_MS)
                body = page.locator("body").inner_text(timeout=20_000)
                status, summary, offers = classify_page(page.url, body)
            finally:
                context.close()
    except Exception as exc:
        # Never log the Chromium profile, cookies, full HTML or auth tokens.
        print(f"check failed ({type(exc).__name__})", file=sys.stderr)

    if status == "sale":
        advance_offer_history(previous, offers)
    if status in ("no_sale", "sale"):
        previous["notified_warning"] = None
    # Do NOT reset purchase history or notified offers when no promotion appears.
    previous["last_check"] = {
        "checked_at": int(time.time()),
        "status": status,
        "summary": summary,
        "offers": offers,
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
    # Allow the slow Pi's 05:55 / 17:55 check to finish before 06:00 / 18:00.
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
    selected = []
    if status == "no_sale":
        print("No flash sale; no notification.")
        return
    if status == "sale":
        selected = pending_available_offers(state, record.get("offers") or [])
        if not selected:
            print("No newly purchasable cookie sale; suppressing duplicate.")
            return
        kind = "sale"
        details = " / ".join(
            f'{o["label"]} · {o["price"]:,}원' for o in selected
        )
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
        mark_offers_notified(state, selected)
    else:
        state["notified_warning"] = kind
    write_state(state)
    print(f"Series Checker workflow triggered: {kind}")

def selftest() -> None:
    cases = [
        (URL, "쿠키 충전 일반 쿠키 30개 " * 12, "no_sale"),
        (URL, "보너스 쿠키 30개 구매 " * 150, "no_sale"),
        (URL, "쿠키 30개 구매 " * 150, "error"),
        ("https://nid.naver.com/nidlogin.login", "네이버 로그인", "login_required"),
        (URL, "오류", "error"),
    ]
    for url, body, expected in cases:
        status, _, _ = classify_page(url, body)
        assert status == expected, (expected, status)

    banner = "놓치면 사라지는 반짝 쿠키 세일 하나만 선택해 구매할 수 있어요! "
    def sample(base: int, extra: int, button: str) -> str:
        return (
            banner + f"이벤트 적용 상품쿠키 {base}개+ 보너스 {extra}개10% "
            + button + " 이벤트 안내(보너스쿠키 유효 기간: 30일) "
            + "이벤트 미적용 상품쿠키 30개 3,000원"
        )

    # A price in the ordinary-products section must not make an event item available.
    status, _, offered = classify_page(URL, sample(30, 3, "구매 완료"))
    assert status == "sale" and len(offered) == 1
    assert offered[0]["purchase_state"] == "purchased", offered
    status, _, available = classify_page(URL, sample(30, 3, "3,000원"))
    assert status == "sale" and available[0]["purchase_state"] == "available"
    assert available[0]["key"] == "30+3"
    status, _, unknown = classify_page(URL, sample(30, 3, ""))
    assert status == "sale" and unknown[0]["purchase_state"] == "unknown"
    status, _, larger = classify_page(URL, sample(100, 5, "10,000원"))
    assert status == "sale" and larger[0]["purchase_state"] == "available"
    assert larger[0]["key"] == "100+5"

    state: dict = {}
    advance_offer_history(state, available)
    assert len(pending_available_offers(state, available)) == 1
    mark_offers_notified(state, available)
    assert not pending_available_offers(state, available)
    # Even after observing a no-sale interval, unchanged, unbought 30+3 is ignored.
    advance_offer_history(state, available)
    assert not pending_available_offers(state, available)
    # New composition must be announced even if the previous option was ignored.
    advance_offer_history(state, larger)
    assert len(pending_available_offers(state, larger)) == 1
    mark_offers_notified(state, larger)
    assert not pending_available_offers(state, larger)
    # A completed purchase is not a new event.
    advance_offer_history(state, offered)
    assert not pending_available_offers(state, offered)
    # Once purchased, the same composition becoming buyable again IS new.
    advance_offer_history(state, available)
    assert len(pending_available_offers(state, available)) == 1
    mark_offers_notified(state, available)
    assert not pending_available_offers(state, available)
    # Unknown button must not create a new purchase transition.
    advance_offer_history(state, unknown)
    assert not pending_available_offers(state, unknown)
    print("Cookie sale parsing and purchase-state regression tests passed.")

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
