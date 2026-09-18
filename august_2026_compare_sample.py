import re
import time
from datetime import date, datetime
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from calendar_snapshot import normalize_title_key

BASE = "https://series.naver.com"
SEARCH_URL = BASE + "/search/search.series"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

# 2026년 8월 알라딘 eBook 만화 캘린더 이미지에서 직접 옮긴 표본.
# 오른쪽 '세트' 열에 O가 찍힌 행은 제외했다.
# 날짜가 여러 권 범위(예: 6~7)인 경우 가장 높은 권수를 비교한다.
SAMPLE = [
    ("2026-08-05", "기프티드", 11),
    ("2026-08-05", "옆집은 뭐 먹지? 어른의 맛", 13),
    ("2026-08-06", "사정을 모르는 전학생이 거침없이 다가온다", 23),
    ("2026-08-07", "마도구사 달리아는 고개 숙이지 않아", 8),
    ("2026-08-10", "선생님! 저희들이 세계를 멸망시킵니다", 8),
    ("2026-08-10", "여성향 게임 세계는 엑스트라에게 엄격한 세계입니다", 10),
    ("2026-08-11", "이세계 전생 모험가", 8),
    ("2026-08-11", "미즈키 선배의 사랑점", 4),
    ("2026-08-12", "대전 감사합니다", 8),
    ("2026-08-12", "묵시록의 4기사", 24),
    ("2026-08-13", "마왕성에서 잘 자요", 31),
    ("2026-08-13", "푸른 오케스트라", 12),
    ("2026-08-14", "내 마음의 위험한 녀석", 13),
    ("2026-08-14", "더 화이팅 신장재편판", 55),
    ("2026-08-18", "고양이와 용", 5),
    ("2026-08-18", "BECK 신장판", 5),
    ("2026-08-19", "페어리 테일 100년 퀘스트", 22),
    ("2026-08-20", "가극 소녀", 16),
    ("2026-08-20", "마녀 이치", 5),
    ("2026-08-20", "마오", 26),
    ("2026-08-21", "슈퍼 뒤에서 담배 피우는 두 사람", 8),
    ("2026-08-21", "봄의 폭풍과 몬스터", 8),
    ("2026-08-25", "술과 사랑은 취해야 제맛", 10),
    ("2026-08-27", "명탐정 코난", 108),
    ("2026-08-27", "섀도 하우스", 20),
    ("2026-08-27", "푸른 상자", 24),
    ("2026-08-28", "공정 드래곤즈", 21),
    ("2026-08-28", "나츠메 우인장", 32),
    ("2026-08-31", "최애가 부하가 되었습니다", 7),
    ("2026-08-31", "검은 고양이와 마녀의 교실", 13),
    ("2026-08-31", "데드 어카운트", 13),
    ("2026-08-31", "텐도 가 이야기", 16),
]


def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


def set_base_title(title):
    title = re.sub(r"\[(?:코믹|비애)\]", " ", title, flags=re.I)
    title = re.sub(r"\[[^\]]*?\d+권\s*세트[^\]]*\]", " ", title)
    title = re.sub(r"\[[^\]]*세트[^\]]*\]", " ", title)
    title = re.sub(r"\(총\s*\d+권[^)]*\)", " ", title)
    return clean_text(title)


