from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

_PROFILE_BOUNDARY_LOCK = RLock()


@contextmanager
def harbor_profile_boundary() -> Iterator[None]:
    """Serialize active-profile mutation with transfer operation creation.

    The v1 deployment runs one backend process against SQLite. Keep this barrier
    process-local, matching the runtime-mode serialization model.
    """

    with _PROFILE_BOUNDARY_LOCK:
        yield
