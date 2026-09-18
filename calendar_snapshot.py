#!/usr/bin/env python3
import argparse
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monthly_release_report import parse_aladin, parse_ridi

KST = ZoneInfo("Asia/Seoul")
STATE_DIR = Path.home() / ".local/state/naver-series-set-checker"
DEFAULT_OUTPUT = STATE_DIR / "release_calendar.json"


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


def normalize_title_key(title: str) -> str:
    # 중복 판정용 2차 정규화.
    # 같은 날짜 안에서만 사용하며, 실제로 다른 상품을 뭉치지 않도록
    # 전각/반각, 문장부호, 공백, 대표적인 "권수" 표기 차이만 흡수한다.
    title = title.lstrip("⭐🌟").strip()
    title = unicodedata.normalize("NFKC", title).casefold()

    # 끝의 권수 표기 통일: 제1권 / 1권 / vol.1 / volume 1 / (1) -> 1
    title = re.sub(r"\s*(?:제\s*)?(\d+)\s*권\s*$", r" \1", title)
    title = re.sub(r"\s*(?:vol(?:ume)?\.?\s*)(\d+)\s*$", r" \1", title, flags=re.I)
    title = re.sub(r"\s*[\(\[（]\s*(\d+)\s*[\)\]）]\s*$", r" \1", title)

    # 콜론/괄호/마침표/중점/하이픈류 등은 비교 시 공백으로 취급.
    # '~'는 합본 범위(예: 1~2)를 구분하기 위해 보존한다.
    title = re.sub(r"[\s\-‐‑‒–—―_:：·・,，.。!！?？'\"“”‘’()\[\]{}（）【】「」『』〈〉《》]+", " ", title)
    title = " ".join(title.split()).strip()
    return title


def source_items(source: str, groups: dict) -> list[dict]:
    items = []
    for day in sorted(groups):
        group = groups[day]
        records = group.get("items")
        if not records:
            records = [{"title": title, "author": ""} for title in group.get("titles", [])]

        for record in records:
            title = clean_calendar_title(source, record.get("title", ""))
            if not title:
                continue
            items.append({
                "day": int(day),
                "source": source,
                "title": title,
                "author": " ".join(str(record.get("author", "")).split()).strip(),
            })
    return items


def choose_author(current: str, incoming: str) -> str:
    current = " ".join((current or "").split()).strip()
    incoming = " ".join((incoming or "").split()).strip()
    if not current:
        return incoming
    if not incoming:
        return current
    if current.casefold() == incoming.casefold():
        return current

    current_vague = bool(re.search(r"외\s*\d+명", current))
    incoming_vague = bool(re.search(r"외\s*\d+명", incoming))
    if current_vague != incoming_vague:
        return incoming if current_vague else current

    # 양쪽 표기가 다르면 더 구체적인 쪽(보통 이름이 더 많이 적힌 쪽)을 사용.
    return incoming if len(incoming) > len(current) else current


def dedupe_items(items: list[dict]) -> list[dict]:
    merged = {}

    for item in items:
        title = item["title"]
        key = (int(item["day"]), normalize_title_key(title))
        entry = merged.get(key)

        if entry is None:
            merged[key] = {
                "day": int(item["day"]),
                "source": item["source"],
                "title": title,
                "author": item.get("author", ""),
                "sources": [item["source"]],
            }
            continue

        if item["source"] not in entry["sources"]:
            entry["sources"].append(item["source"])

        entry["author"] = choose_author(entry.get("author", ""), item.get("author", ""))

        # 양쪽 사이트에 모두 존재하면 독점 별표는 의미가 없으므로 제거.
        if len(entry["sources"]) > 1:
            entry["source"] = "both"
            entry["title"] = entry["title"].lstrip("⭐🌟").strip()

    result = list(merged.values())
    for item in result:
        item["sources"].sort()
    result.sort(key=lambda item: (item["day"], item["title"].casefold()))
    return result


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

    result["items"] = dedupe_items(result["items"])
    result["total"] = len(result["items"])
    return result


def write_snapshot(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def monthly_output_path(year: int, month: int) -> Path:
    return STATE_DIR / f"release_calendar_{year:04d}-{month:02d}.json"


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

    archive_path = monthly_output_path(args.year, args.month)
    write_snapshot(archive_path, snapshot)
    print(f"월별 저장: {archive_path}")

    # 기본 release_calendar.json은 현재 달을 가리키는 최신 별칭으로 유지한다.
    if args.year == now.year and args.month == now.month:
        write_snapshot(args.output, snapshot)
        print(f"현재 달 저장: {args.output}")

    print(f"총 {snapshot['total']}개")
    return 0 if any(v.get("ok") for v in snapshot["sources"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
