"""Start/stop a throw-away *real* PostgreSQL server without Docker (uses the `pgserver` wheel).

    python scripts/local_postgres.py start    # prints the admin URL (postgres superuser, trust auth)
    python scripts/local_postgres.py url
    python scripts/local_postgres.py stop

It is a development convenience for machines without Docker. The server listens on 127.0.0.1 only.
"""

from __future__ import annotations

import sys
from pathlib import Path

PGDATA = Path(__file__).resolve().parent.parent / ".pgdata"


def main() -> int:
    import pgserver

    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action in ("start", "url"):
        server = pgserver.get_server(PGDATA, cleanup_mode=None)  # keep running after this script exits
        print(server.get_uri())
        return 0
    if action == "stop":
        server = pgserver.get_server(PGDATA, cleanup_mode="stop")
        server.cleanup()
        print("stopped")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
