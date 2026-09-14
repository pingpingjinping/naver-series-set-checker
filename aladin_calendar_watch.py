import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://www.aladin.co.kr/events/wevent_main_ebook.aspx?CID=38416"
STATE_FILE = Path("aladin_calendar_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
PATTERN = re.compile(r"(?P<month>\d{1,2})월\s*eBook\s*만화\s*출간\s*일정\s*캘린더", re.I)


def post_discord(content: str):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def find_latest_calendar():
    r = requests.get(LIST_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    candidates = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        m = PATTERN.search(text)
        if not m:
            continue
        href = urljoin(LIST_URL, a["href"])
        event_m = re.search(r"[?&]EventId=(\d+)", href, re.I)
        event_id = int(event_m.group(1)) if event_m else 0
        candidates.append({
            "month": int(m.group("month")),
            "title": text.strip(),
            "url": href,
            "event_id": event_id,
        })

    if not candidates:
        # 링크 텍스트가 분리된 경우를 대비해 전체 HTML 문자열에서도 EventId 주변을 탐색한다.
        html = str(soup)
        for m in PATTERN.finditer(soup.get_text(" ", strip=True)):
            # URL을 확정할 수 없으면 목록 링크로라도 알려줄 수 있게 fallback.
            candidates.append({
                "month": int(m.group("month")),
                "title": m.group(0),
                "url": LIST_URL,
                "event_id": 0,
            })
            break

    if not candidates:
        raise ValueError("알라딘 eBook 만화 출간 일정 캘린더를 찾지 못했습니다.")

    # 같은 월이 여러 번 잡히면 EventId가 큰 항목 우선.
    return max(candidates, key=lambda x: (x["event_id"], x["month"]))


def main():
    current = find_latest_calendar()
    state = load_state()
    previous = state.get("calendar") if isinstance(state.get("calendar"), dict) else None

    if not previous:
        print(f"기준점 등록: {current['title']} -> {current['url']}")
        save_state({"calendar": current})
        return

    changed = (
        previous.get("month") != current.get("month")
        or previous.get("event_id") != current.get("event_id")
        or previous.get("url") != current.get("url")
    )

    if changed:
        post_discord(
            "📚 **알라딘 eBook 만화 출간 일정 캘린더 업데이트**\n\n"
            f"**{current['month']}월 eBook 만화 출간 일정 캘린더가 등록됐어**\n"
            f"<{current['url']}>"
        )
        print(f"캘린더 변경 감지: {previous} -> {current}")
    else:
        print(f"변경 없음: {current['title']}")

    save_state({"calendar": current})


if __name__ == "__main__":
    main()
