"""User sync: mirror F3 password roles to Vaultwarden collection membership.

Rules (per the API contract):

* ``password_manager`` → read/write on the collection for that role's ``orgId``.
* ``password_reader``  → read-only. Both roles for one org → read/write.
* Roles for orgs whose password vault is disabled (or that have no collection yet)
  are skipped, so this task can never re-grant access that the org sync just removed.
* A user with no relevant roles — and Vaultwarden members that the API doesn't list
  at all — stay members of the organization but end up with **no** collection grants.
* Members with ``accessAll`` (owners/admins) are never modified; Vaultwarden ignores
  per-collection rows for them anyway.
"""

from __future__ import annotations

import logging

from password_vault_sync.f3_api import PASSWORD_ROLES, password_vault_enabled
from password_vault_sync.vaultwarden import STATUS_REVOKED, collection_entry

logger = logging.getLogger(__name__)

MANAGER_ROLE = "password_manager"


def _desired_grants(
    api_users: list, enabled_org_ids: set[str], collections_by_external_id: dict
) -> dict[str, dict[str, bool]]:
    """Builds ``email -> {collection_id: read_only}`` from the API's role assignments."""
    desired: dict[str, dict[str, bool]] = {}
    for user in api_users:
        email = (user.get("email") or "").strip().lower()
        if not email:
            logger.warning("Skipping API user without an email: %r", user.get("id"))
            continue
        grants = desired.setdefault(email, {})
        for role in user.get("roles") or []:
            role_name = role.get("roleName")
            if role_name not in PASSWORD_ROLES:
                continue  # e.g. plain 'user', 'editor', 'admin' — not our concern
            external_id = str(role.get("orgId"))
            if external_id not in enabled_org_ids:
                logger.info(
                    "SKIP %s: role '%s' on org %s has no enabled vault (disabled or unknown)",
                    email,
                    role_name,
                    external_id,
                )
                continue
            collection = collections_by_external_id.get(external_id)
            if collection is None:
                logger.warning(
                    "SKIP %s: org %s is enabled but has no collection yet (waiting on the daily org sync)",
                    email,
                    external_id,
                )
                continue
            collection_id = collection["id"]
            if role_name == MANAGER_ROLE:
                grants[collection_id] = False  # read/write wins over read-only
            else:
                grants.setdefault(collection_id, True)
    return desired


def _member_collections_map(member: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in member.get("collections") or [] if entry.get("id")}


def _collection_side_access(collections: list) -> dict[str, dict[str, dict]]:
    """``member_id -> {collection_id: grant_entry}`` from the collection records.

    The member record's ``collections`` array only lists grants for confirmed
    (status 2) members; Vaultwarden keeps invited members' grants on the
    collection side alone. Reading only the member record therefore makes every
    run re-send grants that are already stored. Entries are rewritten to carry
    their collection id so they can be merged with the member-record view.
    """
    access: dict[str, dict[str, dict]] = {}
    for collection in collections:
        collection_id = collection.get("id")
        if not collection_id:
            continue
        for user in collection.get("users") or []:
            member_id = user.get("id")
            if not member_id:
                continue
            entry = dict(user)
            entry["id"] = str(collection_id)
            access.setdefault(str(member_id), {})[str(collection_id)] = entry
    return access


def _reconcile_member(
    member: dict, want: dict[str, bool], side_access: dict[str, dict] | None = None
) -> tuple[bool, list | None]:
    """Returns ``(changed, collections_payload)`` for a member's desired grants.

    ``side_access`` is the collection-side view of this member's grants (see
    :func:`_collection_side_access`); the two views are merged so invited
    members, whose record omits collections, compare against what they actually
    have. Where both views disagree the member record wins (it carries the
    freshest flags for confirmed members).
    """
    current = dict(side_access or {})
    current.update(_member_collections_map(member))
    current_access = {collection_id: bool(entry.get("readOnly")) for collection_id, entry in current.items()}
    if current_access == want:
        return False, None

    payload = []
    for collection_id in sorted(want):
        read_only = want[collection_id]
        previous = current.get(collection_id)
        if previous is not None:
            entry = dict(previous)
            entry["readOnly"] = read_only
        else:
            entry = collection_entry(collection_id, read_only)
        payload.append(entry)
    return True, payload


def run(f3, vw, dry_run: bool = False) -> int:
    """Reconciles API roles → Vaultwarden member collection grants. Returns failures."""
    api_users = f3.list_password_role_users()
    orgs = f3.list_region_orgs()
    enabled_org_ids = {str(org["id"]) for org in orgs if password_vault_enabled(org)}

    collections = vw.list_collection_details()
    collections_by_external_id = {
        str(collection["externalId"]): collection
        for collection in collections
        if collection.get("externalId") not in (None, "", "None")
    }
    side_access = _collection_side_access(collections)

    desired = _desired_grants(api_users, enabled_org_ids, collections_by_external_id)

    members = vw.list_members()
    members_by_email: dict[str, dict] = {}
    for member in members:
        email = (member.get("email") or "").strip().lower()
        if email:
            members_by_email[email] = member
        else:
            logger.warning("Skipping malformed member record: %s", member.get("id"))

    failures = 0
    invited = updated = unchanged = stripped = skipped = 0

    for email in sorted(set(desired) | set(members_by_email)):
        want = desired.get(email, {})
        member = members_by_email.get(email)

        try:
            if member is None:
                if not want:
                    continue  # not in Vaultwarden and no access to grant — nothing to do
                entries = [collection_entry(cid, want[cid]) for cid in sorted(want)]
                if dry_run:
                    logger.info(
                        "[dry-run] Would INVITE '%s' with access to %d collection(s)",
                        email,
                        len(entries),
                    )
                else:
                    vw.invite_member(email, entries)
                    logger.info("INVITED '%s' with access to %d collection(s)", email, len(entries))
                invited += 1
                continue

            if member.get("accessAll"):
                skipped += 1
                logger.debug("SKIP '%s': member has access to all collections (owner/admin).", email)
                continue

            if member.get("status") == STATUS_REVOKED:
                skipped += 1
                logger.warning("SKIP '%s': member is revoked; not modifying.", email)
                continue

            changed, payload = _reconcile_member(member, want, side_access.get(str(member["id"])))
            if not changed or payload is None:
                unchanged += 1
                continue

            if not want:
                logger.info("REVOKING all collection access for '%s' (no password roles in the API)", email)
            if dry_run:
                logger.info(
                    "[dry-run] Would UPDATE '%s': collections -> %s",
                    email,
                    [(entry["id"], "ro" if entry["readOnly"] else "rw") for entry in payload],
                )
            else:
                vw.set_member_collections(
                    member["id"], member.get("type") or 2, list(member.get("groups") or []), payload
                )
                logger.info(
                    "UPDATED '%s': collections -> %s",
                    email,
                    [(entry["id"], "ro" if entry["readOnly"] else "rw") for entry in payload],
                )
            if want:
                updated += 1
            else:
                stripped += 1
        except Exception:
            failures += 1
            logger.exception("Failed to process member '%s'", email)

    logger.info(
        "User sync summary: %s API users, %s members, invited=%s updated=%s revoked=%s unchanged=%s "
        "skipped=%s failures=%s%s",
        len(api_users),
        len(members),
        invited,
        updated,
        stripped,
        unchanged,
        skipped,
        failures,
        " [dry-run: counts are planned actions]" if dry_run else "",
    )
    return failures
