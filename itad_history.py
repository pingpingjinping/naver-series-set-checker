import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

ITAD_API_KEY = os.environ.get("ITAD_API_KEY", "").strip()
ITAD_API_BASE = "https://api.isthereanydeal.com"
STEAM_SHOP_ID = 61
KST = ZoneInfo("Asia/Seoul")

STEAM_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.I)
STEAM_BUNDLE_RE = re.compile(r"store\.steampowered\.com/bundle/(\d+)", re.I)


def steam_product(url: str):
    m = STEAM_APP_RE.search(url)
    if m:
        return "app", m.group(1)
    m = STEAM_BUNDLE_RE.search(url)
    if m:
        return "bundle", m.group(1)
    return None


def steamdb_url(url: str):
    product = steam_product(url)
    if not product:
        return ""
    kind, product_id = product
    return f"https://steamdb.info/{kind}/{product_id}/"


def region_country(region: str):
    if region == "한국":
        return "KR"
    if region == "일본":
        return "JP"
    return "US"


def format_money(currency: str, amount):
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return ""

    currency = (currency or "").upper()
    if currency == "JPY":
        return f"¥{int(round(value)):,}"
    if currency == "KRW":
        return f"₩{int(round(value)):,}"

    symbols = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
    }
    symbol = symbols.get(currency)
    if symbol:
        return f"{symbol}{value:,.2f}"
    return f"{value:,.2f} {currency}".strip()


def format_low_date(timestamp: str):
    if not timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _headers():
    if not ITAD_API_KEY:
        return None
    return {
        "ITAD-API-Key": ITAD_API_KEY,
        "Accept": "application/json",
        "User-Agent": "WalnutPi-Price-Watch/1.0",
    }


def fetch_steam_history_low(url: str, region: str):
    """Steam app/bundle의 해당 지역 Steam 역대 최저가를 ITAD에서 조회한다.

    실패해도 본 가격 추적을 망가뜨리지 않도록 빈 dict를 반환한다.
    """
    product = steam_product(url)
    headers = _headers()
    if not product or not headers:
        return {}

    kind, product_id = product
    shop_key = f"{kind}/{product_id}"
    country = region_country(region)

    try:
        lookup = requests.post(
            f"{ITAD_API_BASE}/lookup/id/shop/{STEAM_SHOP_ID}/v1",
            headers=headers,
            json=[shop_key],
            timeout=20,
        )
        lookup.raise_for_status()
        game_id = (lookup.json() or {}).get(shop_key)
        if not game_id:
            print(f"ITAD 매핑 없음: {shop_key} / {country}")
            return {
                "steamdb_url": steamdb_url(url),
            }

        low_response = requests.post(
            f"{ITAD_API_BASE}/games/storelow/v2",
            headers=headers,
            params={"country": country, "shops": str(STEAM_SHOP_ID)},
            json=[game_id],
            timeout=20,
        )
        low_response.raise_for_status()
        rows = low_response.json() or []
        if not rows:
            print(f"ITAD Steam 역대가 없음: {shop_key} / {country}")
            return {
                "itad_game_url": f"https://isthereanydeal.com/game/id:{game_id}/",
                "steamdb_url": steamdb_url(url),
            }

        lows = rows[0].get("lows") or []
        low = next(
            (
                x
                for x in lows
                if isinstance(x, dict)
                and isinstance(x.get("shop"), dict)
                and int(x["shop"].get("id") or 0) == STEAM_SHOP_ID
            ),
            None,
        )
        if not low:
            print(f"ITAD Steam store low 없음: {shop_key} / {country}")
            return {
                "itad_game_url": f"https://isthereanydeal.com/game/id:{game_id}/",
                "steamdb_url": steamdb_url(url),
            }

        price = low.get("price") or {}
        amount = price.get("amount")
        currency = str(price.get("currency") or "").upper()
        formatted = format_money(currency, amount)
        cut = int(low.get("cut") or 0)
        date = format_low_date(low.get("timestamp") or "")

        if not formatted:
            return {}

        print(
            f"ITAD Steam 역대 최저가: {shop_key} / {country} / "
            f"{formatted} / -{cut}% / {date or '날짜 없음'}"
        )
        return {
            "steam_history_low": formatted,
            "steam_history_low_amount": amount,
            "steam_history_low_currency": currency,
            "steam_history_low_cut": cut,
            "steam_history_low_date": date,
            "itad_game_url": f"https://isthereanydeal.com/game/id:{game_id}/",
            "steamdb_url": steamdb_url(url),
        }
    except Exception as exc:
        print(f"ITAD Steam 역대가 조회 실패: {shop_key} / {country} / {exc}")
        return {
            "steamdb_url": steamdb_url(url),
        }


def current_amount(price_text: str):
    if not price_text:
        return None
    m = re.search(r"(?:₩|￦|¥|￥|\$|€|£)\s*([0-9][0-9,.]*)", price_text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def current_currency(price_text: str):
    if not price_text:
        return ""
    if price_text.startswith(("¥", "￥")):
        return "JPY"
    if price_text.startswith(("₩", "￦")):
        return "KRW"
    if price_text.startswith("$"):
        return "USD"
    if price_text.startswith("€"):
        return "EUR"
    if price_text.startswith("£"):
        return "GBP"
    return ""


def is_current_history_low(item: dict):
    low_amount = item.get("steam_history_low_amount")
    low_currency = str(item.get("steam_history_low_currency") or "").upper()
    now_amount = current_amount(item.get("price") or "")
    now_currency = current_currency(item.get("price") or "")
    try:
        low_amount = float(low_amount)
    except (TypeError, ValueError):
        return False
    if now_amount is None or not low_currency or low_currency != now_currency:
        return False
    return now_amount <= low_amount + 0.005


def history_low_lines(item: dict, include_links=True):
    low = item.get("steam_history_low")
    lines = []
    if low:
        cut = int(item.get("steam_history_low_cut") or 0)
        date = item.get("steam_history_low_date") or ""
        discount = f" (-{cut}%)" if cut > 0 else ""
        date_text = f" · {date}" if date else ""
        lines.append(f"Steam 역대 최저가 (ITAD): **{low}{discount}**{date_text}")
        if is_current_history_low(item):
            lines.append("🔥 **현재가 = Steam 역대 최저가**")

    if include_links:
        links = []
        itad = item.get("itad_game_url") or ""
        steamdb = item.get("steamdb_url") or ""
        if itad:
            links.append(f"[ITAD]({itad})")
        if steamdb:
            links.append(f"[SteamDB]({steamdb})")
        if links:
            lines.append("📊 " + " · ".join(links))

    if not lines:
        return ""
    return "\n".join(lines) + "\n"
