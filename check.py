import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

BASE = "https://series.naver.com"
SEARCH_URL = "https://series.naver.com/search/search.series"
QUERY = "세트"
PAGES = 3
STATE_FILE = Path("state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def fetch_page(page: int):
    params = {
        "t": "comic",
        "q": QUERY,
        "so": "date.dsc",
        "page": page,
    }
    r = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_items(html: str):
    soup = BeautifulSoup(html, "html.parser")
    items = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "detail.series" not in href or "productNo=" not in href:
            continue

        url = urljoin(BASE, href)
        qs = parse_qs(urlparse(url).query)
        product_no = (qs.get("productNo") or [None])[0]
        if not product_no:
            continue

        title = clean_text(a.get_text(" ", strip=True))
        # 검색결과의 작품 링크는 제목이 길고, 네이버 검색어 특성상 [..세트]가 들어가는 경우가 대부분.
        if not title or "세트" not in title:
            continue

        # 제목 뒤에 "(총 N권/완결)" 같은 문자열이 같이 잡혀도 알림에 유용하므로 유지.
        date = ""
        meta_text = ""

        node = a
        for _ in range(6):
            if not getattr(node, "parent", None):
                break
            node = node.parent
            text = clean_text(node.get_text(" ", strip=True))
            m = re.search(r"\b(20\d{2}\.\d{2}\.\d{2}\.)\b", text)
            if m:
                date = m.group(1)
                meta_text = text
                break

        items[product_no] = {
            "id": product_no,
            "title": title,
            "url": url,
            "date": date,
            "meta": meta_text[:500],
        }

    return list(items.values())

def load_state():
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except Exception:
        pass
    return None

def save_state(items):
    payload = {
        "items": {x["id"]: x for x in items}
    }
    STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def send_discord(new_items):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")

    chunks = []
    current = "🆕 **네이버 시리즈 '세트' 새 작품 감지**\n"

    for item in new_items:
        line = f"\n**{item['title']}**"
        if item.get("date"):
            line += f"\n등록/업데이트: {item['date']}"
        line += f"\n<{item['url']}>\n"

        if len(current) + len(line) > 1900:
            chunks.append(current)
            current = "🆕 **네이버 시리즈 '세트' 새 작품 감지 (계속)**\n"
        current += line

    if current.strip():
        chunks.append(current)

    for content in chunks:
        r = requests.post(
            DISCORD_WEBHOOK,
            json={"content": content},
            timeout=30,
        )
        r.raise_for_status()
        time.sleep(0.5)

def main():
    all_items = {}
    for page in range(1, PAGES + 1):
        html = fetch_page(page)
        parsed = parse_items(html)
        print(f"page {page}: {len(parsed)} items")
        for item in parsed:
            all_items[item["id"]] = item
        time.sleep(0.5)

    items = list(all_items.values())
    if not items:
        print("작품을 하나도 추출하지 못했습니다. 사이트 구조가 바뀌었을 수 있습니다.")
        sys.exit(1)

    old = load_state()

    # 최초 실행: 현재 목록만 기준점으로 저장, Discord 알림은 보내지 않음.
    if old is None or not old.get("items"):
        print(f"첫 실행: 현재 {len(items)}개를 기준점으로 저장합니다. 알림은 보내지 않습니다.")
        save_state(items)
        return

    old_ids = set(old["items"].keys())
    new_items = [x for x in items if x["id"] not in old_ids]

    print(f"현재 {len(items)}개 / 신규 {len(new_items)}개")

    if new_items:
        # 최신순 페이지 순서를 최대한 유지
        send_discord(new_items)
        print("Discord 알림 전송 완료")
    else:
        print("신규 작품 없음")

    # 최신 스냅샷 저장
    save_state(items)

if __name__ == "__main__":
    main()
