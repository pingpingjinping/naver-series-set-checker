import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

MODE_FILE = "scheduler_mode.txt"
GITHUB_PRIMARY_CRON = "10 21 * * *"  # 06:10 KST
PI_FAILOVER_CRON = "30 21 * * *"    # 06:30 KST
KST = ZoneInfo("Asia/Seoul")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}


def read_mode() -> str:
    try:
        mode = open(MODE_FILE, "r", encoding="utf-8").read().strip()
    except OSError:
        mode = ""

    if mode not in {"pi", "github"}:
        print(f"scheduler_mode.txt 값이 잘못되어 안전하게 pi로 처리합니다: {mode!r}")
        return "pi"
    return mode


def pi_dispatch_seen_today(workflow_file: str) -> tuple[bool, str]:
    token = env("GH_TOKEN")
    repo = env("GITHUB_REPOSITORY")
    if not token or not repo or not workflow_file:
        raise RuntimeError("GitHub API 확인에 필요한 환경변수가 없습니다")

    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{workflow_file}/runs?event=workflow_dispatch&per_page=100"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "walnutpi-scheduler-failover",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)

    now_kst = datetime.now(KST)
    start_kst = datetime.combine(now_kst.date(), time(6, 0), tzinfo=KST)
    start_utc = start_kst.astimezone(timezone.utc)

    for run in payload.get("workflow_runs", []):
        created = run.get("created_at")
        if not created:
            continue
        created_utc = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if created_utc >= start_utc:
            return True, f"Pi/workflow_dispatch signal found (run {run.get('id')})"

    return False, "no workflow_dispatch signal since 06:00 KST"


def write_result(run: bool, mode: str, reason: str) -> None:
    output_path = env("GITHUB_OUTPUT")
    summary_path = env("GITHUB_STEP_SUMMARY")

    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")
            f.write(f"mode={mode}\n")

    print(reason)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(f"### Scheduler: {reason}\n")


def main() -> int:
    mode = read_mode()
    event_name = env("EVENT_NAME")
    event_schedule = env("EVENT_SCHEDULE")
    force_run = truthy(env("FORCE_RUN"))
    workflow_file = env("WORKFLOW_FILE")

    if force_run:
        write_result(True, mode, "manual force_run")
        return 0

    if event_name == "workflow_dispatch":
        if mode == "pi":
            write_result(True, mode, "Pi workflow_dispatch accepted")
        else:
            write_result(False, mode, "Pi/manual workflow_dispatch ignored (mode=github)")
        return 0

    if event_name == "schedule":
        if event_schedule == GITHUB_PRIMARY_CRON:
            if mode == "github":
                write_result(True, mode, "GitHub primary schedule accepted (mode=github)")
            else:
                write_result(False, mode, "GitHub primary schedule ignored (mode=pi)")
            return 0

        if event_schedule == PI_FAILOVER_CRON:
            if mode == "github":
                write_result(False, mode, "Pi failover check ignored (mode=github)")
                return 0

            try:
                seen, detail = pi_dispatch_seen_today(workflow_file)
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError, RuntimeError) as exc:
                # 확인 자체가 실패하면 하루 작업을 놓치는 것보다 백업 실행을 우선한다.
                write_result(True, mode, f"Pi signal check failed; automatic GitHub failover runs: {exc}")
                return 0

            if seen:
                write_result(False, mode, f"Pi signal received today; failover skipped: {detail}")
            else:
                write_result(True, mode, f"Pi signal missing today; automatic GitHub failover runs: {detail}")
            return 0

        write_result(False, mode, f"unknown schedule ignored: {event_schedule!r}")
        return 0

    write_result(False, mode, f"unsupported event ignored: {event_name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
