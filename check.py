import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://series.naver.com"
SEARCH_URL = "https://series.naver.com/search/search.series"
QUERY = "세트"
PAGES = 3
STATE_FILE = Path("state.json")
TODAY_SETS_FILE = Path(
    os.environ.get(
        "SERIES_TODAY_FILE",
        str(Path.home() / ".local/state/naver-series-set-checker/today_sets.json"),
    )
)
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
        if not title or "세트" not in title:
            continue

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


def is_bl_product(item):
    """네이버 시리즈 작품 장르가 BL이면 True."""
    if re.search(r"\[\s*BL\s*\]", item.get("title", ""), re.IGNORECASE):
        return True

    try:
        product_no = item["id"]
        detail_url = f"https://m.series.naver.com/comic/detail.series?productNo={product_no}"
        r = requests.get(detail_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        strings = [clean_text(x) for x in soup.stripped_strings if clean_text(x)]

        for i, text in enumerate(strings):
            if text != "장르":
                continue

            for value in strings[i + 1:i + 4]:
                normalized = value.strip().upper()
                if normalized == "BL":
                    return True
                if value in {"이용가", "작가", "연재상태", "작품 소개", "가격 정보"}:
                    break
            return False

        visible_text = "\n".join(strings)
        if re.search(r"(?:^|\n)장르\s*(?:\n\s*)?BL(?:\s|\n|$)", visible_text, re.IGNORECASE):
            return True

    except Exception as e:
        print(f"BL 태그 확인 실패: {item.get('title', item.get('id'))} ({e})")

    return False


def normalize_discount_end(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    m = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", text)
    if m:
        year, month, day = map(int, m.groups())
        return f"{year:04d}-{month:02d}-{day:02d}까지"
    return text


def parse_set_discount_html(html: str, mobile: bool = False):
    soup = BeautifulSoup(html, "html.parser")

    if mobile:
        discount_node = soup.select_one(".point_price")
        end_node = soup.select_one(".sub_info")
    else:
        discount_node = soup.select_one(".area_price .price_info")
        end_node = soup.select_one(".area_price .price_info_sub")

    discount = clean_text(discount_node.get_text(" ", strip=True)) if discount_node else ""
    discount_end = normalize_discount_end(end_node.get_text(" ", strip=True)) if end_node else ""

    if discount and not re.search(r"\d+\s*%\s*할인", discount):
        discount = ""

    return discount, discount_end


def add_set_discount_info(item):
    """신규 세트의 할인율/표시된 할인 종료일을 상세 페이지에서 보강한다.

    네이버가 종료일을 제공하지 않는 세트는 discount_end를 빈 값으로 둔다.
    """
    product_no = item["id"]
    discount = ""
    discount_end = ""

    targets = [
        (False, f"https://series.naver.com/comic/detail.series?productNo={product_no}"),
        (True, f"https://m.series.naver.com/comic/detail.series?productNo={product_no}"),
    ]

    for mobile, url in targets:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            found_discount, found_end = parse_set_discount_html(r.text, mobile=mobile)
            if found_discount and not discount:
                discount = found_discount
            if found_end and not discount_end:
                discount_end = found_end
            if discount and discount_end:
                break
        except Exception as e:
            where = "모바일" if mobile else "PC"
            print(f"세트 할인 정보 확인 실패 ({where}): {item.get('title', product_no)} ({e})")

    item["discount"] = discount
    item["discount_end"] = discount_end

    if discount:
        suffix = f" / 종료 {discount_end}" if discount_end else " / 종료일 미표시"
        print(f"세트 할인 확인: {item['title']} / {discount}{suffix}")


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


def save_today_sets(items):
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "generatedAt": now.isoformat(),
        "items": [
            {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "discount": item.get("discount", ""),
                "discount_end": item.get("discount_end", ""),
            }
            for item in items
        ],
    }
    TODAY_SETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TODAY_SETS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def send_discord(new_items):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")

    chunks = []
    current = "🆕 **네이버 시리즈 '세트' 새 작품 감지**\n"

    for item in new_items:
        line = f"\n**{item['title']}**"
        if item.get("discount_end"):
            line += f" (~{item['discount_end']})"
        line += "\n"

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

    if old is None or not old.get("items"):
        print(f"첫 실행: 현재 {len(items)}개를 기준점으로 저장합니다. 알림은 보내지 않습니다.")
        save_today_sets([])
        save_state(items)
        return

    old_ids = set(old["items"].keys())
    new_items = [x for x in items if x["id"] not in old_ids]

    print(f"현재 {len(items)}개 / 신규 {len(new_items)}개")

    if new_items:
        notify_items = []
        skipped_bl = []
        for item in new_items:
            if is_bl_product(item):
                skipped_bl.append(item)
                print(f"BL 알림 제외: {item['title']}")
            else:
                add_set_discount_info(item)
                notify_items.append(item)
            time.sleep(0.2)

        if skipped_bl:
            print(f"BL 작품 {len(skipped_bl)}개 알림 제외")

        save_today_sets(notify_items)

        if notify_items:
            send_discord(notify_items)
            print(f"Discord 알림 전송 완료: {len(notify_items)}개")
        else:
            print("알림 대상 신규 작품 없음 (BL 제외 후 0개)")
    else:
        save_today_sets([])
        print("신규 작품 없음")

    save_state(items)


if __name__ == "__main__":
    main()
