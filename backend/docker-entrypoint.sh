#!/bin/sh
set -eu

python -m alembic -c /app/alembic.ini upgrade head
exec "$@"
