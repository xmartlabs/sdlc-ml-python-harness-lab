from __future__ import annotations

import argparse
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Any, cast

from sqlalchemy import desc
from sqlmodel import Session, select

from harness_lab_app.db import engine, init_db
from harness_lab_app.models import Run, RunEvent, RunState
from harness_lab_app.run_service import RunService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harness Lab worker")
    parser.add_argument(
        "--once", action="store_true", help="Process a single pending run and exit"
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=2.0, help="Polling delay when looping"
    )
    return parser.parse_args(argv)


def _stream_lines(stream: Any, level: str, out: Queue[tuple[str, str]]) -> None:
    for line in iter(stream.readline, ""):
        out.put((level, line.rstrip("\n")))
    stream.close()


def _cancel_requested(session: Session, run_id: str) -> bool:
    session.expire_all()
    run = session.exec(select(Run).where(Run.run_id == run_id)).first()
    return bool(run and run.cancel_requested)


def _run_process_streaming(session: Session, run: Run) -> tuple[int, str, bool]:
    process = subprocess.Popen(
        run.command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    lines: Queue[tuple[str, str]] = Queue()
    stdout_thread = threading.Thread(
        target=_stream_lines, args=(process.stdout, "info", lines)
    )
    stderr_thread = threading.Thread(
        target=_stream_lines, args=(process.stderr, "error", lines)
    )
    stdout_thread.start()
    stderr_thread.start()

    stderr_tail: list[str] = []
    cancelled = False
    last_cancel_check = 0.0
    while stdout_thread.is_alive() or stderr_thread.is_alive() or not lines.empty():
        if not cancelled:
            now = time.monotonic()
            if now - last_cancel_check >= 0.5:
                last_cancel_check = now
                if _cancel_requested(session, run.run_id):
                    cancelled = True
                    process.terminate()
        try:
            level, message = lines.get(timeout=0.1)
        except Empty:
            continue
        if not message:
            continue
        session.add(RunEvent(run_id=run.run_id, level=level, message=message))
        session.commit()
        if level == "error":
            stderr_tail.append(message)

    stdout_thread.join()
    stderr_thread.join()
    return_code = process.wait()
    return return_code, "\n".join(stderr_tail[-20:]), cancelled


def run_pending_once(session: Session, execute_process: bool = True) -> int:
    run = session.exec(
        select(Run)
        .where(Run.state == RunState.pending)
        .order_by(desc(cast("Any", Run.created_at)))
    ).first()
    if run is None:
        return 0

    service = RunService(session)
    service.mark_running(run.run_id)

    if execute_process:
        return_code, stderr_tail, cancelled = _run_process_streaming(session, run)
        if cancelled:
            service.mark_cancelled(run.run_id, return_code)
        else:
            service.mark_finished(
                run.run_id,
                return_code,
                None if return_code == 0 else stderr_tail[-1000:],
            )
    else:
        session.add(
            RunEvent(run_id=run.run_id, message="Dry-run worker simulation completed")
        )
        session.commit()
        service.mark_finished(run.run_id, 0)

    return 1


def run_worker_loop(poll_seconds: float = 2.0) -> None:
    init_db()
    last_heartbeat = 0.0
    while True:
        now = time.monotonic()
        if now - last_heartbeat >= 60.0:  # Log heartbeat every minute
            heartbeat_msg = "Worker heartbeat: alive and polling"
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {heartbeat_msg}")
            with Session(engine) as session:
                session.add(
                    RunEvent(
                        run_id="worker",
                        level="info",
                        message=heartbeat_msg,
                    )
                )
                session.commit()
            last_heartbeat = now

        with Session(engine) as session:
            processed = run_pending_once(session, execute_process=True)
        if processed == 0:
            time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    init_db()
    if args.once:
        with Session(engine) as session:
            return 0 if run_pending_once(session, execute_process=True) else 1
    run_worker_loop(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
