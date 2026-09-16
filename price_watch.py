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


def result(
    title,
    price,
    region="",
    *,
    discount_end="",
    sale_active=None,
    discount_percent=0,
    base_discount=0,
    base_discount_label="",
):
    return {
        "title": title,
        "price": price,
        "region": region,
        "discount_end": discount_end,
        "sale_active": sale_active,
        "discount_percent": int(discount_percent or 0),
        "base_discount": int(base_discount or 0),
        "base_discount_label": base_discount_label,
    }


def format_discount_end_datetime(dt: datetime):
    dt = dt.astimezone(KST)
    if dt.year >= 2099:
        return "종료 예정 없음"
    return dt.strftime("%Y-%m-%d %H:%M KST")


def format_discount_end_value(value):
    if value in (None, "", 0, "0"):
        return ""

    try:
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip())
        ):
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
    nested_end_keys = {"end", "enddate", "enddt", "endat", "endtime", "expiration"}

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


def parse_stove_page_info(url: str):
    info = {
        "discount_end": "",
        "collection_discount": 0,
        "is_collection": False,
    }
    try:
        soup = BeautifulSoup(fetch(url), "html.parser")
        text = clean_text(soup.get_text(" ", strip=True))
    except Exception:
        return info

    info["is_collection"] = bool(
        re.search(r"(?:\bCOLLECTION\b|컬렉션\s*(?:설명|할인|구매|에 포함))", text, re.I)
    )

    m = re.search(r"컬렉션\s*할인(?!\s*금액)\s*[-–]?\s*(\d{1,3})\s*%", text, re.I)
    if m:
        info["collection_discount"] = int(m.group(1))

    m = re.search(
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\s+"
        r"(\d{1,2}):(\d{2})\s*\((?:UTC\s*([+-]\d{1,2})(?::?(\d{2}))?|"
        r"(KST|JST|EDT|EST|PDT|PST))\)"
        r"[^\d]{0,30}할인\s*종료",
        text,
        re.I,
    )
    if not m:
        return info

    year, month, day, hour, minute = map(int, m.group(1, 2, 3, 4, 5))
    if m.group(6):
        offset_hour = int(m.group(6))
        offset_minute = int(m.group(7) or 0)
        sign = 1 if offset_hour >= 0 else -1
        tz = timezone(timedelta(hours=offset_hour, minutes=sign * offset_minute))
    else:
        named = (m.group(8) or "").upper()
        named_offsets = {
            "KST": 9,
            "JST": 9,
            "EDT": -4,
            "EST": -5,
            "PDT": -7,
            "PST": -8,
        }
        tz = timezone(timedelta(hours=named_offsets.get(named, 0)))
    try:
        info["discount_end"] = format_discount_end_datetime(
            datetime(year, month, day, hour, minute, tzinfo=tz)
        )
    except ValueError:
        pass
    return info


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
        discount = int(float(discount_rate or 0))
    except (TypeError, ValueError):
        discount = 0

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
    if discount > 0:
        price += f" (-{discount}%)"

    raw_end = find_discount_end_value(product)
    api_end = format_discount_end_value(raw_end) if discount > 0 else ""
    page_info = parse_stove_page_info(url) if discount > 0 else {
        "discount_end": "",
        "collection_discount": 0,
        "is_collection": False,
    }

    discount_end = api_end or page_info["discount_end"]
    base_discount = int(page_info.get("collection_discount") or 0)
    base_label = "컬렉션 할인" if page_info.get("is_collection") and base_discount > 0 else ""

    if discount > 0 and discount_end == "종료 예정 없음":
        if base_discount <= 0:
            base_discount = discount
            base_label = "컬렉션 할인" if page_info.get("is_collection") else "상시 할인"
        sale_active = False
        discount_end = ""
    elif discount > 0 and discount_end:
        sale_active = True
    elif discount > base_discount:
        sale_active = True
    elif discount > 0 and base_discount > 0:
        sale_active = False
    elif discount > 0:
        sale_active = None
    else:
        sale_active = False

    if sale_active:
        if discount_end:
            print(f"STOVE 기간 세일: -{discount}% / 종료 {discount_end}")
        else:
            print(f"STOVE 세일 감지: -{discount}% / 종료일 정보 없음")
    elif base_discount > 0:
        print(f"STOVE 상시 {base_label}: -{base_discount}%")

    return result(
        title,
        price,
        "한국",
        discount_end=discount_end if sale_active else "",
        sale_active=sale_active,
        discount_percent=discount,
        base_discount=base_discount,
        base_discount_label=base_label,
    )


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

        if id_key == "bundleid" and option.get("bundleid") not in (None, 0):
            if str(option.get("bundleid")) != str(item_id):
                continue

        for discount_obj in option.get("active_discounts") or []:
            if not isinstance(discount_obj, dict):
                continue
            raw_end = discount_obj.get("discount_end_date")
            try:
                ts = float(raw_end)
            except (TypeError, ValueError):
                continue
            if ts > now:
                end_times.append(ts)

    if not end_times:
        return ""

    result_end = format_discount_end_value(min(end_times))
    if result_end:
        print(f"Steam 할인 종료: {item_id} / {region} / {result_end}")
    return result_end


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
    try:
        base_discount = int(bundle.get("bundle_base_discount") or 0)
    except (TypeError, ValueError):
        base_discount = 0

    price = f"{symbol}{amount:,}"
    if discount > 0:
        price += f" (-{discount}%)"

    sale_active = discount > base_discount
    discount_end = steam_discount_end("bundleid", bundle_id, cc, lang, region) if sale_active else ""

    if sale_active:
        print(
            f"Steam Bundle 세일: {bundle_id} / {region} / "
            f"현재 -{discount}% / 기본 번들 -{base_discount}%"
        )
    elif base_discount > 0:
        print(f"Steam Bundle 상시 할인: {bundle_id} / {region} / -{base_discount}%")

    print(f"Steam Bundle resolve 사용: {bundle_id} / {region} / {formatted}")
    return result(
        title,
        price,
        region,
        discount_end=discount_end,
        sale_active=sale_active,
        discount_percent=discount,
        base_discount=base_discount,
        base_discount_label="번들 할인" if base_discount > 0 else "",
    )


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
    text = clean_text(soup.get_text(" ", strip=True))

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

    base_discount = 0
    m = re.search(r"(?:Bundle discount|꾸러미 할인)\s*:?\s*(\d{1,3})\s*%", text, re.I)
    if m:
        base_discount = int(m.group(1))

    current_discount = 0
    for node in soup.select(".bundle_area_purchase_game .discount_pct"):
        m = re.search(r"(\d{1,3})\s*%", clean_text(node.get_text(" ", strip=True)))
        if m:
            current_discount = max(current_discount, int(m.group(1)))
    if current_discount == 0:
        current_discount = base_discount

    for selector in [
        ".bundle_area_purchase_game .discount_final_price",
        ".bundle_area_purchase_game .game_purchase_price",
        ".bundle_area_purchase_game .your_price",
        ".bundle_area_purchase_game .discount_prices",
    ]:
        for node in soup.select(selector):
            amount = parse_steam_display_price(node.get_text(" ", strip=True), currency)
            if amount is None:
                continue
            sale_active = current_discount > base_discount
            discount_end = steam_discount_end("bundleid", bundle_id, cc, lang, region) if sale_active else ""
            price = f"{symbol}{amount:,}"
            if current_discount > 0:
                price += f" (-{current_discount}%)"
            return result(
                title or f"Steam Bundle {bundle_id}",
                price,
                region,
                discount_end=discount_end,
                sale_active=sale_active,
                discount_percent=current_discount,
                base_discount=base_discount,
                base_discount_label="번들 할인" if base_discount > 0 else "",
            )

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
            return result(title, f"{symbol}0", region, sale_active=False)

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
        return result(
            title,
            price,
            region,
            discount_end=discount_end,
            sale_active=discount > 0,
            discount_percent=discount,
        )

    raise ValueError("Steam 한국/일본 스토어 모두에서 판매 가격을 찾지 못했습니다.")


