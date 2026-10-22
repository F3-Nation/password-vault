"""Org sync: mirror F3 region orgs to Vaultwarden collections.

Rules (per the API contract):

* ``meta.password_vault_enabled === true``:
    * no collection with ``externalId == org.id`` → create it (name = org name, encrypted).
    * collection exists but its decrypted name differs from ``org.name`` → rename it.
* ``password_vault_enabled`` missing or falsy:
    * collection exists → remove every user's grant on it (the collection itself stays).
    * no collection → nothing to do.
* Collections whose ``externalId`` matches no region org (orphans) are only warned
  about; deleting collections is never part of the sync.
"""

from __future__ import annotations

import logging

from password_vault_sync import bitwarden_crypto as crypto
from password_vault_sync.f3_api import password_vault_enabled

logger = logging.getLogger(__name__)


def _decrypted_name(collection: dict, org_key: bytes) -> str | None:
    """Best-effort decryption of a stored collection name; ``None`` when undecryptable."""
    stored = collection.get("name")
    if not isinstance(stored, str):
        return None
    try:
        return crypto.decrypt_enc_string(stored, org_key).decode("utf-8")
    except crypto.CryptoError as exc:
        logger.warning(
            "Could not decrypt collection %s name (externalId=%s): %s",
            collection.get("id"),
            collection.get("externalId"),
            exc,
        )
        return None


def run(f3, vw, org_key: bytes | None, dry_run: bool = False) -> int:
    """Reconciles orgs → collections. Returns the number of failures."""
    if org_key is None:
        raise ValueError("VAULTWARDEN_ORG_KEY is required for the orgs task")

    orgs = f3.list_region_orgs()
    collections = vw.list_collection_details()

    failures = 0
    created = renamed = stripped = unchanged = 0
    enabled_count = 0

    by_external_id: dict[str, list[dict]] = {}
    for collection in collections:
        external_id = collection.get("externalId")
        if external_id in (None, "", "None"):
            logger.warning(
                "Collection %s has no externalId (manually created?); leaving it alone.",
                collection.get("id"),
            )
            continue
        by_external_id.setdefault(str(external_id), []).append(collection)

    org_ids = {str(org["id"]) for org in orgs}
    for external_id in sorted(by_external_id):
        if external_id not in org_ids:
            logger.warning(
                "ORPHAN collection %s (externalId=%s) does not match any region org; leaving it alone.",
                ", ".join(c["id"] for c in by_external_id[external_id]),
                external_id,
            )
        elif len(by_external_id[external_id]) > 1:
            failures += 1
            logger.error(
                "Duplicate externalId=%s on collections: %s",
                external_id,
                ", ".join(c["id"] for c in by_external_id[external_id]),
            )

    for org in sorted(orgs, key=lambda o: o["id"]):
        external_id = str(org["id"])
        org_name = org.get("name") or ""
        enabled = password_vault_enabled(org)
        if enabled:
            enabled_count += 1

        matches = by_external_id.get(external_id)
        if matches and len(matches) > 1:
            continue  # already counted as a failure above
        collection = matches[0] if matches else None

        try:
            if enabled:
                if collection is None:
                    encrypted_name = crypto.encrypt_string(org_name, org_key)
                    if dry_run:
                        logger.info("[dry-run] Would CREATE collection for org %s '%s'", external_id, org_name)
                    else:
                        created_collection = vw.create_collection(encrypted_name, external_id)
                        logger.info(
                            "CREATED collection %s for org %s '%s'",
                            created_collection.get("id"),
                            external_id,
                            org_name,
                        )
                    created += 1
                    continue

                current_name = _decrypted_name(collection, org_key)
                if current_name == org_name:
                    unchanged += 1
                    continue

                logger.info(
                    "Org %s name changed %r -> %r (collection %s)",
                    external_id,
                    current_name,
                    org_name,
                    collection["id"],
                )
                if dry_run:
                    logger.info("[dry-run] Would RENAME collection %s -> '%s'", collection["id"], org_name)
                else:
                    vw.update_collection(
                        collection["id"],
                        crypto.encrypt_string(org_name, org_key),
                        collection.get("externalId"),
                        users=collection.get("users") or [],
                        groups=collection.get("groups") or [],
                    )
                    logger.info("RENAMED collection %s -> '%s'", collection["id"], org_name)
                renamed += 1
            elif collection is not None:
                users = collection.get("users") or []
                if dry_run:
                    logger.info(
                        "[dry-run] Would STRIP %d user grant(s) from disabled collection %s (externalId=%s)",
                        len(users),
                        collection["id"],
                        external_id,
                    )
                else:
                    vw.update_collection(
                        collection["id"],
                        collection.get("name"),  # keep the stored (encrypted) name untouched
                        collection.get("externalId"),
                        users=[],
                        groups=collection.get("groups") or [],
                    )
                    logger.info(
                        "STRIPPED %d user grant(s) from disabled collection %s (externalId=%s); collection kept",
                        len(users),
                        collection["id"],
                        external_id,
                    )
                stripped += 1
        except Exception:
            failures += 1
            logger.exception("Failed to sync org %s '%s'", external_id, org_name)

    logger.info(
        "Org sync summary: %s orgs (%s enabled), created=%s renamed=%s stripped=%s unchanged=%s failures=%s%s",
        len(orgs),
        enabled_count,
        created,
        renamed,
        stripped,
        unchanged,
        failures,
        " [dry-run: counts are planned actions]" if dry_run else "",
    )
    return failures
