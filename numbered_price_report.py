import json
import os
import time
from pathlib import Path

import requests

URLS_FILE = Path("price_urls.txt")
STATE_FILE = Path("price_state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
ACTIONS_URL = "https://github.com/pingpingjinping/naver-series-set-checker/actions/workflows/check.yml"


def read_urls():
    if not URLS_FILE.exists():
        return []
    out = []
    seen = set()
    for raw in URLS_FILE.read_text(encoding="utf-8").splitlines():
        url = raw.strip()
        if not url or url.startswith("#") or url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def load_items():
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        items = data.get("items", {})
        return items if isinstance(items, dict) else {}
    except Exception:
        return {}


def post_discord(content):
    if not DISCORD_WEBHOOK:
        raise RuntimeError("DISCORD_WEBHOOK secret이 설정되어 있지 않습니다.")
    r = requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=30)
    r.raise_for_status()


def main():
    urls = read_urls()
    items = load_items()

    lines = ["📋 **현재 가격 목록 (삭제 번호)**", ""]
    if not urls:
        lines.append("등록된 가격 추적 URL이 없습니다.")
    else:
        for i, url in enumerate(urls, 1):
            item = items.get(url)
            if item:
                title = item.get("title") or "가격 추적 상품"
                price = item.get("price") or "알 수 없음"
                region = item.get("region") or ""
                region_text = f" · {region}" if region else ""
                lines.append(f"**#{i} · {title}**")
                lines.append(f"현재: `{price}`{region_text}")
            else:
                lines.append(f"**#{i} · 조회 정보 없음**")
            lines.append(f"<{url}>")
            lines.append("")

    lines.append("삭제할 때 Actions의 `삭제할 번호들`에 예: `1,3` 입력")
    lines.append("")
    lines.append(f"⚙️ **URL 추가/삭제:** <{ACTIONS_URL}>")

    chunks = []
    current = ""
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

    print("번호가 포함된 현재 가격 목록을 Discord로 전송했습니다.")


if __name__ == "__main__":
    main()
