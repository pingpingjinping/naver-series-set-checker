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
MD_IMAGE_DETAIL_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MD_LINK_RE = re.compile(r"\[(.*?)\]\((?:https?://)[^)]*\)")

RIDI_CATEGORY_ORDER = ["RIDI ONLY", "신작", "후속권 업데이트"]
RIDI_CATEGORY_TAGS = {
    "RIDI ONLY": "⭐ [RIDI ONLY]",
    "신작": "🆕 [신작]",
    "후속권 업데이트": "🔄 [후속권]",
}
RIDI_SET_CATEGORY = "세트전"
RIDI_LABEL_FALLBACK = {
    "20240215154705_529079.png": "RIDI ONLY",
    "20240215154750_031013.png": "신작",
    "20240215154844_434923.png": "세트전",
    "20240215154859_482261.png": "후속권 업데이트",
}

ALADIN_CATEGORY_TAGS = {
    "알라딘 독점": "⭐ [알라딘 독점]",
    "신간": "🆕 [신간]",
    "후속권 업데이트": "🔄 [후속권]",
    "후속권": "🔄 [후속권]",
    "신규 연재 업데이트": "✨ [신규 연재]",
    "신규 연재": "✨ [신규 연재]",
}
ALADIN_SET_CATEGORY = "특가 세트"
ALADIN_LABEL_FALLBACK = {
    "2019_monthC_label1.png": "신간",
    "2019_monthC_label2.png": "특가 세트",
    "2019_monthC_label3.png": "후속권 업데이트",
    "2019_monthC_label4.png": "알라딘 독점",
    "2019_monthC_label5.png": "신규 연재 업데이트",
}


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


def aladin_url_candidates(url: str):
    """www/mall 두 호스트를 모두 시도할 수 있게 후보 URL을 만든다."""
    candidates = [url]
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "www.aladin.co.kr":
        candidates.append(urlunsplit(parts._replace(netloc="mall.aladin.co.kr")))
    elif host == "mall.aladin.co.kr":
        candidates.append(urlunsplit(parts._replace(netloc="www.aladin.co.kr")))
    return list(dict.fromkeys(candidates))


def fetch_aladin_html(url: str):
    """GitHub Actions에서 알라딘 403이 나면 호스트/헤더를 바꿔 재시도한다."""
    last_error = None
    browser_headers = dict(HEADERS)
    browser_headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.aladin.co.kr/",
        "Cache-Control": "no-cache",
    })
    header_options = [browser_headers, HEADERS, JINA_HEADERS, {}]

    for attempt in range(3):
        for candidate in aladin_url_candidates(url):
            for headers in header_options:
                try:
                    r = requests.get(candidate, headers=headers, timeout=30)
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
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" |\t")
    return text.strip()


def image_key(url: str):
    try:
        return urlsplit(url).path.rsplit("/", 1)[-1]
    except Exception:
        return url


def format_title(title: str, categories, tag_map, order=None):
    title = clean_markdown(title)
    if not title:
        return ""
    categories = set(categories or [])
    order = order or list(tag_map)
    tags = [tag_map[c] for c in order if c in categories and c in tag_map]
    return " ".join(tags + [title]) if tags else title


def strip_ridi_set_note(title: str):
    """신작/후속권 제목 뒤에 덧붙은 세트 행사 문구만 제거한다."""
    title = clean_markdown(title)
    patterns = [
        r"\s*\d{1,2}/\d{1,2}\s*세트\s*오픈.*$",
        r"\s*\d+(?:\s*[~\-]\s*\d+)?권\s*\([^)]*세트[^)]*\)\s*$",
        r"\s*\d+(?:\s*[~\-]\s*\d+)?권\s*세트\s*$",
    ]
    for pattern in patterns:
        title = re.sub(pattern, "", title).strip()
    return title


def add_item(groups, key, label, title, author=""):
    title = clean_markdown(title)
    author = clean_markdown(author)
    if not title:
        return
    lowered = title.lower()
    if lowered in {
        "image",
        "신작",
        "세트전",
        "후속권 업데이트",
        "신규 연재 업데이트",
        "신간",
        "후속권",
        "특가 세트",
    }:
        return
    if title.startswith(("RIDI ONLY", "알라딘 독점")) and len(title) < 20:
        return

    if key not in groups:
        groups[key] = {"label": label, "titles": [], "items": []}
    if title not in groups[key]["titles"]:
        groups[key]["titles"].append(title)
        groups[key]["items"].append({"title": title, "author": author})
    elif author:
        for item in groups[key]["items"]:
            if item["title"] == title and not item.get("author"):
                item["author"] = author
                break


def ridi_author_from_meta(meta_lines):
    """리디 캘린더에서 제목 바로 아래의 작가명을 읽는다."""
    category_labels = {"RIDI ONLY", "신작", "세트전", "후속권 업데이트"}
    for meta_line in meta_lines:
        if MD_IMAGE_DETAIL_RE.search(meta_line):
            continue
        author = clean_markdown(meta_line)
        if not author or author in category_labels:
            continue
        return author
    return ""


