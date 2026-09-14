import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

URLS_FILE = Path("price_urls.txt")
STATE_FILE = Path("price_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

PRICE_RE = re.compile(r"(?:₩|￦|¥|￥|\$|€|£)\s?[0-9][0-9,.]*|[0-9][0-9,.]*\s?(?:원|엔)")


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def read_urls():
    if not URLS_FILE.exists():
        return []
    out = []
    seen = set()
    for raw in URLS_FILE.read_text(encoding="utf-8").splitlines():
        url = raw.strip()
        if not url or url.startswith("#"):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def fetch(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def parse_title(soup: BeautifulSoup):
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return clean_text(og["content"])
    h1 = soup.find("h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    h2 = soup.find("h2")
    if h2:
        return clean_text(h2.get_text(" ", strip=True))
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True))
    return "가격 추적 상품"


def parse_stove(url: str, soup: BeautifulSoup, text: str):
    title = parse_title(soup)

    # STOVE 컬렉션/상품 페이지는 본문에 '판매 가격(할인 포함)' 뒤로 최종 판매가가 표시됨.
    marker = "판매 가격(할인 포함)"
    idx = text.find(marker)
    if idx >= 0:
        tail = text[idx + len(marker): idx + len(marker) + 120]
        m = PRICE_RE.search(tail)
        if m:
            return title, clean_text(m.group(0))

    # 일반 구매 영역 fallback: '구매' 이후 짧은 구간에서 마지막 가격을 최종가로 취급.
    buy_idx = text.find("구매")
    if buy_idx >= 0:
        section = text[buy_idx: buy_idx + 800]
        prices = PRICE_RE.findall(section)
        if prices:
            return title, clean_text(prices[-1])

    raise ValueError("STOVE 페이지에서 판매 가격을 찾지 못했습니다.")


def parse_generic(url: str, soup: BeautifulSoup, text: str):
    title = parse_title(soup)

    # JSON-LD Product/Offer 우선
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                offers = obj.get("offers")
                if isinstance(offers, dict) and offers.get("price") is not None:
                    currency = offers.get("priceCurrency", "")
                    return title, f"{currency} {offers['price']}".strip()
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                stack.extend(obj)

    # meta/itemprop 가격 fallback
    for attrs in (
        {"itemprop": "price"},
        {"property": "product:price:amount"},
        {"name": "price"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            currency = ""
            cur = soup.find("meta", attrs={"itemprop": "priceCurrency"})
            if cur and cur.get("content"):
                currency = cur["content"]
            return title, f"{currency} {tag['content']}".strip()

    # 최후 fallback: 페이지에서 보이는 첫 통화 가격
    m = PRICE_RE.search(text)
    if m:
        return title, clean_text(m.group(0))

    raise ValueError("가격을 자동 추출하지 못했습니다. 이 사이트용 파서 추가가 필요합니다.")


def parse_page(url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
    host = urlparse(url).netloc.lower()

    if host.endswith("store.onstove.com"):
        return parse_stove(url, soup, text)
    return parse_generic(url, soup, text)


def load_state():
    if not STATE_FILE.exists():
        return {"items": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), dict):
            return data
    except Exception:
        pass
    return {"items": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_discord(title: str, old_price: str, new_price: str, url: str):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")

    content = (
        "💰 **가격 변경 감지**\n\n"
        f"**{title}**\n"
        f"이전: `{old_price}`\n"
        f"현재: `{new_price}`\n"
        f"<{url}>"
    )
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def main():
    urls = read_urls()
    state = load_state()
    old_items = state.get("items", {})
    new_items = {}
    failures = []

    if not urls:
        print("price_urls.txt에 추적 URL이 없습니다.")
        save_state({"items": {}})
        return

    for url in urls:
        try:
            html = fetch(url)
            title, price = parse_page(url, html)
            prev = old_items.get(url)

            new_items[url] = {
                "title": title,
                "price": price,
                "url": url,
            }

            if prev is None:
                print(f"기준점 등록: {title} = {price}")
            elif prev.get("price") != price:
                print(f"가격 변경: {title}: {prev.get('price')} -> {price}")
                send_discord(title, prev.get("price", "알 수 없음"), price, url)
            else:
                print(f"변경 없음: {title} = {price}")

        except Exception as e:
            failures.append(f"{url}: {e}")
            print(f"ERROR: {url}: {e}", file=sys.stderr)
            # 일시 오류로 상태를 잃지 않도록 기존 값을 유지
            if url in old_items:
                new_items[url] = old_items[url]
        time.sleep(0.5)

    save_state({"items": new_items})

    if failures:
        print("\n일부 URL 확인 실패:", file=sys.stderr)
        for x in failures:
            print(f"- {x}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
