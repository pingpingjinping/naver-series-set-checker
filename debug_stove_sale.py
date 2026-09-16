import json
import re
import time
import uuid

import requests

PRODUCT_NO = 3894
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def stove_headers():
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


def walk(value, path="$", out=None):
    if out is None:
        out = []
    keywords = ("discount", "sale", "end", "date", "period", "event", "promo", "price")

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            key_l = str(key).lower()
            if any(k in key_l for k in keywords) and not isinstance(child, (dict, list)):
                out.append((child_path, child))
            walk(child, child_path, out)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            walk(child, f"{path}[{i}]", out)
    return out


def dump(label, obj):
    print(f"\n===== {label} =====")
    rows = walk(obj)
    seen = set()
    for path, value in rows:
        key = (path, repr(value))
        if key in seen:
            continue
        seen.add(key)
        text = repr(value)
        if len(text) > 300:
            text = text[:300] + "..."
        print(f"{path} = {text}")


def main():
    headers = stove_headers()

    r = requests.get(
        "https://api.onstove.com/store/v1.0/components",
        headers=headers,
        params={
            "search_type": "PAGE_ID",
            "search_value": "INDIE_PRODUCT",
            "product_no": str(PRODUCT_NO),
            "timestemp": str(int(time.time() * 1000)),
        },
        timeout=30,
    )
    r.raise_for_status()
    components_json = r.json()
    dump("/components interesting fields", components_json)

    component_ids = []
    stack = [components_json]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if value.get("component_id") and value.get("component_id") not in component_ids:
                component_ids.append(value["component_id"])
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)

    print("\ncomponent_ids:", ",".join(component_ids))

    for component_id in component_ids:
        try:
            r = requests.get(
                "https://api.onstove.com/store/v1.0/components/groups/product-merge",
                headers=headers,
                params={
                    "component_ids": component_id,
                    "product_no": str(PRODUCT_NO),
                    "preview": "",
                    "timestemp": str(int(time.time() * 1000)),
                },
                timeout=30,
            )
            r.raise_for_status()
            dump(f"product-merge {component_id}", r.json())
        except Exception as e:
            print(f"product-merge {component_id} ERROR: {e}")

    page = requests.get(
        f"https://store.onstove.com/ko/games/{PRODUCT_NO}",
        headers=HEADERS,
        timeout=30,
    )
    page.raise_for_status()
    html = page.text
    print("\n===== page snippets =====")
    for pattern in [r".{0,120}할인.{0,160}", r".{0,120}discount.{0,160}", r".{0,120}2026.{0,160}"]:
        for m in re.finditer(pattern, html, re.I | re.S):
            print(re.sub(r"\s+", " ", m.group(0))[:500])


if __name__ == "__main__":
    main()
