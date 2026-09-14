import json
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

RIDI_CALENDAR_URL = "https://ridibooks.com/event/11339"
STATE_FILE = Path("ridi_calendar_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

CALENDAR_RE = re.compile(r"(20\d{2})\s*년\s*(\d{1,2})\s*월\s*만화\s*캘린더")


def load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(title: str, year: int, month: int):
    STATE_FILE.write_text(
        json.dumps(
            {
                "url": RIDI_CALENDAR_URL,
                "title": title,
                "year": year,
                "month": month,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def fetch_calendar():
    r = requests.get(RIDI_CALENDAR_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    candidates = []
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        candidates.append(og["content"])
    if soup.title:
        candidates.append(soup.title.get_text(" ", strip=True))
    candidates.append(soup.get_text(" ", strip=True))

    for text in candidates:
        m = CALENDAR_RE.search(text or "")
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            return f"{year}년 {month}월 만화 캘린더", year, month

    raise ValueError("리디 페이지에서 'YYYY년 M월 만화 캘린더' 제목을 찾지 못했습니다.")


def post_discord(title: str):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")

    content = (
        "📚 **리디 만화 신간 캘린더 업데이트**\n\n"
        f"**{title}가 등록됐어**\n"
        f"🔗 캘린더 보기\n<{RIDI_CALENDAR_URL}>"
    )
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def main():
    title, year, month = fetch_calendar()
    old = load_state()
    old_key = (old.get("year"), old.get("month"))
    new_key = (year, month)

    if not old.get("year") or not old.get("month"):
        save_state(title, year, month)
        print(f"리디 만화 캘린더 기준점 등록: {title}")
        return

    if old_key == new_key:
        print(f"리디 만화 캘린더 변경 없음: {title}")
        if old.get("title") != title or old.get("url") != RIDI_CALENDAR_URL:
            save_state(title, year, month)
        return

    print(f"리디 만화 캘린더 변경 감지: {old_key} -> {new_key}")
    post_discord(title)
    save_state(title, year, month)


if __name__ == "__main__":
    main()
