import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

URLS_FILE = Path("price_urls.txt")
STATE_FILE = Path("price_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
SEND_CURRENT_REPORT = os.environ.get("SEND_CURRENT_REPORT", "").lower() in {"1", "true", "yes", "on"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

STOVE_PRODUCT_RE = re.compile(r"store\.onstove\.com/(?:[a-z]{2}/)?games/(\d+)", re.I)
STEAM_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.I)
PRICE_RE = re.compile(r"(?:₩|￦|¥|￥|\$|€|£)\s?[0-9][0-9,.]*|[0-9][0-9,.]*\s?(?:원|엔)")

# STOVE 상품 페이지의 가격/구매 관련 컴포넌트 타입.
# component_id는 하드코딩하지 않고 /components API에서 매번 자동으로 찾는다.
STOVE_PRICE_COMPONENT_TYPES = [
    "INDIE_PRODUCT_PRECESSIONS_COMPONENT",  # 구매 가능 상품
    "INDIE_PRODUCT_PURCHASE_COMPONENT",     # 장바구니/구매
]


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
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True))
    return "가격 추적 상품"


def find_stove_price_object(obj, product_no: int):
    """현재 상품의 KRW amount를 우선 찾고, 없으면 KRW 가격 객체를 후보로 반환한다."""
    fallback = None

    def walk(value):
        nonlocal fallback
        if isinstance(value, dict):
            amount = value.get("amount")
            if isinstance(amount, dict):
                currency = str(amount.get("currency") or "").upper()
                sales_price = amount.get("salesPrice")
                original_price = amount.get("originalPrice")
                if currency == "KRW" and (sales_price is not None or original_price is not None):
                    pn = value.get("productNo")
                    if str(pn) == str(product_no):
                        return value
                    if fallback is None:
                        fallback = value

            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found

        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found is not None:
                    return found

        return None

    exact = walk(obj)
    return exact or fallback


