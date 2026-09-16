import price_watch as pw


# Discord 알림의 관리 링크는 현재 WalnutPi가 호출하는 전용 가격 워크플로로 보냅니다.
pw.ACTIONS_URL = "https://github.com/pingpingjinping/naver-series-set-checker/actions/workflows/price.yml"


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

    pw.post_discord(
        f"{pw.change_header(prev, current)}\n\n"
        f"**#{index} · {current['title']}**\n"
        f"{region_line}"
        f"이전: `{prev.get('price', '알 수 없음')}`\n"
        f"현재: `{current['price']}`\n"
        f"{sale_change_status_line(prev, current)}"
        f"{pw.base_discount_line(current)}"
        f"{discount_line}"
        f"<{url}>\n\n"
        f"⚙️ 가격 추적 관리: <{pw.ACTIONS_URL}>"
    )


pw.send_discord = send_discord

if __name__ == "__main__":
    pw.main()
