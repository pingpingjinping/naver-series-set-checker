import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
SOURCE = os.environ.get("SOURCE", "both").strip().lower() or "both"
KST = ZoneInfo("Asia/Seoul")

RIDI_URL = "https://ridibooks.com/event/11339"
ALADIN_LIST_URL = "https://www.aladin.co.kr/events/wevent_main_ebook.aspx?CID=38416"
ALADIN_CALENDAR_RE = re.compile(r"(?P<month>\d{1,2})월\s*eBook\s*만화\s*출간\s*일정\s*캘린더", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
JINA_HEADERS = {"User-Agent": "Mozilla/5.0"}

MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def post_discord(content: str):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def jina_target_url(url: str):
    """Jina Reader target을 http로 바꾼다.

    GitHub Actions에서 https target 또는 완전한 브라우저 헤더를 붙이면
    Cloudflare 403이 날 수 있어서, Jina에는 http target + 최소 UA만 사용한다.
    원본 페이지 링크 자체는 계속 https를 사용한다.
    """
    parts = urlsplit(url)
    if parts.scheme in {"http", "https"}:
        parts = parts._replace(scheme="http")
        return urlunsplit(parts)
    return url


def jina_text(url: str):
    target = jina_target_url(url)
    reader_url = "https://r.jina.ai/" + target
    last_error = None
    for attempt in range(3):
        try:
            r = requests.get(reader_url, headers=JINA_HEADERS, timeout=45)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def clean_markdown(text: str):
    text = MD_IMAGE_RE.sub("", text or "")
    text = MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text)
    text = re.sub(r"[*_`~]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" |\t")
    return text.strip()


def add_item(groups, key, label, title):
    title = clean_markdown(title)
    if not title:
        return
    lowered = title.lower()
    if lowered in {"image", "신작", "세트전", "후속권 업데이트", "신규 연재 업데이트"}:
        return
    if title.startswith(("RIDI ONLY", "알라딘 독점")) and len(title) < 20:
        return

    if key not in groups:
        groups[key] = {"label": label, "titles": []}
    if title not in groups[key]["titles"]:
        groups[key]["titles"].append(title)


def parse_ridi(year: int, month: int):
    text = jina_text(RIDI_URL)
    groups = {}
    current = None

    date_re = re.compile(r"^#{0,6}\s*(\d{1,2})\.(\d{1,2})\.\(([^)]+)\)\s*$")
    bullet_re = re.compile(r"^\s*[-*]\s+(.+?)\s*$")

    for raw in text.splitlines():
        line = raw.strip()
        m = date_re.match(line)
        if m:
            m_month = int(m.group(1))
            day = int(m.group(2))
            weekday = m.group(3).strip()
            current = (m_month, day, weekday)
            continue

        if not current or current[0] != month:
            continue

        b = bullet_re.match(line)
        if not b:
            continue

        day = current[1]
        weekday = current[2]
        add_item(groups, day, f"{month}/{day} ({weekday})", b.group(1))

    if not groups:
        raise ValueError("리디 이번 달 출시작을 파싱하지 못했습니다.")
    return groups, RIDI_URL


def find_aladin_calendar(month: int):
    r = requests.get(ALADIN_LIST_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    candidates = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        m = ALADIN_CALENDAR_RE.search(text)
        if not m or int(m.group("month")) != month:
            continue
        href = urljoin(ALADIN_LIST_URL, a["href"])
        event_m = re.search(r"[?&]EventId=(\d+)", href, re.I)
        event_id = int(event_m.group(1)) if event_m else 0
        candidates.append((event_id, href))

    if not candidates:
        raise ValueError(f"알라딘 {month}월 eBook 만화 출간 캘린더를 찾지 못했습니다.")
    return max(candidates, key=lambda x: x[0])[1]


def parse_aladin(year: int, month: int):
    """알라딘 캘린더 HTML의 출시일 박스에서 제목 열만 직접 읽는다."""
    url = find_aladin_calendar(month)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    groups = {}

    for box in soup.select("dl.monthC_comicbox_book"):
        dt = box.find("dt")
        if not dt:
            continue
        date_text = clean_markdown(dt.get_text(" ", strip=True))
        m = re.search(r"(\d{1,2})\s*\(([^)]+)\)", date_text)
        if not m:
            continue
        day = int(m.group(1))
        weekday = m.group(2).strip()
        if day < 1 or day > 31:
            continue

        for cell in box.select("td.monthC_comicbox_text1"):
            title = cell.get_text(" ", strip=True)
            add_item(groups, day, f"{month}/{day} ({weekday})", title)

    if not groups:
        raise ValueError("알라딘 이번 달 출시작을 파싱하지 못했습니다.")
    return groups, url


def send_report(site_name: str, emoji: str, year: int, month: int, groups, source_url: str):
    total = sum(len(v["titles"]) for v in groups.values())
    base_header = f"{emoji} **{site_name} {year}년 {month}월 만화 출시 일정**\n총 **{total}개**"
    footer = f"\n📅 원본 캘린더: <{source_url}>"

    messages = []
    current = base_header + "\n"

    for day in sorted(groups):
        group = groups[day]
        group_header = f"\n**{group['label']}**\n"
        if len(current) + len(group_header) > 1750:
            messages.append(current.rstrip())
            current = base_header + "\n" + group_header
        else:
            current += group_header

        for title in group["titles"]:
            line = f"• {title}\n"
            if len(current) + len(line) + len(footer) > 1900:
                messages.append(current.rstrip())
                current = base_header + "\n" + group_header + line
            else:
                current += line

    current += footer
    messages.append(current.rstrip())

    for i, message in enumerate(messages, 1):
        if len(messages) > 1:
            message = message.replace(base_header, base_header + f" · {i}/{len(messages)}", 1)
        post_discord(message)
        time.sleep(0.45)

    print(f"{site_name}: {total}개 / Discord {len(messages)}개 메시지 전송")


def main():
    now = datetime.now(KST)
    year, month = now.year, now.month

    if SOURCE not in {"both", "ridi", "aladin"}:
        raise ValueError("SOURCE는 both, ridi, aladin 중 하나여야 합니다.")

    errors = []

    if SOURCE in {"both", "ridi"}:
        try:
            groups, url = parse_ridi(year, month)
            send_report("리디", "📘", year, month, groups, url)
        except Exception as exc:
            errors.append(f"리디: {exc}")
            print(f"리디 월간 출시작 전송 실패: {exc}")

    if SOURCE in {"both", "aladin"}:
        try:
            groups, url = parse_aladin(year, month)
            send_report("알라딘", "📙", year, month, groups, url)
        except Exception as exc:
            errors.append(f"알라딘: {exc}")
            print(f"알라딘 월간 출시작 전송 실패: {exc}")

    if errors:
        raise RuntimeError(" / ".join(errors))


if __name__ == "__main__":
    main()
