import os
import sys

MODE_FILE = "scheduler_mode.txt"


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
    force_run = truthy(env("FORCE_RUN"))

    if force_run:
        write_result(True, mode, "forced workflow_dispatch accepted")
        return 0

    if event_name == "workflow_dispatch":
        if mode == "pi":
            write_result(True, mode, "Pi workflow_dispatch accepted")
        else:
            write_result(False, mode, "workflow_dispatch ignored (mode=github; use force_run for scheduler/manual run)")
        return 0

    write_result(False, mode, f"unsupported event ignored: {event_name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
