import sys
from pathlib import Path
from sqlmodel import Session, SQLModel

# Add src to sys.path
src_dir = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_dir))

from harness_lab_app.db import get_engine, init_db
from harness_lab_app.case_service import CaseService


def reset_database() -> None:
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
        CaseService(session).seed_tetris_case()

    print("✅ Database successfully reset and seeded!")


if __name__ == "__main__":
    reset_database()
