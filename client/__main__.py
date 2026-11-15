import argparse

from client.api import DropboxClient
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

    with DropboxClient(args.server) as api:
        run(api, args.folder, args.interval)


if __name__ == "__main__":
    main()
