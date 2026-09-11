import argparse
import os

from app.auth.bootstrap import bootstrap_admin
from app.config import get_settings
from app.db.session import create_db_engine, create_session_factory


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the initial Harbor Transfer Portal admin")
    parser.add_argument("--username", default=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"))
    args = parser.parse_args()

    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    if not password:
        parser.error("BOOTSTRAP_ADMIN_PASSWORD must be set")

    settings = get_settings()
    session_factory = create_session_factory(create_db_engine(settings.database_url))
    with session_factory() as session:
        user, created = bootstrap_admin(session, username=args.username, password=password)

    if created:
        print(f"bootstrap admin created: {user.username}")
    else:
        print(f"bootstrap admin already exists: {user.username}; credentials unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