def parse_steam_bundle(url: str, bundle_id: str):
    for cc, lang, currency, symbol, region in [
        ("kr", "koreana", "KRW", "₩", "한국"),
        ("jp", "japanese", "JPY", "¥", "일본"),
    ]:
        try:
            parsed = steam_bundle_resolve(bundle_id, cc, lang, currency, symbol, region)
            if parsed:
                return parsed
        except Exception as e:
            print(f"Steam Bundle resolve 실패 ({region}): {e}", file=sys.stderr)

        try:
            parsed = steam_bundle_page(bundle_id, url, cc, lang, currency, symbol, region)
            if parsed:
                return parsed
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
                    return result(title, f"{currency} {offers['price']}".strip())
                stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
            elif isinstance(obj, list):
                stack.extend(obj)

    m = PRICE_RE.search(text)
    if m:
        return result(title, clean_text(m.group(0)))
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


def base_discount_line(item, bold=True):
    base = int(item.get("base_discount") or 0)
    if base <= 0:
        return ""
    label = item.get("base_discount_label") or "상시 할인"
    value = f"-{base}%"
    if bold:
        value = f"**{value}**"
    return f"상시 {label}: {value}\n"


def sale_status_line(item):
    active = item.get("sale_active")
    if active is True:
        return "세일 상태: **진행 중**\n"
    if active is False:
        return "세일 상태: **세일 아님**\n"
    return ""


