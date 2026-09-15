import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URLS_FILE = Path("price_urls.txt")
STATE_FILE = Path("price_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
SEND_CURRENT_REPORT = os.environ.get("SEND_CURRENT_REPORT", "").lower() in {"1", "true", "yes", "on"}
ACTIONS_URL = "https://github.com/pingpingjinping/naver-series-set-checker/actions/workflows/check.yml"
KST = ZoneInfo("Asia/Seoul")

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
STEAM_BUNDLE_RE = re.compile(r"store\.steampowered\.com/bundle/(\d+)", re.I)
PRICE_RE = re.compile(r"(?:₩|￦|¥|￥|\$|€|£)\s?[0-9][0-9,.]*|[0-9][0-9,.]*\s?(?:원|엔)")

STOVE_PRICE_COMPONENT_TYPES = [
    "INDIE_PRODUCT_PRECESSIONS_COMPONENT",
    "INDIE_PRODUCT_PURCHASE_COMPONENT",
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


def get_any(d, *keys):
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


def format_discount_end_datetime(dt: datetime):
    dt = dt.astimezone(KST)
    # STOVE collection discounts often use year 2999/3000 as an effectively
    # open-ended sentinel rather than a meaningful customer-facing deadline.
    if dt.year >= 2099:
        return "종료 예정 없음"
    return dt.strftime("%Y-%m-%d %H:%M KST")


def format_discount_end_value(value):
    if value in (None, "", 0, "0"):
        return ""

    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip())):
            ts = float(value)
            if ts > 100_000_000_000:
                ts /= 1000.0
            return format_discount_end_datetime(datetime.fromtimestamp(ts, tz=timezone.utc))
    except (OverflowError, OSError, ValueError):
        pass

    if isinstance(value, str):
        raw = value.strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_discount_end_datetime(dt)
        except ValueError:
            pass

    return ""


def find_discount_end_value(obj, parent_key=""):
    direct_keys = {
        "discountenddate",
        "discountenddt",
        "discountendat",
        "discountendtime",
        "discountexpiration",
        "discountfinishdate",
        "discountfinishdt",
    }
    nested_end_keys = {"enddate", "enddt", "endat", "endtime", "expiration"}

    if isinstance(obj, dict):
        parent_norm = re.sub(r"[^a-z0-9]", "", str(parent_key).lower())
        for key, value in obj.items():
            key_norm = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if key_norm in direct_keys or ("discount" in parent_norm and key_norm in nested_end_keys):
                if value not in (None, "", 0, "0"):
                    return value
        for key, value in obj.items():
            found = find_discount_end_value(value, key)
            if found not in (None, "", 0, "0"):
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_discount_end_value(value, parent_key)
            if found not in (None, "", 0, "0"):
                return found
    return None


def parse_stove_page_discount_end(url: str):
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
        text = clean_text(soup.get_text(" ", strip=True))
    except Exception:
        return ""

    m = re.search(
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+"
        r"(\d{1,2}):(\d{2})\s*\(UTC\s*([+-]\d{1,2})(?::?(\d{2}))?\)"
        r"[^\d]{0,30}할인\s*종료",
        text,
        re.I,
    )
    if not m:
        return ""

    year, month, day, hour, minute = map(int, m.group(1, 2, 3, 4, 5))
    offset_hour = int(m.group(6))
    offset_minute = int(m.group(7) or 0)
    sign = 1 if offset_hour >= 0 else -1
    offset = timezone(timedelta(hours=offset_hour, minutes=sign * offset_minute))
    try:
        return format_discount_end_datetime(datetime(year, month, day, hour, minute, tzinfo=offset))
    except ValueError:
        return ""