def similarity(a, b):
    a = normalize_title_key(a)
    b = normalize_title_key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def search_products(title):
    candidates = {}

    for page in (1, 2):
        r = requests.get(
            SEARCH_URL,
            params={"t": "comic", "q": title, "so": "date.dsc", "page": page},
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "detail.series" not in href or "productNo=" not in href:
                continue

            label = clean_text(a.get("title") or a.get_text(" ", strip=True))
            if not label:
                continue

            url = urljoin(BASE, href)
            product_no = (parse_qs(urlparse(url).query).get("productNo") or [None])[0]
            if not product_no:
                continue

            score = similarity(title, set_base_title(label))
            old = candidates.get(product_no)
            if old is None or score > old["score"]:
                candidates[product_no] = {
                    "score": score,
                    "productNo": product_no,
                    "label": label,
                    "url": url,
                    "is_set": "세트" in label,
                }

        time.sleep(0.12)

    # 일반 작품을 우선하되, 세트 상품도 후보로 남긴다.
    ranked = sorted(
        candidates.values(),
        key=lambda x: (x["score"], not x["is_set"]),
        reverse=True,
    )
    return [x for x in ranked if x["score"] >= 0.62][:8]


def get_volume_date(product_no, volume_no):
    detail_url = f"{BASE}/comic/detail.series?productNo={product_no}"
    r = requests.get(detail_url, headers=HEADERS, timeout=30)

    if "nid.naver.com" in r.url:
        return None, "연령확인 필요"

    m = re.search(r"sVolumeListUrl\s*:\s*['\"]([^'\"]+)", r.text)
    if not m:
        return None, "volumeList URL 없음"

    api_url = urljoin(BASE, m.group(1))
    vr = requests.get(
        api_url,
        headers={**HEADERS, "Referer": detail_url},
        timeout=30,
    )

    if vr.status_code != 200:
        return None, f"volumeList HTTP {vr.status_code}"

    try:
        rows = vr.json().get("resultData") or []
    except Exception:
        return None, "volumeList JSON 아님"

    row = next((x for x in rows if int(x.get("volumeNo") or 0) == int(volume_no)), None)
    if row is None:
        return None, f"{volume_no}권을 목록에서 못 찾음"

    raw = row.get("lastVolumeUpdateDate")
    if not raw:
        return None, f"{volume_no}권 날짜 없음"

    return datetime.fromisoformat(raw).date(), None


def main():
    results = []
    misses = []

    print(f"알라딘 이미지 표본: {len(SAMPLE)}개 (세트 O 제외)")
    print()

    for idx, (aladin_raw, title, volume) in enumerate(SAMPLE, 1):
        aladin_date = date.fromisoformat(aladin_raw)

        candidates = search_products(title)
        if not candidates:
            misses.append((title, volume, "네이버 작품 검색 실패"))
            print(f"[{idx:02}/{len(SAMPLE)}] 미매칭: {title} {volume}권")
            time.sleep(0.15)
            continue

        found = None
        naver_date = None
        errors = []

        for candidate in candidates:
            candidate_date, error = get_volume_date(candidate["productNo"], volume)
            if not error:
                found = candidate
                naver_date = candidate_date
                break
            errors.append(f"{candidate['label']}: {error}")

        if found is None:
            reason = " / ".join(errors[:3]) or "날짜 조회 실패"
            misses.append((title, volume, reason))
            print(f"[{idx:02}/{len(SAMPLE)}] 조회 실패: {title} {volume}권 / {reason}")
            time.sleep(0.15)
            continue

        diff = (naver_date - aladin_date).days
        results.append({
            "title": title,
            "volume": volume,
            "aladin": aladin_date,
            "naver": naver_date,
            "diff": diff,
            "match": found["label"],
            "score": found["score"],
        })

        print(
            f"[{idx:02}/{len(SAMPLE)}] {title} {volume}권 / "
            f"알라딘 {aladin_date} / 네이버 {naver_date} / {diff:+d}일"
        )
        time.sleep(0.15)

    print()
    print("========== 8월 표본 결과 ==========")

    if not results:
        print("비교 가능한 작품이 없습니다.")
        return

    n = len(results)

    for days, label in ((0, "같은 날"), (1, "±1일 이내"), (2, "±2일 이내"), (3, "±3일 이내")):
        count = sum(abs(x["diff"]) <= days for x in results)
        print(f"{label:<10}: {count:3}개 / {count/n*100:5.1f}%")

    diffs = sorted(abs(x["diff"]) for x in results)
    avg = sum(diffs) / n
    median = diffs[n // 2] if n % 2 else (diffs[n // 2 - 1] + diffs[n // 2]) / 2

    print(f"비교 가능      : {n}개 / 표본 {len(SAMPLE)}개")
    print(f"평균 날짜 차이 : {avg:.2f}일")
    print(f"중앙값 차이    : {median:.1f}일")

    changed = sorted((x for x in results if x["diff"] != 0), key=lambda x: abs(x["diff"]), reverse=True)
    print()
    print("--- 날짜가 다른 작품 ---")
    if not changed:
        print("없음")
    else:
        for x in changed:
            print(
                f"{x['title']} {x['volume']}권\n"
                f"  알라딘 {x['aladin']} / 네이버 {x['naver']} / {x['diff']:+d}일"
            )

    if misses:
        print()
        print("--- 미매칭/조회 실패 ---")
        for title, volume, reason in misses:
            print(f"{title} {volume}권 / {reason}")


if __name__ == "__main__":
    main()
