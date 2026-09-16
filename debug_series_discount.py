import re
import requests
from bs4 import BeautifulSoup

PRODUCTS = [14726944, 14719914]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

KEYWORDS = [
    "할인", "세트", "가격", "구매", "소장", "대여", "기간", "종료", "쿠키",
    "discount", "sale", "price", "end", "expire", "period",
]


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def dump_page(label, url):
    print(f"\n===== {label} =====")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        print("status:", r.status_code)
        print("final_url:", r.url)
        print("content_type:", r.headers.get("content-type"))
        print("bytes:", len(r.content))
        r.raise_for_status()
    except Exception as e:
        print("ERROR:", repr(e))
        return

    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    print("title:", clean(soup.title.get_text(" ", strip=True)) if soup.title else "")

    text = clean(soup.get_text(" ", strip=True))
    print("visible_snippets:")
    seen = set()
    for kw in KEYWORDS:
        for m in re.finditer(re.escape(kw), text, re.I):
            snippet = text[max(0, m.start()-120):m.end()+220]
            snippet = clean(snippet)
            if snippet and snippet not in seen:
                seen.add(snippet)
                print(" -", snippet[:500])
            if len(seen) >= 25:
                break
        if len(seen) >= 25:
            break

    print("html_snippets:")
    seen = set()
    patterns = [
        r".{0,140}할인.{0,240}",
        r".{0,140}discount.{0,240}",
        r".{0,140}(?:sale|price|period|expire|end).{0,240}",
        r".{0,140}20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}.{0,220}",
        r".{0,140}\b1[67-9]\d{8,12}\b.{0,220}",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, html, re.I | re.S):
            snippet = clean(m.group(0))
            if snippet and snippet not in seen:
                seen.add(snippet)
                print(" -", snippet[:600])
            if len(seen) >= 40:
                break
        if len(seen) >= 40:
            break


for product_no in PRODUCTS:
    print("\n\n########################################")
    print("PRODUCT", product_no)
    print("########################################")
    dump_page(
        f"desktop {product_no}",
        f"https://series.naver.com/comic/detail.series?productNo={product_no}",
    )
    dump_page(
        f"mobile {product_no}",
        f"https://m.series.naver.com/comic/detail.series?productNo={product_no}",
    )