def find_stove_price_object(obj, product_no: int):
    fallback = None

    def walk(value):
        nonlocal fallback
        if isinstance(value, dict):
            amount = value.get("amount")
            if isinstance(amount, dict):
                currency = str(amount.get("currency") or "").upper()
                sales_price = get_any(amount, "sales_price", "salesPrice", "sale_price", "salePrice")
                original_price = get_any(amount, "original_price", "originalPrice")
                if currency == "KRW" and (sales_price is not None or original_price is not None):
                    pn = get_any(value, "product_no", "productNo")
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
        raise ValueError("STOVE 페이지 정의에서 가격 관련 컴포넌트를 찾지 못했습니다.")
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
        product = find_stove_price_object(r.json(), product_no)
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

    sales_price = get_any(amount, "sales_price", "salesPrice", "sale_price", "salePrice")
    original_price = get_any(amount, "original_price", "originalPrice")
    discount_rate = get_any(amount, "discount_rate", "discountRate")
    if sales_price is None:
        sales_price = original_price
    if sales_price is None:
        raise ValueError("STOVE KR 상품 API 응답에 판매가가 없습니다.")

    try:
        discount_rate_num = int(float(discount_rate or 0))
    except (TypeError, ValueError):
        discount_rate_num = 0

    title = get_any(product, "product_name", "productName")
    title_obj = product.get("title")
    if not title and isinstance(title_obj, dict):
        title = title_obj.get("text")
    if not title:
        try:
            title = parse_title(BeautifulSoup(fetch(url), "html.parser"))
        except Exception:
            title = f"STOVE 상품 {product_no}"

    price = f"₩{int(float(sales_price)):,}"
    if discount_rate_num > 0:
        price += f" (-{discount_rate_num}%)"

    discount_end = ""
    if discount_rate_num > 0:
        discount_end = format_discount_end_value(find_discount_end_value(product))
        if not discount_end:
            discount_end = parse_stove_page_discount_end(url)
        if discount_end:
            print(f"STOVE 할인 종료: {discount_end}")

    return title, price, "한국", discount_end


