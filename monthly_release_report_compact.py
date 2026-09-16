import time

import monthly_release_report as core


# Discord가 ▪/▫를 이모지 이미지로 바꾸지 않도록 text variation selector를 붙인다.
NORMAL_MARKER = "▫︎"
EXCLUSIVE_MARKER = "▪︎"


def compact_sections(site_name: str, titles):
    if site_name == "리디":
        exclusive_bucket = "리디 독점"
        order = ["🆕 신작", "🔄 후속권", exclusive_bucket, "기타"]
    else:
        exclusive_bucket = "알라딘 독점"
        order = ["🆕 신간", "🔄 후속권", exclusive_bucket, "기타"]

    buckets = {name: [] for name in order}

    for raw in titles:
        text = raw
        exclusive = False
        bucket = "기타"

        if "⭐ [RIDI ONLY]" in text:
            exclusive = True
            text = text.replace("⭐ [RIDI ONLY]", "")
        if "⭐ [알라딘 독점]" in text:
            exclusive = True
            text = text.replace("⭐ [알라딘 독점]", "")

        if "🆕 [신작]" in text:
            bucket = "🆕 신작"
            text = text.replace("🆕 [신작]", "")
        elif "🆕 [신간]" in text:
            bucket = "🆕 신간"
            text = text.replace("🆕 [신간]", "")
        elif "✨ [신규 연재]" in text:
            # 알라딘 신규 연재도 별도 구분 없이 신간으로 합친다.
            bucket = "🆕 신간"
            text = text.replace("✨ [신규 연재]", "")
        elif "🔄 [후속권]" in text:
            bucket = "🔄 후속권"
            text = text.replace("🔄 [후속권]", "")
        elif exclusive:
            bucket = exclusive_bucket

        text = core.clean_markdown(text).strip()
        if not text:
            continue

        # 기존 불릿 대신 작은 네모 하나로 독점 여부를 표시한다.
        marker = EXCLUSIVE_MARKER if exclusive else NORMAL_MARKER
        text = f"{marker} {text}"

        if text not in buckets[bucket]:
            buckets[bucket].append(text)

    return [(name, buckets[name]) for name in order if buckets[name]]


def send_report(site_name: str, emoji: str, year: int, month: int, groups, source_url: str):
    total = sum(len(v["titles"]) for v in groups.values())
    base_header = f"{emoji} **{site_name} {year}년 {month}월 만화 출시 일정**\n총 **{total}개**"
    footer = f"\n📅 원본 캘린더: <{source_url}>"
    site_icon = "📘" if site_name == "리디" else "📙"

    messages = []
    current = base_header + "\n"

    def start_new(context=""):
        nonlocal current
        if current.strip() != base_header:
            messages.append(current.rstrip())
        current = base_header + "\n" + context

    for day in sorted(groups):
        group = groups[day]
        # 긴 보고서를 아래로 내려가며 읽어도 어느 사이트인지 바로 알 수 있게 날짜마다 표시한다.
        day_header = f"\n**{site_icon} {group['label']}**\n"

        if len(current) + len(day_header) + len(footer) > 1900:
            start_new(day_header)
        else:
            current += day_header

        for section_name, titles in compact_sections(site_name, group["titles"]):
            section_header = f"\n{section_name}\n"
            context = day_header + section_header

            if len(current) + len(section_header) + len(footer) > 1900:
                start_new(context)
            else:
                current += section_header

            for title in titles:
                line = f"{title}\n"
                if len(current) + len(line) + len(footer) > 1900:
                    start_new(context + line)
                else:
                    current += line

    current += footer
    messages.append(current.rstrip())

    for i, message in enumerate(messages, 1):
        if len(messages) > 1:
            message = message.replace(base_header, base_header + f" · {i}/{len(messages)}", 1)
        core.post_discord(message)
        time.sleep(0.45)

    print(f"{site_name}: {total}개 / Discord {len(messages)}개 메시지 전송 (카테고리 묶음)")


core.send_report = send_report
core.main()
