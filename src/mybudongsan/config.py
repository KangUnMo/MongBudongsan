from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Local paths resolved without reading or storing credentials."""

    data_dir: Path
    db_url: str

    @classmethod
    def resolve(
        cls,
        *,
        data_dir: Path | None = None,
        db_url: str | None = None,
    ) -> Settings:
        resolved_data_dir = (
            data_dir
            or _environment_path("MYBUDONGSAN_DATA_DIR")
            or Path.cwd() / "data"
        ).expanduser().resolve()
        resolved_db_url = db_url or os.environ.get("MYBUDONGSAN_DB_URL")
        if resolved_db_url is None:
            resolved_db_url = f"sqlite+pysqlite:///{resolved_data_dir / 'mybudongsan.sqlite3'}"
        return cls(data_dir=resolved_data_dir, db_url=resolved_db_url)


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None
