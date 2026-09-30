#!/usr/bin/env python3
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo

from monthly_release_report import jina_text

KST = ZoneInfo("Asia/Seoul")

RIDI_URL = "https://ridibooks.com/event/11339"
ALADIN_LIST_URL = "https://www.aladin.co.kr/events/wevent_main_ebook.aspx?CID=38416"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

RIDI_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*만화\s*캘린더")
ALADIN_RE = re.compile(r"(?P<month>\d{1,2})월\s*eBook\s*만화\s*출간\s*일정\s*캘린더", re.I)

STATE_DIR = Path.home() / ".local/state/naver-series-set-checker"
STATE_FILE = STATE_DIR / "monthly_calendar_monitor.json"
TOKEN_FILE = Path.home() / ".config/naver-series-set-checker/github_token"
WEBHOOK_FILE = Path.home() / ".config/naver-series-set-checker/discord_webhook"

GITHUB_REPO = "pingpingjinping/naver-series-set-checker"
GITHUB_BRANCH = "main"
GITHUB_NOTIFY_WORKFLOW = "walnutpi-calendar-notify.yml"


def load_text_secret(env_name: str, path: Path) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def target_month(now: datetime):
    if now.day >= 27:
        if now.month == 12:
            return now.year + 1, 1
        return now.year, now.month + 1
    if now.day <= 6:
        return now.year, now.month
    return None


def fetch_ridi() -> dict:
    # 리디는 Cloudflare managed challenge로 일반 requests/curl을 차단할 수 있다.
    # 상세 출시 일정 파서와 동일하게 Jina Reader를 경유해 공개 페이지 텍스트를 읽는다.
    text = jina_text(RIDI_URL)
    m = RIDI_RE.search(text or "")
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        return {
            "year": year,
            "month": month,
            "title": f"{year}년 {month}월 만화 캘린더",
            "url": RIDI_URL,
        }

    raise ValueError("리디 페이지에서 'YYYY년 M월 만화 캘린더'를 찾지 못했습니다.")


