#!/usr/bin/env python3
import argparse
import csv
import logging
import os
import sys

from dotenv import load_dotenv
from vaultwarden_api import STATUS_INVITED, STATUS_REVOKED, VaultwardenApiClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("add_members_to_collection")

# Accepted permission tokens -> read_only flag.
_READ_ONLY_TOKENS = {"ro", "r", "read", "readonly", "read-only"}
_READ_WRITE_TOKENS = {"rw", "w", "write", "readwrite", "read-write"}


def _parse_permission(token: str) -> bool:
    """Maps a permission token to a ``readOnly`` flag (True = read only)."""
    normalized = token.strip().lower()
    if normalized in _READ_ONLY_TOKENS:
        return True
    if normalized in _READ_WRITE_TOKENS:
        return False
    raise ValueError(
        f"Invalid permission '{token}'. Use one of: "
        f"{sorted(_READ_ONLY_TOKENS)} (read only) or {sorted(_READ_WRITE_TOKENS)} (read/write)."
    )


def read_members_csv(csv_path: str, default_read_only: bool) -> dict[str, bool]:
    """Reads a members CSV into {email: read_only}.

    The file must have a header row with an ``email`` column and an optional
    ``permission`` column (``ro``/``rw`` and common synonyms). Rows with a blank
    permission cell fall back to ``default_read_only``. Later rows for the same
    email win so a duplicate can override an earlier one.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV '{csv_path}' is empty (a header row is required).")

        field_map = {(name or "").strip().lower(): name for name in reader.fieldnames}
        if "email" not in field_map:
            raise ValueError(
                f"CSV '{csv_path}' must have an 'email' column. Found: {reader.fieldnames}."
            )
        email_field = field_map["email"]
        permission_field = field_map.get("permission")

        members: dict[str, bool] = {}
        for line_num, row in enumerate(reader, start=2):
            email = (row.get(email_field) or "").strip().lower()
            if not email:
                continue
            perm = (row.get(permission_field) or "").strip() if permission_field else ""
            try:
                read_only = _parse_permission(perm) if perm else default_read_only
            except ValueError as exc:
                raise ValueError(f"CSV '{csv_path}' line {line_num}: {exc}") from exc
            members[email] = read_only
    return members


def _collection_entry(collection_id: str, read_only: bool) -> dict:
    return {
        "id": collection_id,
        "readOnly": read_only,
        "hidePasswords": False,
        "manage": False,
    }


def resolve_collection_id(
    client: VaultwardenApiClient,
    *,
    uuid: str | None = None,
    external_id: str | None = None,
) -> str:
    """Resolves a collection to its canonical UUID from exactly one identifier.

    Provide either ``uuid`` (validated to exist in the org) or ``external_id``
    (for F3 regions this is the region's Postgres id, e.g. ``37004``), which is
    resolved to the collection UUID. Raises a clear error listing the available
    collections if the identifier does not match.
    """
    if (uuid is None) == (external_id is None):
        raise ValueError("Provide exactly one of 'uuid' or 'external_id'.")

    collections = client.list_collections()
    available = (
        ", ".join(f"{c.get('id')} (externalId={c.get('externalId')})" for c in collections)
        or "<none>"
    )

    if uuid is not None:
        uuid = uuid.strip()
        if any(str(c.get("id")) == uuid for c in collections):
            return uuid
        raise ValueError(
            f"No collection found with UUID '{uuid}'. Available collections: {available}"
        )

    external_id = external_id.strip()
    external_matches = [c for c in collections if str(c.get("externalId")) == external_id]
    if len(external_matches) == 1:
        resolved = str(external_matches[0]["id"])
        logger.info("Resolved externalId '%s' to collection %s", external_id, resolved)
        return resolved
    if len(external_matches) > 1:
        ids = ", ".join(str(c.get("id")) for c in external_matches)
        raise ValueError(
            f"Ambiguous externalId '{external_id}': multiple collections share it ({ids}). "
            f"Pass the collection UUID with --collection-uuid instead."
        )
    raise ValueError(
        f"No collection found with externalId '{external_id}'. Available collections: {available}"
    )


def _permission_label(read_only: bool) -> str:
    return "read-only" if read_only else "read/write"


def apply_members(
    client: VaultwardenApiClient,
    collection_id: str,
    desired: dict[str, bool],
) -> int:
    """Invites, grants, or updates members for a single collection.

    ``collection_id`` must be a resolved collection UUID.
    Returns the number of members that failed to process.
    """
    members_by_email = {}
    for member in client.list_members():
        email = member.get("email")
        if not isinstance(email, str) or not email.strip():
            logger.warning("Skipping malformed member record: %s", member.get("id"))
            continue
        members_by_email[email.strip().lower()] = member

    failures = 0
    for email, read_only in sorted(desired.items()):
        try:
            member = members_by_email.get(email)
            if member is None:
                client.invite_member(email, [_collection_entry(collection_id, read_only)])
                logger.info(
                    "INVITED '%s' with %s access to collection %s",
                    email,
                    _permission_label(read_only),
                    collection_id,
                )
                continue

            if member.get("accessAll"):
                logger.info(
                    "SKIP '%s': member has access to all collections; not modifying.", email
                )
                continue

            if member.get("status", STATUS_INVITED) == STATUS_REVOKED:
                logger.warning("SKIP '%s': member is revoked; not modifying.", email)
                continue

            collections = list(member.get("collections", []))
            existing = next((c for c in collections if c.get("id") == collection_id), None)

            if existing is None:
                collections.append(_collection_entry(collection_id, read_only))
                client.set_member_collections(
                    member["id"], member["type"], list(member.get("groups", [])), collections
                )
                logger.info(
                    "ADDED '%s' to collection %s (%s)",
                    email,
                    collection_id,
                    _permission_label(read_only),
                )
            elif bool(existing.get("readOnly")) != read_only:
                existing["readOnly"] = read_only
                client.set_member_collections(
                    member["id"], member["type"], list(member.get("groups", [])), collections
                )
                logger.info(
                    "UPDATED '%s' permission on collection %s -> %s",
                    email,
                    collection_id,
                    _permission_label(read_only),
                )
            else:
                logger.info(
                    "SKIP '%s': already has %s access to collection %s.",
                    email,
                    _permission_label(read_only),
                    collection_id,
                )
        except Exception:
            failures += 1
            logger.exception("Failed to process member '%s'", email)

    return failures


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Add members to a Vaultwarden collection. New users are invited to the "
            "organization; existing members are granted access or have their "
            "read-only/read-write permission updated."
        )
    )
    collection_group = parser.add_mutually_exclusive_group(required=True)
    collection_group.add_argument(
        "--collection-uuid",
        help="Target collection by its Vaultwarden UUID.",
    )
    collection_group.add_argument(
        "--collection-external-id",
        help="Target collection by its externalId (the region's Postgres id, e.g. 37004).",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=os.getenv("MEMBERS_CSV_PATH"),
        metavar="PATH",
        help=(
            "Path to a members CSV with a header row: an 'email' column and an "
            "optional 'permission' column ('ro'/'rw'). Rows with a blank permission "
            "use --default-permission. Defaults to the MEMBERS_CSV_PATH env var."
        ),
    )
    parser.add_argument(
        "--default-permission",
        choices=["ro", "rw"],
        default="ro",
        help="Permission used for CSV rows with a blank 'permission' cell (default: ro).",
    )
    parser.add_argument("--org-id", default=os.getenv("VAULTWARDEN_ORG_ID"), help="Organization ID")
    parser.add_argument("--vw-url", default=os.getenv("VAULTWARDEN_URL"), help="Vaultwarden base URL")
    parser.add_argument(
        "--client-id", default=os.getenv("VAULTWARDEN_CLIENT_ID"), help="API key client id"
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("VAULTWARDEN_CLIENT_SECRET"),
        help="API key client secret",
    )

    args = parser.parse_args()

    if not args.csv_path:
        parser.error("a members CSV is required via --csv or the MEMBERS_CSV_PATH env var")

    try:
        default_read_only = _parse_permission(args.default_permission)
        desired = read_members_csv(args.csv_path, default_read_only)

        if not desired:
            logger.info("No members found in %s; nothing to do.", args.csv_path)
            return

        client = VaultwardenApiClient(
            vw_url=args.vw_url,
            client_id=args.client_id,
            client_secret=args.client_secret,
            org_id=args.org_id,
        )
        client.login()
        collection_id = resolve_collection_id(
            client,
            uuid=args.collection_uuid,
            external_id=args.collection_external_id,
        )
        failures = apply_members(client, collection_id, desired)
    except Exception:
        logger.exception("Aborting: failed to add members to collection.")
        sys.exit(1)

    if failures:
        logger.error("%d member(s) failed to process.", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
