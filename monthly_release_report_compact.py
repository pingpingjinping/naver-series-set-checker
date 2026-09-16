import time

import monthly_release_report as core


# Discord가 ▪/▫를 이모지 이미지로 바꾸지 않도록 text variation selector를 붙인다.
NORMAL_MARKER = "▫︎"
EXCLUSIVE_MARKER = "▪︎"


def compact_titles(titles):
    """카테고리 표시는 모두 제거하고 독점 여부만 작은 네모로 구분한다."""
    result = []

    for raw in titles:
        text = raw
        exclusive = False

        if "⭐ [RIDI ONLY]" in text:
            exclusive = True
            text = text.replace("⭐ [RIDI ONLY]", "")
        if "⭐ [알라딘 독점]" in text:
            exclusive = True
            text = text.replace("⭐ [알라딘 독점]", "")

        # 신작/신간/신규 연재/후속권은 더 이상 카테고리로 나누지 않는다.
        for tag in ("🆕 [신작]", "🆕 [신간]", "✨ [신규 연재]", "🔄 [후속권]"):
            text = text.replace(tag, "")

        text = core.clean_markdown(text).strip()
        if not text:
            continue

        marker = EXCLUSIVE_MARKER if exclusive else NORMAL_MARKER
        text = f"{marker} {text}"

        if text not in result:
            result.append(text)

    return result


def send_report(site_name: str, emoji: str, year: int, month: int, groups, source_url: str):
    total = sum(len(v["titles"]) for v in groups.values())
    site_icon = "🔸" if site_name == "리디" else "🔹"
    base_header = f"{site_icon} **{site_name} {year}년 {month}월 만화 출시 일정**\n총 **{total}개**"
    footer = f"\n📅 원본 캘린더: <{source_url}>"

    messages = []
    current = base_header + "\n"

    def start_new(context=""):
        nonlocal current
        if current.strip() != base_header:
            messages.append(current.rstrip())
        current = base_header + "\n" + context

    for day in sorted(groups):
        group = groups[day]
        # 날짜마다 사이트 아이콘을 넣어 긴 보고서에서도 출처를 바로 구분한다.
        day_header = f"\n**{site_icon} {group['label']}**\n"

        if len(current) + len(day_header) + len(footer) > 1900:
            start_new(day_header)
        else:
            current += day_header

        for title in compact_titles(group["titles"]):
            line = f"{title}\n"
            if len(current) + len(line) + len(footer) > 1900:
                start_new(day_header + line)
            else:
                current += line

    current += footer
    messages.append(current.rstrip())

    for i, message in enumerate(messages, 1):
        if len(messages) > 1:
            message = message.replace(base_header, base_header + f" · {i}/{len(messages)}", 1)
        core.post_discord(message)
        time.sleep(0.45)

    print(f"{site_name}: {total}개 / Discord {len(messages)}개 메시지 전송 (날짜별 통합 목록)")


core.send_report = send_report
core.main()