def fetch_aladin(target_year: int, target_month: int) -> dict | None:
    r = requests.get(ALADIN_LIST_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # 알라딘은 월마다 EventId가 바뀌므로 "가장 최신"을 추정하지 않고,
    # 이번 감시 주기의 목표 월과 정확히 일치하는 링크만 찾는다.
    candidates = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        m = ALADIN_RE.search(text)
        if not m:
            continue

        month = int(m.group("month"))
        if month != target_month:
            continue

        href = urljoin(ALADIN_LIST_URL, a["href"])
        event_m = re.search(r"[?&]EventId=(\d+)", href, re.I)
        event_id = int(event_m.group(1)) if event_m else 0
        candidates.append(
            {
                "year": target_year,
                "month": target_month,
                "title": text.strip(),
                "url": href,
                "event_id": event_id,
            }
        )

    if candidates:
        # 같은 목표 월 링크가 여러 개면 새 이벤트일 가능성이 높은 큰 EventId 우선.
        return max(candidates, key=lambda x: x["event_id"])

    # 링크 텍스트가 태그 단위로 쪼개져 있을 때를 위한 fallback.
    # 목표 월 문구 자체가 페이지에 있을 때만 성공으로 본다.
    page_text = soup.get_text(" ", strip=True)
    for m in ALADIN_RE.finditer(page_text):
        if int(m.group("month")) == target_month:
            return {
                "year": target_year,
                "month": target_month,
                "title": m.group(0),
                "url": ALADIN_LIST_URL,
                "event_id": 0,
            }

    return None

def post_discord(content: str) -> None:
    # 로컬 webhook이 있으면 바로 전송하고, 없으면 기존 Pi GitHub 토큰으로
    # GitHub Actions의 알림 전용 workflow를 호출한다.
    webhook = load_text_secret("DISCORD_WEBHOOK", WEBHOOK_FILE)
    if webhook:
        r = requests.post(webhook, json={"content": content}, timeout=30)
        r.raise_for_status()
        return

    token = load_text_secret("GITHUB_TOKEN", TOKEN_FILE)
    if not token:
        raise RuntimeError(
            f"Discord webhook도 GitHub token도 없습니다: {WEBHOOK_FILE}, {TOKEN_FILE}"
        )

    api = (
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/"
        f"{GITHUB_NOTIFY_WORKFLOW}/dispatches"
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "walnutpi-monthly-calendar-monitor",
    }
    body = {
        "ref": GITHUB_BRANCH,
        "inputs": {
            "content": content,
        },
    }
    r = requests.post(api, headers=headers, json=body, timeout=30)
    r.raise_for_status()


def github_sync_state(site: str, item: dict) -> None:
    token = load_text_secret("GITHUB_TOKEN", TOKEN_FILE)
    if not token:
        print(f"[{site}] GitHub token 없음 -> 원격 state 동기화 생략")
        return

    if site == "ridi":
        path = "ridi_calendar_state.json"
        payload_obj = {
            "url": item["url"],
            "title": item["title"],
            "year": item["year"],
            "month": item["month"],
        }
        message = "chore: update Ridi calendar state from WalnutPi"
    else:
        path = "aladin_calendar_state.json"
        payload_obj = {
            "calendar": {
                "month": item["month"],
                "title": item["title"],
                "url": item["url"],
                "event_id": item.get("event_id", 0),
            }
        }
        message = "chore: update Aladin calendar state from WalnutPi"

    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "walnutpi-monthly-calendar-monitor",
    }

    current = requests.get(api, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=30)
    current.raise_for_status()
    sha = current.json()["sha"]

    encoded = base64.b64encode(
        (json.dumps(payload_obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    ).decode("ascii")

    body = {
        "message": message,
        "content": encoded,
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    updated = requests.put(api, headers=headers, json=body, timeout=30)
    updated.raise_for_status()
    print(f"[{site}] GitHub state 동기화 완료")


def is_target(item: dict | None, year: int, month: int) -> bool:
    if not isinstance(item, dict):
        return False
    return item.get("year") == year and item.get("month") == month


def update_site(site: str, target_year: int, target_mon: int, state: dict, now: datetime) -> bool:
    old = state.get(site)
    if is_target(old, target_year, target_mon):
        print(f"[{site}] {month_key(target_year, target_mon)} 이미 확인 완료 -> 조회 생략")
        return True

    if site == "ridi":
        current = fetch_ridi()
        label = "리디"
    else:
        current = fetch_aladin(target_year, target_mon)
        label = "알라딘"
        if current is None:
            print(f"[aladin] 목표 월 {month_key(target_year, target_mon)} 링크 아직 없음")
            return False

    current_key = month_key(current["year"], current["month"])
    target_key = month_key(target_year, target_mon)
    print(f"[{site}] 현재 캘린더={current_key}, 목표={target_key}")

    if current["year"] == target_year and current["month"] == target_mon:
        if site == "ridi":
            message = (
                "📚 **리디 만화 신간 캘린더 업데이트**\n\n"
                f"**{current['title']}가 등록됐어**\n"
                f"<{current['url']}>"
            )
        else:
            message = (
                "📚 **알라딘 eBook 만화 출간 일정 캘린더 업데이트**\n\n"
                f"**{target_mon}월 eBook 만화 출간 일정 캘린더가 등록됐어**\n"
                f"<{current['url']}>"
            )

        post_discord(message)
        print(f"[{site}] 목표 월 발견 -> Discord 알림 완료")

    state[site] = current
    save_state(state)

    try:
        github_sync_state(site, current)
    except Exception as exc:
        print(f"[{site}] GitHub state 동기화 실패(로컬 감시는 계속 유지): {type(exc).__name__}: {exc}")

    return current["year"] == target_year and current["month"] == target_mon


def send_manual_check_alert(target_year: int, target_mon: int, state: dict) -> None:
    key = month_key(target_year, target_mon)
    missing = []

    for site, label in (("ridi", "리디"), ("aladin", "알라딘")):
        if not is_target(state.get(site), target_year, target_mon):
            missing.append((site, label))

    if not missing:
        print(f"[deadline] {key} 두 사이트 모두 이미 확인 완료")
        return

    alerted = state.setdefault("manual_alerted", {})
    pending = [(site, label) for site, label in missing if alerted.get(site) != key]
    if not pending:
        print(f"[deadline] {key} 직접 확인 알림 이미 전송됨")
        return

    lines = [
        "⚠️ **월간 만화 캘린더 직접 확인 필요**",
        "",
        f"**{target_year}년 {target_mon}월 캘린더가 5일까지 자동 확인되지 않았어.**",
        "자동 확인은 이번 달 주기에서 중단했어. 직접 확인해줘.",
        "",
    ]

    for site, label in pending:
        url = RIDI_URL if site == "ridi" else ALADIN_LIST_URL
        lines.append(f"- {label}: <{url}>")

    post_discord("\n".join(lines))

    for site, _ in pending:
        alerted[site] = key
    save_state(state)
    print(f"[deadline] {key} 직접 확인 알림 전송 완료")


def ensure_release_calendar_snapshot(target_year: int, target_mon: int) -> None:
    archive = STATE_DIR / f"release_calendar_{target_year:04d}-{target_mon:02d}.json"
    if archive.exists():
        print(f"[snapshot] {archive.name} 이미 있음 -> 생성 생략")
        return

    script = Path(__file__).with_name("calendar_snapshot.py")
    print(f"[snapshot] {target_year:04d}-{target_mon:02d} 상세 출시 일정 생성")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--year",
            str(target_year),
            "--month",
            str(target_mon),
        ],
        check=False,
    )
    if result.returncode != 0 or not archive.exists():
        raise RuntimeError(
            f"캘린더 스냅샷 생성 실패(returncode={result.returncode}, exists={archive.exists()})"
        )
    print(f"[snapshot] 생성 완료: {archive}")


def main() -> int:
    now = datetime.now(KST)
    target = target_month(now)

    if target is None:
        print(f"{now:%Y-%m-%d} KST: 27일~6일 감시 기간이 아니므로 종료")
        return 0

    target_year, target_mon = target
    key = month_key(target_year, target_mon)
    state = load_state()

    print(f"{now:%Y-%m-%d %H:%M:%S} KST / target={key}")

    # 6일에는 더 이상 사이트 요청을 보내지 않고, 5일까지 못 찾은 사이트만 직접 확인 알림.
    if now.day == 6:
        send_manual_check_alert(target_year, target_mon, state)
        return 0

    failures = []
    for site in ("ridi", "aladin"):
        try:
            update_site(site, target_year, target_mon, state, now)
        except Exception as exc:
            failures.append((site, exc))
            print(f"[{site}] 확인 실패: {type(exc).__name__}: {exc}")

    if failures:
        print("일부 사이트 확인 실패. 다음 예약 실행 때 다시 시도합니다.")
        return 1

    if all(is_target(state.get(site), target_year, target_mon) for site in ("ridi", "aladin")):
        try:
            ensure_release_calendar_snapshot(target_year, target_mon)
        except Exception as exc:
            print(f"[snapshot] 생성 실패: {type(exc).__name__}: {exc}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
