#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monthly_release_report import parse_aladin, parse_ridi

KST = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT = Path.home() / ".local/state/naver-series-set-checker/release_calendar.json"


def clean_calendar_title(source: str, title: str) -> str:
    exclusive = False
    if source == "ridi" and "[RIDI ONLY]" in title:
        exclusive = True
    elif source == "aladin" and "[알라딘 독점]" in title:
        exclusive = True

    for label in (
        "⭐ [RIDI ONLY]",
        "⭐ [알라딘 독점]",
        "🆕 [신작]",
        "🆕 [신간]",
        "🔄 [후속권]",
        "✨ [신규 연재]",
    ):
        title = title.replace(label, "")

    title = " ".join(title.split()).strip()
    if not title:
        return ""

    if exclusive:
        return f"{'⭐' if source == 'ridi' else '🌟'} {title}"
    return title


def source_items(source: str, groups: dict) -> list[dict]:
    items = []
    for day in sorted(groups):
        for title in groups[day].get("titles", []):
            title = clean_calendar_title(source, title)
            if not title:
                continue
            items.append({
                "day": int(day),
                "source": source,
                "title": title,
            })
    return items


def build_snapshot(year: int, month: int) -> dict:
    result = {
        "year": year,
        "month": month,
        "generatedAt": datetime.now(KST).isoformat(),
        "items": [],
        "sources": {},
    }

    for source, label, parser in (
        ("ridi", "리디", parse_ridi),
        ("aladin", "알라딘", parse_aladin),
    ):
        try:
            groups, url = parser(year, month)
            items = source_items(source, groups)
            result["items"].extend(items)
            result["sources"][source] = {
                "label": label,
                "ok": True,
                "url": url,
                "count": len(items),
                "error": None,
            }
            print(f"{label}: {len(items)}개")
        except Exception as exc:
            result["sources"][source] = {
                "label": label,
                "ok": False,
                "url": None,
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(f"{label}: 실패 - {type(exc).__name__}: {exc}")

    result["items"].sort(key=lambda item: (item["day"], item["source"], item["title"]))
    result["total"] = len(result["items"])
    return result


def main() -> int:
    now = datetime.now(KST)
    parser = argparse.ArgumentParser(description="리디/알라딘 월간 출시 일정을 대시보드용 JSON으로 저장")
    parser.add_argument("--year", type=int, default=now.year)
    parser.add_argument("--month", type=int, default=now.month)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if not 1 <= args.month <= 12:
        parser.error("--month는 1~12여야 합니다.")

    snapshot = build_snapshot(args.year, args.month)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(args.output)

    print(f"저장: {args.output}")
    print(f"총 {snapshot['total']}개")
    return 0 if any(v.get("ok") for v in snapshot["sources"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