def steam_appdetails(appid: str, cc: str, lang: str):
    api_url = "https://store.steampowered.com/api/appdetails"
    r = requests.get(
        api_url,
        headers=HEADERS,
        params={"appids": appid, "cc": cc, "l": lang},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get(appid, {})
    if not data.get("success"):
        return None
    return data.get("data", {})


def steam_major_price(po):
    return int(po.get("final", 0)) // 100


def steam_storebrowse_item(id_key: str, item_id: str, cc: str, lang: str):
    payload = {
        "ids": [{id_key: int(item_id)}],
        "context": {
            "language": lang,
            "country_code": cc.upper(),
            "steam_realm": 1,
        },
        "data_request": {
            "include_all_purchase_options": True,
            "include_best_purchase_option": True,
        },
    }
    r = requests.get(
        "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
        headers=HEADERS,
        params={"input_json": json.dumps(payload, separators=(",", ":"))},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("response", {}).get("store_items", [])
    if not items:
        return None
    return items[0]


def steam_discount_end(id_key: str, item_id: str, cc: str, lang: str, region: str):
    try:
        item = steam_storebrowse_item(id_key, item_id, cc, lang)
    except Exception as e:
        print(f"Steam 할인 종료 조회 실패 ({region}): {e}", file=sys.stderr)
        return ""
    if not item:
        return ""

    options = []
    best = item.get("best_purchase_option")
    if isinstance(best, dict):
        options.append(best)
    own = item.get("self_purchase_option")
    if isinstance(own, dict):
        options.append(own)
    for option in item.get("purchase_options") or []:
        if isinstance(option, dict):
            options.append(option)

    seen = set()
    end_times = []
    now = time.time()
    for option in options:
        marker = (option.get("packageid"), option.get("bundleid"), option.get("formatted_final_price"))
        if marker in seen:
            continue
        seen.add(marker)

        # For a bundle lookup, prefer purchase options that actually refer to
        # the requested bundle. App lookups may point to a package instead.
        if id_key == "bundleid" and option.get("bundleid") not in (None, 0):
            if str(option.get("bundleid")) != str(item_id):
                continue

        for discount in option.get("active_discounts") or []:
            if not isinstance(discount, dict):
                continue
            raw_end = discount.get("discount_end_date")
            try:
                ts = float(raw_end)
            except (TypeError, ValueError):
                continue
            if ts > now:
                end_times.append(ts)

    if not end_times:
        return ""

    # If multiple discounts are stacked, the current effective price can first
    # change when the earliest active discount expires.
    result = format_discount_end_value(min(end_times))
    if result:
        print(f"Steam 할인 종료: {item_id} / {region} / {result}")
    return result


def parse_steam_display_price(text: str, currency: str):
    text = clean_text(text)
    if currency == "KRW":
        patterns = [r"(?:₩|￦)\s*([0-9][0-9,.]*)", r"([0-9][0-9,.]*)\s*원"]
    elif currency == "JPY":
        patterns = [r"(?:¥|￥)\s*([0-9][0-9,.]*)", r"([0-9][0-9,.]*)\s*엔"]
    else:
        return None

    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        digits = re.sub(r"[^0-9]", "", m.group(1))
        if digits:
            return int(digits)
    return None


def steam_bundle_resolve(bundle_id: str, cc: str, lang: str, currency: str, symbol: str, region: str):
    api_url = "https://store.steampowered.com/actions/ajaxresolvebundles"
    headers = {
        **HEADERS,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": "https://store.steampowered.com/",
        "X-Requested-With": "XMLHttpRequest",
    }
    r = requests.get(
        api_url,
        headers=headers,
        params={"bundleids": bundle_id, "cc": cc.upper(), "l": lang},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list) or not data:
        return None

    bundle = next(
        (x for x in data if isinstance(x, dict) and str(x.get("bundleid")) == str(bundle_id)),
        None,
    )
    if bundle is None and isinstance(data[0], dict):
        bundle = data[0]
    if not bundle:
        return None

    title = clean_text(str(bundle.get("name") or f"Steam Bundle {bundle_id}"))
    formatted = clean_text(str(bundle.get("formatted_final_price") or ""))
    amount = parse_steam_display_price(formatted, currency)
    if amount is None:
        return None

    try:
        discount = int(bundle.get("discount_percent") or 0)
    except (TypeError, ValueError):
        discount = 0

    price = f"{symbol}{amount:,}"
    if discount > 0:
        price += f" (-{discount}%)"

    discount_end = steam_discount_end("bundleid", bundle_id, cc, lang, region) if discount > 0 else ""
    print(f"Steam Bundle resolve 사용: {bundle_id} / {region} / {formatted}")
    return title, price, region, discount_end


def steam_bundle_page(bundle_id: str, url: str, cc: str, lang: str, currency: str, symbol: str, region: str):
    cookies = {"birthtime": "0", "lastagecheckage": "1-January-1990"}
    r = requests.get(
        url,
        headers=HEADERS,
        params={"cc": cc, "l": lang},
        cookies=cookies,
        timeout=30,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    title = None
    for selector in ["h2.pageheader", ".pageheader", ".bundle_page_header h2"]:
        node = soup.select_one(selector)
        if node:
            title = clean_text(node.get_text(" ", strip=True))
            if title:
                break
    if not title:
        title = parse_title(soup)
    title = re.sub(r"\s+(?:on Steam|Steam에서)$", "", title, flags=re.I)

    for selector in [
        ".bundle_area_purchase_game .discount_final_price",
        ".bundle_area_purchase_game .game_purchase_price",
        ".bundle_area_purchase_game .your_price",
        ".bundle_area_purchase_game .discount_prices",
    ]:
        for node in soup.select(selector):
            amount = parse_steam_display_price(node.get_text(" ", strip=True), currency)
            if amount is not None:
                discount_end = steam_discount_end("bundleid", bundle_id, cc, lang, region)
                return title or f"Steam Bundle {bundle_id}", f"{symbol}{amount:,}", region, discount_end

    return None


def parse_steam_app(url: str, appid: str):
    for cc, lang, symbol, region, currency in [
        ("kr", "koreana", "₩", "한국", "KRW"),
        ("jp", "japanese", "¥", "일본", "JPY"),
    ]:
        info = steam_appdetails(appid, cc, lang)
        if not info:
            continue

        title = info.get("name") or f"Steam App {appid}"
        if info.get("is_free"):
            return title, f"{symbol}0", region, ""

        po = info.get("price_overview")
        if not po or po.get("currency") != currency:
            continue

        price = f"{symbol}{steam_major_price(po):,}"
        try:
            discount = int(po.get("discount_percent") or 0)
        except (TypeError, ValueError):
            discount = 0
        if discount > 0:
            price += f" (-{discount}%)"
        discount_end = steam_discount_end("appid", appid, cc, lang, region) if discount > 0 else ""
        return title, price, region, discount_end

    raise ValueError("Steam 한국/일본 스토어 모두에서 판매 가격을 찾지 못했습니다.")


def parse_steam_bundle(url: str, bundle_id: str):
    for cc, lang, currency, symbol, region in [
        ("kr", "koreana", "KRW", "₩", "한국"),
        ("jp", "japanese", "JPY", "¥", "일본"),
    ]:
        try:
            result = steam_bundle_resolve(bundle_id, cc, lang, currency, symbol, region)
            if result:
                return result
        except Exception as e:
            print(f"Steam Bundle resolve 실패 ({region}): {e}", file=sys.stderr)

        try:
            result = steam_bundle_page(bundle_id, url, cc, lang, currency, symbol, region)
            if result:
                return result
        except Exception as e:
            print(f"Steam Bundle 페이지 fallback 실패 ({region}): {e}", file=sys.stderr)

    raise ValueError("Steam Bundle 한국/일본 스토어 모두에서 판매 가격을 찾지 못했습니다.")


def parse_steam(url: str):
    m = STEAM_APP_RE.search(url)
    if m:
        return parse_steam_app(url, m.group(1))

    m = STEAM_BUNDLE_RE.search(url)
    if m:
        return parse_steam_bundle(url, m.group(1))

    raise ValueError("지원하는 Steam App/Bundle ID를 URL에서 찾지 못했습니다.")


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
                    return title, f"{currency} {offers['price']}".strip(), "", ""
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                stack.extend(obj)

    m = PRICE_RE.search(text)
    if m:
        return title, clean_text(m.group(0)), "", ""
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


def send_discord(
    index: int,
    title: str,
    old_price: str,
    new_price: str,
    url: str,
    region: str = "",
    discount_end: str = "",
    old_discount_end: str = "",
):
    region_line = f"지역: **{region}**\n" if region else ""
    if discount_end and old_discount_end and discount_end != old_discount_end:
        discount_line = f"할인 종료: `{old_discount_end}` → `{discount_end}`\n"
    elif discount_end:
        discount_line = f"할인 종료: **{discount_end}**\n"
    elif old_discount_end and old_discount_end != discount_end:
        discount_line = f"할인 종료: `{old_discount_end}` → `종료/정보 없음`\n"
    else:
        discount_line = ""

    post_discord(
        "💰 **가격/할인 정보 변경 감지**\n\n"
        f"**#{index} · {title}**\n"
        f"{region_line}"
        f"이전: `{old_price}`\n"
        f"현재: `{new_price}`\n"
        f"{discount_line}"
        f"<{url}>\n\n"
        f"⚙️ 가격 추적 관리: <{ACTIONS_URL}>"
    )


def send_current_report(items, failures):
    lines = ["📋 **현재 가격 목록**", ""]
    for item in items:
        region = f" · {item['region']}" if item.get("region") else ""
        lines.append(f"**#{item['index']} · {item['title']}**")
        lines.append(f"현재: `{item['price']}`{region}")
        if item.get("discount_end"):
            lines.append(f"할인 종료: **{item['discount_end']}**")
        lines.append(f"<{item['url']}>")
        lines.append("")
    if failures:
        lines.append("⚠️ **조회 실패**")
        lines.extend(f"- {x}" for x in failures)
        lines.append("")

    lines.append(f"⚙️ **URL 추가/삭제:** <{ACTIONS_URL}>")

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

    for index, url in enumerate(urls, start=1):
        try:
            title, price, region, discount_end = parse_page(url)
            prev = old_items.get(url)
            item = {
                "index": index,
                "title": title,
                "price": price,
                "region": region,
                "discount_end": discount_end,
                "url": url,
            }
            new_items[url] = {
                "title": title,
                "price": price,
                "region": region,
                "discount_end": discount_end,
                "url": url,
            }
            report_items.append(item)

            end_suffix = f" / 할인 종료 {discount_end}" if discount_end else ""
            if prev is None:
                print(f"기준점 등록: #{index} {title} = {price} ({region}){end_suffix}")
                continue

            price_or_region_changed = prev.get("price") != price or prev.get("region", "") != region
            # Migration-safe: old state entries have no discount_end key. Populate it
            # silently once, then detect real deadline changes from subsequent runs.
            end_changed = "discount_end" in prev and prev.get("discount_end", "") != discount_end

            if price_or_region_changed or end_changed:
                if price_or_region_changed:
                    print(f"가격/지역 변경: #{index} {title}: {prev.get('price')} -> {price} [{region}]{end_suffix}")
                else:
                    print(
                        f"할인 종료 변경: #{index} {title}: "
                        f"{prev.get('discount_end', '') or '정보 없음'} -> {discount_end or '정보 없음'}"
                    )
                send_discord(
                    index,
                    title,
                    prev.get("price", "알 수 없음"),
                    price,
                    url,
                    region,
                    discount_end,
                    prev.get("discount_end", ""),
                )
            else:
                print(f"변경 없음: #{index} {title} = {price} ({region}){end_suffix}")
        except Exception as e:
            msg = f"#{index} {url}: {e}"
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
