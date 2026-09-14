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

STOVE_HEADERS = {
    **HEADERS,
    "Cookie": "LOCALE=KO; NNTO=KR; PRM=KO; REGULATION=KOREA; TZ=Asia%2FSeoul; TZ_OFFSET=540; dvc=pc",
}

PRICE_RE = re.compile(r"(?:₩|￦|¥|￥|\$|€|£)\s?[0-9][0-9,.]*|[0-9][0-9,.]*\s?(?:원|엔)")
STEAM_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)")


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
    host = urlparse(url).netloc.lower()
    headers = STOVE_HEADERS if host.endswith("store.onstove.com") else HEADERS
    r = requests.get(url, headers=headers, timeout=30)
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

    marker = "판매 가격(할인 포함)"
    idx = text.find(marker)
    if idx >= 0:
        tail = text[idx + len(marker): idx + len(marker) + 120]
        m = PRICE_RE.search(tail)
        if m:
            price = clean_text(m.group(0))
            if not ("₩" in price or "￦" in price or price.endswith("원")):
                raise ValueError(f"STOVE가 한국 원화가 아닌 가격({price})을 반환했습니다. 한국 국가 쿠키 적용 여부를 확인해야 합니다.")
            return title, price

    buy_idx = text.find("구매")
    if buy_idx >= 0:
        section = text[buy_idx: buy_idx + 800]
        prices = PRICE_RE.findall(section)
        krw_prices = [p for p in prices if ("₩" in p or "￦" in p or p.endswith("원"))]
        if krw_prices:
            return title, clean_text(krw_prices[-1])

    raise ValueError("STOVE 페이지에서 한국 원화 판매 가격을 찾지 못했습니다.")


def parse_steam(url: str):
    m = STEAM_APP_RE.search(url)
    if not m:
        raise ValueError("Steam URL에서 App ID를 찾지 못했습니다.")
    appid = m.group(1)
    api_url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=kr&l=koreana"
    r = requests.get(api_url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json().get(appid, {})
    if not data.get("success"):
        raise ValueError("Steam Store API에서 상품 정보를 받지 못했습니다.")
    info = data.get("data", {})
    title = info.get("name") or f"Steam App {appid}"
    if info.get("is_free"):
        return title, "₩0"
    po = info.get("price_overview")
    if not po:
        raise ValueError("Steam 상품에 현재 판매 가격 정보가 없습니다.")
    currency = po.get("currency")
    if currency != "KRW":
        raise ValueError(f"Steam API가 KRW가 아닌 통화({currency})를 반환했습니다.")
    final = int(po.get("final", 0))
    return title, f"₩{final:,}"


def parse_generic(url: str, soup: BeautifulSoup, text: str):
    title = parse_title(soup)

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

    m = PRICE_RE.search(text)
    if m:
        return title, clean_text(m.group(0))

    raise ValueError("가격을 자동 추출하지 못했습니다. 이 사이트용 파서 추가가 필요합니다.")


def parse_page(url: str, html: str | None = None):
    host = urlparse(url).netloc.lower()

    if host.endswith("store.steampowered.com"):
        return parse_steam(url)

    if html is None:
        html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))

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
            host = urlparse(url).netloc.lower()
            if host.endswith("store.steampowered.com"):
                title, price = parse_page(url)
            else:
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
