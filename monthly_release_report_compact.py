import time

import monthly_release_report as core


# Discord가 ▪/▫를 이모지 이미지로 바꾸지 않도록 text variation selector를 붙인다.
NORMAL_MARKER = "▫︎"
EXCLUSIVE_MARKER = "▪︎"


def compact_titles(titles):
    """카테고리 표시는 제거하고, 독점작을 먼저 모은 뒤 일반작을 표시한다."""
    exclusive_titles = []
    normal_titles = []

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
        line = f"{marker} {text}"
        target = exclusive_titles if exclusive else normal_titles

        if line not in target:
            target.append(line)

    return exclusive_titles + normal_titles


def send_report(site_name: str, emoji: str, year: int, month: int, groups, source_url: str):
    total = sum(len(v["titles"]) for v in groups.values())

    # 맨 위 제목은 책 아이콘, 날짜 구분은 작은 색 다이아를 사용한다.
    header_icon = "📘" if site_name == "리디" else "📙"
    site_icon = "🔹" if site_name == "리디" else "🔸"
    base_header = f"{header_icon} **{site_name} {year}년 {month}월 만화 출시 일정**\n총 **{total}개**"
    footer = f"\n📅 원본 캘린더: <{source_url}>"

    messages = []
    current = base_header + "\n"

    def start_new(context=""):
        nonlocal current
        if current.strip():
            messages.append(current.rstrip())
        # 월간 보고서 제목/총 개수는 첫 메시지에만 표시한다.
        current = context

    for day in sorted(groups):
        group = groups[day]
        # 날짜마다 작은 사이트 아이콘을 넣어 긴 보고서에서도 출처를 바로 구분한다.
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

    for message in messages:
        core.post_discord(message)
        time.sleep(0.45)

    print(f"{site_name}: {total}개 / Discord {len(messages)}개 메시지 전송 (날짜별 통합 목록, 독점 우선, 제목 1회)")


core.send_report = send_report
core.main()
