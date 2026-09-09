# ruff: noqa: E402

import shutil
import sys
from pathlib import Path

from sqlmodel import Session, SQLModel

# Add src to sys.path
src_dir = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_dir))

from harness_lab_app.case_service import CaseService
from harness_lab_app.config import get_settings
from harness_lab_app.db import get_engine, init_db


def purge_run_disk() -> None:
    """Remove on-disk run state (data/runs/* and workspace/*).

    The DB reset alone leaves these dirs behind, so a re-created run with the
    same id would collide with a stale workspace (the old "set RESUME=1" trap).
    Opt-in only — this is destructive.
    """
    settings = get_settings()
    for base in (settings.data_runs_dir, settings.workspace_dir):
        if not base.exists():
            continue
        removed = 0
        for child in base.iterdir():
            if child.name in {".gitkeep", ".gitignore"}:
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        print(f"🧨 Purged {removed} entries from {base}")


def reset_database(purge_disk: bool = False) -> None:
    print("🧹 Resetting application database...")
    engine = get_engine()

    # Drop all tables managed by SQLModel
    print("🗑️ Dropping existing tables...")
    SQLModel.metadata.drop_all(engine)

    # Recreate tables and migrations
    print("🏗️ Creating clean database schema...")
    init_db()

    # Seed default data
    print("🌱 Seeding essential database data (Tetris case)...")
    with Session(engine) as session:
        CaseService(session).seed_benchmark_cases()

    if purge_disk:
        print("🧨 Purging on-disk run state (data/runs/*, workspace/*)...")
        purge_run_disk()

    print("✅ Database successfully reset and seeded!")


if __name__ == "__main__":
    reset_database(purge_disk="--purge-disk" in sys.argv[1:])