def change_header(prev, current):
    if "sale_active" in prev:
        old_sale = prev.get("sale_active")
        new_sale = current.get("sale_active")
        if old_sale is False and new_sale is True:
            return "🟢 **세일 시작**"
        if old_sale is True and new_sale is False:
            return "🔴 **세일 종료**"
        if old_sale is True and new_sale is True:
            return "🟡 **세일/가격 정보 변경**"
    return "💰 **가격/할인 정보 변경 감지**"


def send_discord(index: int, prev: dict, current: dict, url: str):
    region = current.get("region", "")
    region_line = f"지역: **{region}**\n" if region else ""

    old_end = prev.get("discount_end", "")
    new_end = current.get("discount_end", "")
    if new_end and old_end and new_end != old_end:
        discount_line = f"할인 종료: `{old_end}` → `{new_end}`\n"
    elif new_end:
        discount_line = f"할인 종료: **{new_end}**\n"
    elif old_end and old_end != new_end:
        discount_line = f"할인 종료: `{old_end}` → `종료/정보 없음`\n"
    else:
        discount_line = ""

    post_discord(
        f"{change_header(prev, current)}\n\n"
        f"**#{index} · {current['title']}**\n"
        f"{region_line}"
        f"이전: `{prev.get('price', '알 수 없음')}`\n"
        f"현재: `{current['price']}`\n"
        f"{sale_status_line(current)}"
        f"{base_discount_line(current)}"
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
        if item.get("sale_active") is True:
            lines.append("세일 상태: **진행 중**")
        elif item.get("sale_active") is False:
            lines.append("세일 상태: **세일 아님**")
        if int(item.get("base_discount") or 0) > 0:
            label = item.get("base_discount_label") or "상시 할인"
            lines.append(f"상시 {label}: **-{item['base_discount']}%**")
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


def log_suffix(item):
    parts = []
    if item.get("sale_active") is True:
        parts.append("세일 진행 중")
    elif item.get("sale_active") is False:
        parts.append("세일 아님")

    base = int(item.get("base_discount") or 0)
    if base > 0:
        label = item.get("base_discount_label") or "상시 할인"
        parts.append(f"상시 {label} -{base}%")

    if item.get("discount_end"):
        parts.append(f"할인 종료 {item['discount_end']}")
    return (" / " + " / ".join(parts)) if parts else ""


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
            parsed = parse_page(url)
            prev = old_items.get(url)

            current = {
                **parsed,
                "url": url,
            }
            new_items[url] = current
            report_items.append({"index": index, **current})

            suffix = log_suffix(current)
            if prev is None:
                print(f"기준점 등록: #{index} {current['title']} = {current['price']} ({current['region']}){suffix}")
                continue

            price_or_region_changed = (
                prev.get("price") != current["price"]
                or prev.get("region", "") != current["region"]
            )
            end_changed = (
                "discount_end" in prev
                and prev.get("discount_end", "") != current.get("discount_end", "")
            )
            sale_changed = (
                "sale_active" in prev
                and prev.get("sale_active") != current.get("sale_active")
            )
            base_changed = (
                "base_discount" in prev
                and int(prev.get("base_discount") or 0) != int(current.get("base_discount") or 0)
            )

            if price_or_region_changed or end_changed or sale_changed or base_changed:
                if sale_changed:
                    print(
                        f"세일 상태 변경: #{index} {current['title']}: "
                        f"{prev.get('sale_active')} -> {current.get('sale_active')}{suffix}"
                    )
                elif price_or_region_changed:
                    print(
                        f"가격/지역 변경: #{index} {current['title']}: "
                        f"{prev.get('price')} -> {current['price']} [{current['region']}]{suffix}"
                    )
                elif base_changed:
                    print(
                        f"상시 할인 변경: #{index} {current['title']}: "
                        f"-{int(prev.get('base_discount') or 0)}% -> "
                        f"-{int(current.get('base_discount') or 0)}%"
                    )
                else:
                    print(
                        f"할인 종료 변경: #{index} {current['title']}: "
                        f"{prev.get('discount_end', '') or '정보 없음'} -> "
                        f"{current.get('discount_end', '') or '정보 없음'}"
                    )
                send_discord(index, prev, current, url)
            else:
                print(
                    f"변경 없음: #{index} {current['title']} = "
                    f"{current['price']} ({current['region']}){suffix}"
                )
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
