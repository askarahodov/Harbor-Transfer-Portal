#!/bin/sh
set -eu

if [ -z "${ALEMBIC_SKIP:-}" ]; then
    python -m alembic -c /app/alembic.ini upgrade head
fi
exec "$@"