def debug_stove_response(data, component_id: str):
    """Actions 로그에 가격 관련 키의 실제 JSON 경로/주변 객체를 짧게 출력한다."""
    if isinstance(data, dict):
        top = ", ".join(str(k) for k in list(data.keys())[:30])
        print(f"STOVE DEBUG [{component_id}] top-level keys: {top}")
    elif isinstance(data, list):
        print(f"STOVE DEBUG [{component_id}] top-level: list(len={len(data)})")
    else:
        print(f"STOVE DEBUG [{component_id}] top-level: {type(data).__name__}")

    interesting = {
        "salesPrice",
        "originalPrice",
        "discountRate",
        "currency",
        "productNo",
        "product_no",
        "amount",
        "price",
        "salePrice",
        "sale_price",
    }
    hits = []

    def walk(value, path="$", depth=0):
        if len(hits) >= 25 or depth > 20:
            return
        if isinstance(value, dict):
            matched = [k for k in value.keys() if str(k) in interesting]
            if matched:
                try:
                    preview = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                except Exception:
                    preview = repr(value)
                if len(preview) > 1000:
                    preview = preview[:1000] + "..."
                hits.append((path, matched, preview))
            for key, child in value.items():
                walk(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for i, child in enumerate(value[:100]):
                walk(child, f"{path}[{i}]", depth + 1)

    walk(data)
    if not hits:
        try:
            preview = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            preview = repr(data)
        if len(preview) > 2500:
            preview = preview[:2500] + "..."
        print(f"STOVE DEBUG [{component_id}] 가격 관련 키 없음. 응답 앞부분: {preview}")
        return

    for path, keys, preview in hits:
        print(f"STOVE DEBUG [{component_id}] path={path} keys={','.join(keys)} object={preview}")


def stove_api_headers():
    caller_uuid = str(uuid.uuid4())
    return {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://store.onstove.com",
        "Referer": "https://store.onstove.com/",
        "caller-detail": caller_uuid,
        "caller-id": "indie-web-store",
        "x-client-lang": "ko",
        "x-device-type": "P01",
        "x-lang": "ko",
        "x-nation": "KR",
        "x-timezone": "Asia/Seoul",
        "x-utc-offset": "540",
        "x-uuid": caller_uuid,
    }


def get_stove_price_components(product_no: int, headers):
    """INDIE_PRODUCT 페이지 정의에서 가격/구매 관련 component_id를 자동으로 찾는다."""
    api_url = "https://api.onstove.com/store/v1.0/components"
    params = {
        "search_type": "PAGE_ID",
        "search_value": "INDIE_PRODUCT",
        "product_no": str(product_no),
        "timestemp": str(int(time.time() * 1000)),
    }
    r = requests.get(api_url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    # 응답 최상위 구조가 바뀌더라도 components 배열을 재귀적으로 찾는다.
    components = None

    def find_components(value):
        nonlocal components
        if components is not None:
            return
        if isinstance(value, dict):
            candidate = value.get("components")
            if isinstance(candidate, list):
                components = candidate
                return
            for child in value.values():
                find_components(child)
        elif isinstance(value, list):
            for child in value:
                find_components(child)

    find_components(data)
    if not components:
        raise ValueError("STOVE /components 응답에서 components 목록을 찾지 못했습니다.")

    selected = []
    for component_type in STOVE_PRICE_COMPONENT_TYPES:
        for component in components:
            if not isinstance(component, dict):
                continue
            if component.get("component_type") != component_type:
                continue
            component_id = component.get("component_id")
            if component_id and component_id not in selected:
                selected.append(component_id)

    if not selected:
        available = sorted(
            {
                str(c.get("component_type"))
                for c in components
                if isinstance(c, dict) and c.get("component_type")
            }
        )
        raise ValueError(
            "STOVE 페이지 정의에서 가격 관련 컴포넌트를 찾지 못했습니다. "
            f"available_types={','.join(available)}"
        )

    return selected


def parse_stove(url: str):
    m = STOVE_PRODUCT_RE.search(url)
    if not m:
        raise ValueError("STOVE URL에서 product_no를 찾지 못했습니다.")
    product_no = int(m.group(1))

    headers = stove_api_headers()
    component_ids = get_stove_price_components(product_no, headers)
    print(f"STOVE 가격 컴포넌트 자동 탐색: {','.join(component_ids)}")

    api_url = "https://api.onstove.com/store/v1.0/components/groups/product-merge"
    product = None
    tried = []

    for component_id in component_ids:
        params = {
            "component_ids": component_id,
            "product_no": str(product_no),
            "preview": "",
            "timestemp": str(int(time.time() * 1000)),
        }
        tried.append(component_id)
        r = requests.get(api_url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        debug_stove_response(data, component_id)
        product = find_stove_price_object(data, product_no)
        if product:
            print(f"STOVE 가격 컴포넌트 사용: {component_id}")
            break

    if not product:
        raise ValueError(
            "STOVE KR 상품 API에서 KRW 가격 데이터를 찾지 못했습니다. "
            f"자동 탐색 component_ids={','.join(tried)}"
        )

    amount = product.get("amount") or {}
    currency = str(amount.get("currency") or "").upper()
    if currency != "KRW":
        raise ValueError(f"STOVE KR 상품 API가 KRW가 아닌 통화({currency or '없음'})를 반환했습니다.")

    sales_price = amount.get("salesPrice")
    original_price = amount.get("originalPrice")
    discount_rate = amount.get("discountRate")
    if sales_price is None:
        sales_price = original_price
    if sales_price is None:
        raise ValueError("STOVE KR 상품 API 응답에 판매가가 없습니다.")

    title = product.get("productName")
    title_obj = product.get("title")
    if not title and isinstance(title_obj, dict):
        title = title_obj.get("text")
    if not title:
        try:
            html = fetch(url)
            title = parse_title(BeautifulSoup(html, "html.parser"))
        except Exception:
            title = f"STOVE 상품 {product_no}"

    price = f"₩{int(sales_price):,}"
    if discount_rate not in (None, 0, "0"):
        price += f" (-{discount_rate}%)"
    return title, price, "한국"


def steam_appdetails(appid: str, cc: str, lang: str):
    api_url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc={cc}&l={lang}"
    r = requests.get(api_url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json().get(appid, {})
    if not data.get("success"):
        return None
    return data.get("data", {})


def steam_major_price(po):
    # Steam final 값은 1/100 단위 정수다. JPY/KRW도 100으로 나눠 표시해야 한다.
    return int(po.get("final", 0)) // 100


def parse_steam(url: str):
    m = STEAM_APP_RE.search(url)
    if not m:
        raise ValueError("Steam URL에서 App ID를 찾지 못했습니다.")
    appid = m.group(1)

    info = steam_appdetails(appid, "kr", "koreana")
    if info:
        title = info.get("name") or f"Steam App {appid}"
        if info.get("is_free"):
            return title, "₩0", "한국"
        po = info.get("price_overview")
        if po and po.get("currency") == "KRW":
            return title, f"₩{steam_major_price(po):,}", "한국"

    info = steam_appdetails(appid, "jp", "japanese")
    if info:
        title = info.get("name") or f"Steam App {appid}"
        if info.get("is_free"):
            return title, "¥0", "일본"
        po = info.get("price_overview")
        if po and po.get("currency") == "JPY":
            return title, f"¥{steam_major_price(po):,}", "일본"

    raise ValueError("Steam 한국/일본 스토어 모두에서 판매 가격을 찾지 못했습니다.")


def parse_generic(url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text(" ", strip=True))
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
                    return title, f"{currency} {offers['price']}".strip(), ""
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                stack.extend(obj)

    m = PRICE_RE.search(text)
    if m:
        return title, clean_text(m.group(0)), ""
    raise ValueError("가격을 자동 추출하지 못했습니다. 이 사이트용 파서 추가가 필요합니다.")


def parse_page(url: str):
    host = urlparse(url).netloc.lower()
    if host.endswith("store.steampowered.com"):
        return parse_steam(url)
    if host.endswith("store.onstove.com"):
        return parse_stove(url)
    return parse_generic(url, fetch(url))


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


def post_discord(content: str):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def send_discord(title: str, old_price: str, new_price: str, url: str, region: str = ""):
    region_line = f"지역: **{region}**\n" if region else ""
    post_discord(
        "💰 **가격 변경 감지**\n\n"
        f"**{title}**\n"
        f"{region_line}"
        f"이전: `{old_price}`\n"
        f"현재: `{new_price}`\n"
        f"<{url}>"
    )


def send_current_report(items, failures):
    lines = ["📋 **현재 가격 목록**", ""]
    for item in items:
        region = f" · {item['region']}" if item.get("region") else ""
        lines.append(f"**{item['title']}**")
        lines.append(f"현재: `{item['price']}`{region}")
        lines.append(f"<{item['url']}>")
        lines.append("")
    if failures:
        lines.append("⚠️ **조회 실패**")
        lines.extend(f"- {x}" for x in failures)

    current = ""
    chunks = []
    for line in lines:
        candidate = current + line + "\n"
        if len(candidate) > 1900 and current:
            chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip())
    for chunk in chunks:
        post_discord(chunk)
        time.sleep(0.4)


def main():
    urls = read_urls()
    state = load_state()
    old_items = state.get("items", {})
    new_items = {}
    failures = []
    report_items = []

    if not urls:
        print("price_urls.txt에 추적 URL이 없습니다.")
        save_state({"items": {}})
        return

    for url in urls:
        try:
            title, price, region = parse_page(url)
            prev = old_items.get(url)
            item = {"title": title, "price": price, "region": region, "url": url}
            new_items[url] = item
            report_items.append(item)

            if prev is None:
                print(f"기준점 등록: {title} = {price} ({region})")
            elif prev.get("price") != price or prev.get("region", "") != region:
                print(f"가격/지역 변경: {title}: {prev.get('price')} -> {price} [{region}]")
                send_discord(title, prev.get("price", "알 수 없음"), price, url, region)
            else:
                print(f"변경 없음: {title} = {price} ({region})")
        except Exception as e:
            msg = f"{url}: {e}"
            failures.append(msg)
            print(f"ERROR: {msg}", file=sys.stderr)
            if url in old_items:
                new_items[url] = old_items[url]
        time.sleep(0.5)

    save_state({"items": new_items})

    if SEND_CURRENT_REPORT:
        send_current_report(report_items, failures)
        print("현재 가격 목록을 Discord로 전송했습니다.")

    if failures:
        print("\n일부 URL 확인 실패:", file=sys.stderr)
        for x in failures:
            print(f"- {x}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