def ridi_icon_map(text: str):
    labels = ["RIDI ONLY", "신작", "세트전", "후속권 업데이트"]
    mapping = dict(RIDI_LABEL_FALLBACK)
    for alt, url in MD_IMAGE_DETAIL_RE.findall(text):
        for label in labels:
            if label.lower() in alt.lower():
                mapping[image_key(url)] = label
                break
    return mapping


def parse_ridi(year: int, month: int):
    text = jina_text(RIDI_URL)
    icon_map = ridi_icon_map(text)
    groups = {}
    current = None
    pending = None

    date_re = re.compile(r"^#{0,6}\s*(\d{1,2})\.(\d{1,2})\.\(([^)]+)\)\s*$")
    bullet_re = re.compile(r"^\s*[-*]\s+(.+?)\s*$")

    def flush_pending():
        nonlocal pending
        if not pending:
            return

        categories = set()
        for meta_line in pending["meta"]:
            for _, image_url in MD_IMAGE_DETAIL_RE.findall(meta_line):
                category = icon_map.get(image_key(image_url))
                if category:
                    categories.add(category)

        # 세트전만 있는 항목은 제외. 신작/후속권에 세트 행사가 같이 붙은 경우는
        # 출시 정보 자체는 남기되 세트 아이콘과 세트 행사 문구만 숨긴다.
        release_categories = {"신작", "후속권 업데이트"} & categories
        if RIDI_SET_CATEGORY in categories and not release_categories:
            pending = None
            return

        raw_title = pending["title"]
        if RIDI_SET_CATEGORY in categories and release_categories:
            raw_title = strip_ridi_set_note(raw_title)

        shown_categories = categories - {RIDI_SET_CATEGORY}
        title = format_title(
            raw_title,
            shown_categories,
            RIDI_CATEGORY_TAGS,
            RIDI_CATEGORY_ORDER,
        )
        add_item(
            groups,
            pending["day"],
            f"{month}/{pending['day']} ({pending['weekday']})",
            title,
            ridi_author_from_meta(pending["meta"]),
        )
        pending = None

    for raw in text.splitlines():
        line = raw.strip()
        if line == "캘린더 안내":
            flush_pending()
            break

        m = date_re.match(line)
        if m:
            flush_pending()
            m_month = int(m.group(1))
            day = int(m.group(2))
            weekday = m.group(3).strip()
            current = (m_month, day, weekday)
            continue

        b = bullet_re.match(line)
        if b:
            flush_pending()
            if current and current[0] == month:
                pending = {
                    "day": current[1],
                    "weekday": current[2],
                    "title": b.group(1),
                    "meta": [],
                }
            continue

        if pending:
            pending["meta"].append(line)

    flush_pending()

    if not groups:
        raise ValueError("리디 이번 달 출시작을 파싱하지 못했습니다.")
    return groups, RIDI_URL


def find_aladin_calendar(month: int):
    soup = BeautifulSoup(fetch_aladin_html(ALADIN_LIST_URL), "html.parser")

    candidates = []
    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        m = ALADIN_CALENDAR_RE.search(text)
        if not m or int(m.group("month")) != month:
            continue
        href = urljoin(ALADIN_LIST_URL, a["href"])
        event_m = re.search(r"[?&]EventId=(\d+)", href, re.I)
        event_id = int(event_m.group(1)) if event_m else 0
        if event_id:
            href = f"https://www.aladin.co.kr/events/wevent.aspx?EventId={event_id}"
        candidates.append((event_id, href))

    if not candidates:
        raise ValueError(f"알라딘 {month}월 eBook 만화 출간 캘린더를 찾지 못했습니다.")
    return max(candidates, key=lambda x: x[0])[1]


def aladin_icon_map(soup: BeautifulSoup):
    mapping = dict(ALADIN_LABEL_FALLBACK)
    labels = [
        "알라딘 독점",
        "신간",
        "후속권 업데이트",
        "후속권",
        "신규 연재 업데이트",
        "신규 연재",
        "특가 세트",
    ]
    for img in soup.find_all("img", src=True):
        alt = (img.get("alt") or "").strip()
        if not alt:
            continue
        for label in labels:
            if label in alt:
                mapping[image_key(img["src"])] = label
                break
    return mapping


def parse_aladin(year: int, month: int):
    """알라딘 캘린더 HTML의 출시일 박스에서 제목과 구분 아이콘을 읽는다."""
    url = find_aladin_calendar(month)
    soup = BeautifulSoup(fetch_aladin_html(url), "html.parser")
    icon_map = aladin_icon_map(soup)
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

        for row in box.select("table.monthC_comicbox_table tr"):
            cell = row.select_one("td.monthC_comicbox_text1")
            if not cell:
                continue
            title = cell.get_text(" ", strip=True)
            author_cell = cell.find_next_sibling("td")
            author = author_cell.get_text(" ", strip=True) if author_cell else ""

            category = None
            icon = row.find("img", class_="monthC_label_s")
            if icon and icon.get("src"):
                category = icon_map.get(image_key(icon["src"]))

            if category == ALADIN_SET_CATEGORY:
                continue

            categories = {category} if category else set()
            rendered = format_title(title, categories, ALADIN_CATEGORY_TAGS)
            add_item(groups, day, f"{month}/{day} ({weekday})", rendered, author)

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
