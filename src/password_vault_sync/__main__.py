"""Command-line entry point: ``python -m password_vault_sync --task orgs|users|all``."""

from __future__ import annotations

import argparse
import logging
import sys

from password_vault_sync import sync_orgs, sync_users
from password_vault_sync.config import Config
from password_vault_sync.f3_api import F3Client
from password_vault_sync.vaultwarden import VaultwardenApiClient

logger = logging.getLogger("password_vault_sync")

TASKS = ("orgs", "users", "all")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="password-vault-sync",
        description=(
            "Syncs the F3 Nation API with Vaultwarden: region orgs become collections "
            "(orgs task) and password roles become collection membership (users task)."
        ),
    )
    parser.add_argument(
        "--task",
        choices=TASKS,
        default="all",
        help="What to sync when the job runs: 'orgs', 'users', or 'all' (orgs first, then users).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log every mutation that would happen without writing to Vaultwarden.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load a local .env if present; in Cloud Run everything comes from the environment.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv is a hard dependency
        pass

    try:
        config = Config.from_env(require_org_key=args.task in ("orgs", "all"))
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    logger.info(
        "Starting sync: task=%s dry_run=%s f3=%s vaultwarden=%s",
        args.task,
        args.dry_run,
        config.f3_api_url,
        config.vaultwarden_url,
    )

    failures = 0
    try:
        f3 = F3Client(config.f3_api_url, config.f3_api_key, config.f3_api_client)
        vaultwarden = VaultwardenApiClient(
            vw_url=config.vaultwarden_url,
            client_id=config.vaultwarden_client_id,
            client_secret=config.vaultwarden_client_secret,
            org_id=config.vaultwarden_org_id,
        )
        vaultwarden.login()

        if args.task in ("orgs", "all"):
            failures += sync_orgs.run(f3, vaultwarden, config.vaultwarden_org_key, dry_run=args.dry_run)
        if args.task in ("users", "all"):
            failures += sync_users.run(f3, vaultwarden, dry_run=args.dry_run)
    except Exception:
        logger.exception("Fatal error during sync")
        return 1

    if failures:
        logger.error("Sync finished with %d failure(s)", failures)
        return 1
    logger.info("Sync completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
