from __future__ import annotations

import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_from_repository_dotenv() -> None:
    """Keep tests independent from a developer's local REPOSITORY_ROOT/.env.

    Settings reads REPOSITORY_ROOT/.env regardless of the current working
    directory, so a local bootstrap file can leak non-secret defaults (for
    example IMPORT_ALLOW_OVERWRITE, path roots, contour) into assertions.
    Tests that need specific values still pass explicit keyword arguments.
    """
    if "env_file" not in Settings.model_config:
        return
    original = Settings.model_config["env_file"]
    Settings.model_config["env_file"] = None
    try:
        yield
    finally:
        Settings.model_config["env_file"] = original