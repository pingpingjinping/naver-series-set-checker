import os
import re
from pathlib import Path

URLS_FILE = Path("price_urls.txt")
ADD_URLS = os.environ.get("ADD_URLS", "").strip()
REMOVE_NUMBERS = os.environ.get("REMOVE_NUMBERS", "").strip()


def read_file():
    if not URLS_FILE.exists():
        return [], []

    comments = []
    urls = []
    seen = set()
    for raw in URLS_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
            continue
        if line not in seen:
            seen.add(line)
            urls.append(line)
    return comments, urls


def parse_add_urls(text):
    if not text:
        return []
    # 줄바꿈/공백/쉼표로 여러 URL을 한 번에 붙여넣을 수 있다.
    candidates = re.split(r"[\s,]+", text.strip())
    out = []
    seen = set()
    for value in candidates:
        url = value.strip()
        if not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError(f"URL 형식이 아닙니다: {url}")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def parse_remove_numbers(text, count):
    if not text:
        return []
    numbers = []
    for token in re.split(r"[\s,]+", text.strip()):
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"삭제 번호는 숫자만 입력하세요: {token}")
        number = int(token)
        if number < 1 or number > count:
            raise ValueError(f"삭제 번호가 범위를 벗어났습니다: {number} (현재 1~{count})")
        if number not in numbers:
            numbers.append(number)
    return numbers


def save(comments, urls):
    lines = comments[:]
    if lines and urls:
        lines.append("")
    lines.extend(urls)
    URLS_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main():
    comments, urls = read_file()
    original = list(urls)

    remove_numbers = parse_remove_numbers(REMOVE_NUMBERS, len(urls))
    if remove_numbers:
        remove_set = set(remove_numbers)
        removed = [(i, url) for i, url in enumerate(urls, 1) if i in remove_set]
        urls = [url for i, url in enumerate(urls, 1) if i not in remove_set]
        for i, url in removed:
            print(f"삭제: #{i} {url}")

    additions = parse_add_urls(ADD_URLS)
    for url in additions:
        if url in urls:
            print(f"중복이라 건너뜀: {url}")
            continue
        urls.append(url)
        print(f"추가: {url}")

    if urls != original:
        save(comments, urls)
    else:
        print("price_urls.txt 변경 없음")

    print("\n현재 가격 추적 목록:")
    if not urls:
        print("(비어 있음)")
    else:
        for i, url in enumerate(urls, 1):
            print(f"{i}. {url}")


if __name__ == "__main__":
    main()
