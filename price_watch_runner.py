import re

import price_watch as pw
import itad_history as ih


# Discord 알림의 관리 링크는 현재 WalnutPi가 호출하는 전용 가격 워크플로로 보냅니다.
pw.ACTIONS_URL = "https://github.com/pingpingjinping/naver-series-set-checker/actions/workflows/price.yml"


RELEASE_LABELS = {
    "UNRELEASED": "미출시",
    "PREORDER": "예약구매",
    "RELEASED": "정식 출시",
}


def _steam_price_result(info: dict, appid: str, cc: str, lang: str, symbol: str, region: str, currency: str):
    po = info.get("price_overview")
    if not po or po.get("currency") != currency:
        return None

    price = f"{symbol}{pw.steam_major_price(po):,}"
    try:
        discount = int(po.get("discount_percent") or 0)
    except (TypeError, ValueError):
        discount = 0
    if discount > 0:
        price += f" (-{discount}%)"

    discount_end = pw.steam_discount_end("appid", appid, cc, lang, region) if discount > 0 else ""
    return pw.result(
        info.get("name") or f"Steam App {appid}",
        price,
        region,
        discount_end=discount_end,
        sale_active=discount > 0,
        discount_percent=discount,
    )


def parse_steam_app_with_release(url: str, appid: str):
    """Steam App을 미출시/예약구매/정식출시 3상태로 판정한다.

    - coming_soon=true + 판매가 없음 -> UNRELEASED
    - coming_soon=true + 판매가 있음 -> PREORDER
    - coming_soon=false -> RELEASED

    한국 가격을 우선하고, 기존 동작처럼 한국 판매가가 없으면 일본을 확인한다.
    """
    unreleased_candidate = None

    for cc, lang, symbol, region, currency in [
        ("kr", "koreana", "₩", "한국", "KRW"),
        ("jp", "japanese", "¥", "일본", "JPY"),
    ]:
        info = pw.steam_appdetails(appid, cc, lang)
        if not info:
            continue

        title = info.get("name") or f"Steam App {appid}"
        release = info.get("release_date") or {}
        coming_soon = bool(release.get("coming_soon"))
        release_date = pw.clean_text(str(release.get("date") or ""))
        priced = _steam_price_result(info, appid, cc, lang, symbol, region, currency)

        if coming_soon:
            if priced:
                # 가격 문자열 자체에도 예구 상태를 남겨 기존 main()의 가격 변경 감지로
                # PREORDER -> RELEASED 전환까지 확실히 알림을 발생시킨다.
                priced["price"] += " (예구)"
                priced["release_status"] = "PREORDER"
                priced["release_date"] = release_date
                return priced

            if unreleased_candidate is None:
                unreleased_candidate = pw.result(
                    title,
                    "미출시",
                    region,
                    sale_active=None,
                )
                unreleased_candidate["release_status"] = "UNRELEASED"
                unreleased_candidate["release_date"] = release_date
            continue

        if info.get("is_free"):
            item = pw.result(title, f"{symbol}0", region, sale_active=False)
            item["release_status"] = "RELEASED"
            item["release_date"] = release_date
            return item

        if priced:
            priced["release_status"] = "RELEASED"
            priced["release_date"] = release_date
            return priced

    if unreleased_candidate is not None:
        return unreleased_candidate

    # 지역락/예외 케이스는 기존 파서의 오류 메시지와 동작을 유지한다.
    return pw.parse_steam_app(url, appid)


# 기존 가격 파서는 그대로 두고 Steam App에만 출시 상태 판정을 추가한다.
# 출시된 Steam 항목에는 기존 ITAD 역대 최저가 연동을 그대로 유지한다.
_original_parse_page = pw.parse_page


def parse_page_with_history(url: str):
    host = pw.urlparse(url).netloc.lower()
    app_match = pw.STEAM_APP_RE.search(url) if host.endswith("store.steampowered.com") else None

    if app_match:
        item = parse_steam_app_with_release(url, app_match.group(1))
    else:
        item = _original_parse_page(url)

    release_status = item.get("release_status")
    if release_status in (None, "RELEASED"):
        info = ih.fetch_steam_history_low(url, item.get("region", ""))
        if info:
            item.update(info)
    elif app_match:
        # 미출시/예구 중에도 SteamDB 링크는 상태 파일에 남겨 둔다.
        item["steamdb_url"] = ih.steamdb_url(url)

    return item


pw.parse_page = parse_page_with_history


def sale_change_status_line(prev: dict, current: dict):
    old_sale = prev.get("sale_active") if "sale_active" in prev else None
    new_sale = current.get("sale_active")

    if old_sale is False and new_sale is True:
        return "🟢 세일 상태: **시작**\n"
    if old_sale is True and new_sale is False:
        return "🔴 세일 상태: **종료**\n"
    if new_sale is True:
        return "🟡 세일 상태: **진행 중**\n"
    if new_sale is False:
        return "⚪ 세일 상태: **세일 아님**\n"
    return ""


def release_status_line(prev: dict, current: dict):
    status = current.get("release_status")
    if not status:
        return ""

    label = RELEASE_LABELS.get(status, status)
    old = prev.get("release_status") if "release_status" in prev else None
    if old and old != status:
        old_label = RELEASE_LABELS.get(old, old)
        return f"출시 상태: **{old_label} → {label}**\n"
    return f"출시 상태: **{label}**\n"


def release_date_line(current: dict):
    value = current.get("release_date") or ""
    if not value:
        return ""
    if current.get("release_status") == "RELEASED":
        return f"출시일: **{value}**\n"
    return f"출시 예정: **{value}**\n"


def change_header(prev: dict, current: dict):
    if "release_status" in prev:
        old = prev.get("release_status")
        new = current.get("release_status")
        if old != new:
            if new == "PREORDER":
                return "🛒 **Steam 예약구매 시작**"
            if new == "RELEASED":
                return "🎮 **Steam 정식 출시**"
            if new == "UNRELEASED":
                return "🕒 **Steam 출시 상태 변경**"

    return pw.change_header(prev, current)


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

    history_line = ih.history_low_lines(current, include_links=False)

    pw.post_discord(
        f"{change_header(prev, current)}\n\n"
        f"**#{index} · {current['title']}**\n"
        f"{region_line}"
        f"{release_status_line(prev, current)}"
        f"{release_date_line(current)}"
        f"이전: `{prev.get('price', '알 수 없음')}`\n"
        f"현재: `{current['price']}`\n"
        f"{sale_change_status_line(prev, current)}"
        f"{pw.base_discount_line(current)}"
        f"{discount_line}"
        f"{history_line}"
        f"<{url}>\n\n"
        f"⚙️ 가격 추적 관리: <{pw.ACTIONS_URL}>"
    )


pw.send_discord = send_discord

if __name__ == "__main__":
    pw.main()
