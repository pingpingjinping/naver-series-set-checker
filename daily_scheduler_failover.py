import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

MODE_FILE = "scheduler_mode.txt"
KST = ZoneInfo("Asia/Seoul")
WORKFLOWS = [
    ("Series", "series.yml"),
    ("Price", "price.yml"),
    ("Ridi", "ridi.yml"),
    ("Aladin", "aladin.yml"),
]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def read_mode() -> str:
    try:
        mode = open(MODE_FILE, "r", encoding="utf-8").read().strip()
    except OSError:
        mode = ""

    if mode not in {"pi", "github"}:
        print(f"scheduler_mode.txt 값이 잘못되어 안전하게 pi로 처리합니다: {mode!r}")
        return "pi"
    return mode


def github_api(method: str, path: str, payload=None):
    token = env("GH_TOKEN")
    repo = env("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GitHub API 환경변수가 없습니다")

    url = f"https://api.github.com/repos/{repo}/{path}"
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "walnutpi-daily-scheduler-failover",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def dispatch_seen_today(workflow_file: str) -> tuple[bool, str]:
    encoded = urllib.parse.quote(workflow_file, safe="")
    payload = github_api(
        "GET",
        f"actions/workflows/{encoded}/runs?event=workflow_dispatch&per_page=100",
    )

    now_kst = datetime.now(KST)
    start_kst = datetime.combine(now_kst.date(), time(6, 0), tzinfo=KST)
    start_utc = start_kst.astimezone(timezone.utc)

    for run in (payload or {}).get("workflow_runs", []):
        created = run.get("created_at")
        if not created:
            continue
        created_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if created_utc >= start_utc:
            return True, f"run {run.get('id')}"

    return False, "06:00 KST 이후 workflow_dispatch 없음"


def dispatch_workflow(workflow_file: str) -> None:
    encoded = urllib.parse.quote(workflow_file, safe="")
    ref = env("GITHUB_REF_NAME", "main") or "main"
    github_api(
        "POST",
        f"actions/workflows/{encoded}/dispatches",
        {
            "ref": ref,
            "inputs": {
                "force_run": True,
            },
        },
    )


def write_summary(lines: list[str]) -> None:
    path = env("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    mode = read_mode()
    now_kst = datetime.now(KST)
    summary = [
        "### Daily Scheduler / Failover",
        f"- mode: `{mode}`",
        f"- check time: `{now_kst:%Y-%m-%d %H:%M:%S} KST`",
        "",
        "| Workflow | Result |",
        "|---|---|",
    ]

    if mode == "github":
        failed = False
        for label, workflow_file in WORKFLOWS:
            try:
                dispatch_workflow(workflow_file)
                result = "GitHub mode → 실행 신호 전송"
            except Exception as exc:
                failed = True
                result = f"실행 신호 실패: {type(exc).__name__}: {exc}"
            print(f"{label}: {result}")
            summary.append(f"| {label} | {result} |")

        write_summary(summary)
        return 1 if failed else 0

    # Pi 모드에서는 06:10 신호가 올 시간을 조금 지난 뒤에만 자동 대체를 판단한다.
    if now_kst.time() < time(6, 10):
        result = "06:10 KST 이전이라 failover 확인 생략"
        print(result)
        summary.append(f"| - | {result} |")
        write_summary(summary)
        return 0

    failed = False
    for label, workflow_file in WORKFLOWS:
        try:
            seen, detail = dispatch_seen_today(workflow_file)
        except Exception as exc:
            # 확인 자체가 실패하면 하루 작업을 놓치는 것보다 대체 실행을 우선한다.
            seen = False
            detail = f"신호 확인 실패: {type(exc).__name__}: {exc}"

        if seen:
            result = f"Pi/기존 실행 신호 확인 → 생략 ({detail})"
        else:
            try:
                dispatch_workflow(workflow_file)
                result = f"신호 없음 → GitHub 자동 대체 실행 ({detail})"
            except Exception as exc:
                failed = True
                result = f"대체 실행 실패: {type(exc).__name__}: {exc}"

        print(f"{label}: {result}")
        summary.append(f"| {label} | {result} |")

    write_summary(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
