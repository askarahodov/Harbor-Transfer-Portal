#!/bin/sh
set -eu

# Persist a per-installation signing secret on the backend data volume.
# No secret is printed or placed into the repository.
if [ -z "${JWT_SECRET:-}" ] || [ "${JWT_SECRET}" = "replace-with-random-high-entropy-secret" ]; then
    umask 077
    mkdir -p /app/data/secrets
    python - <<'PY'
import os
from pathlib import Path
from secrets import token_urlsafe

path = Path("/app/data/secrets/jwt-secret")
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    pass
else:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(token_urlsafe(48) + "\n")
if len(path.read_text(encoding="utf-8").strip()) < 32:
    raise SystemExit("JWT secret file is missing or invalid")
PY
    JWT_SECRET="$(cat /app/data/secrets/jwt-secret)"
    export JWT_SECRET
fi

if [ -z "${ALEMBIC_SKIP:-}" ]; then
    python -m alembic -c /app/alembic.ini upgrade head
fi
exec "$@"
