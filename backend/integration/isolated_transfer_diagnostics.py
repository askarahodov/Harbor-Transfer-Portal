#!/usr/bin/env python3
"""Print persisted TARGET acceptance failure details without changing test outcome."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

work_root = Path(os.environ.get("HTP_ACCEPTANCE_WORK_ROOT", "/tmp/htp-isolated-target"))
database = work_root / "portal.db"

if not database.is_file():
    print(f"Acceptance diagnostics: database not found: {database}")
    raise SystemExit(0)

connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row
try:
    operations = connection.execute(
        "SELECT id, status, error_code, error_message FROM operations ORDER BY id"
    ).fetchall()
    for operation in operations:
        print(
            "Acceptance operation: "
            f"id={operation['id']} status={operation['status']} "
            f"error_code={operation['error_code']!r} "
            f"error_message={operation['error_message']!r}"
        )
        artifacts = connection.execute(
            "SELECT artifact_type, repository, name, reference, version, status, "
            "source_digest, target_digest, error_code, error_message "
            "FROM artifact_results WHERE operation_id = ? ORDER BY id",
            (operation["id"],),
        ).fetchall()
        for artifact in artifacts:
            print(
                "Acceptance artifact: "
                + " ".join(f"{key}={artifact[key]!r}" for key in artifact.keys())
            )
finally:
    connection.close()
