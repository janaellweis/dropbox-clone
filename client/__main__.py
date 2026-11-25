import argparse
import logging

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.runner import run


def main():
    parser = argparse.ArgumentParser(description="Dropbox Clone Sync Client")

    parser.add_argument(
        "--folder",
        required=True,
        help="Local folder to synchronize",
    )

    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8001",
        help="Backend API URL",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between sync attempts",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        with DropboxClient(args.server) as api:
            run(api, args.folder, args.interval)
    except SyncConflictError:
        logging.getLogger(__name__).error(
            "Synchronization stopped: manual intervention required"
        )


if __name__ == "__main__":
    main()
