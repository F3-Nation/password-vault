"""Tests for the role → collection membership sync rules."""

from __future__ import annotations

from password_vault_sync import sync_users
from tests.helpers import FakeF3, FakeVW, collection, grant, member

ENABLED = {"id": 37004, "name": "Milwaukee", "meta": {"password_vault_enabled": True}}
DISABLED = {"id": 25220, "name": "Other", "meta": None}
COLLECTION = collection("c-mke", "37004", "2.encrypted")


def api_user(email, *roles):
    return {
        "email": email,
        "roles": [{"orgId": org_id, "orgName": "x", "roleName": role} for org_id, role in roles],
    }


def run(api_users, orgs, collections, members, dry_run=False):
    f3 = FakeF3(orgs=orgs, users=api_users)
    vw = FakeVW(collections=collections, members=members)
    failures = sync_users.run(f3, vw, dry_run=dry_run)
    return vw, failures


def test_manager_is_read_write_and_reader_is_read_only() -> None:
    vw, failures = run(
        api_users=[
            api_user("rw@example.com", (37004, "password_manager")),
            api_user("ro@example.com", (37004, "password_reader")),
        ],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[
            member("m-rw", "rw@example.com"),
            member("m-ro", "ro@example.com"),
        ],
    )
    assert failures == 0
    updates = {call[1]["id"]: call for call in vw.mutations if call[0] == "set_member"}
    assert updates["m-rw"][1]["collections"] == [grant("c-mke", read_only=False)]
    assert updates["m-ro"][1]["collections"] == [grant("c-mke", read_only=True)]


def test_both_roles_prefer_read_write() -> None:
    vw, _ = run(
        api_users=[api_user("both@example.com", (37004, "password_reader"), (37004, "password_manager"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "both@example.com")],
    )
    (call,) = vw.mutations
    assert call[1]["collections"] == [grant("c-mke", read_only=False)]


def test_roles_for_disabled_orgs_are_ignored() -> None:
    # Otherwise the users task would re-grant access the org task just stripped.
    vw, failures = run(
        api_users=[api_user("x@example.com", (25220, "password_manager"))],
        orgs=[DISABLED],
        collections=[collection("c-other", "25220", "2.encrypted")],
        members=[member("m1", "x@example.com")],
    )
    assert failures == 0
    assert vw.mutations == []


def test_roles_for_unknown_orgs_are_ignored() -> None:
    vw, _ = run(
        api_users=[api_user("x@example.com", (99999, "password_manager"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "x@example.com")],
    )
    assert vw.mutations == []


def test_enabled_org_without_collection_is_skipped() -> None:
    vw, _ = run(
        api_users=[api_user("x@example.com", (37004, "password_manager"))],
        orgs=[ENABLED],
        collections=[],  # waiting on the daily org sync
        members=[member("m1", "x@example.com")],
    )
    assert vw.mutations == []


def test_non_password_roles_are_ignored() -> None:
    vw, _ = run(
        api_users=[api_user("x@example.com", (37004, "admin"), (37004, "user"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "x@example.com", collections=[grant("c-mke", read_only=True)])],
    )
    # No password role means no access, so any stale grant is removed.
    (call,) = vw.mutations
    assert call[0] == "set_member"
    assert call[1]["collections"] == []


def test_new_user_with_access_is_invited_with_grants() -> None:
    vw, failures = run(
        api_users=[api_user("new@example.com", (37004, "password_reader"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "someone.else@example.com")],
    )
    assert failures == 0
    (call,) = vw.mutations
    assert call == ("invite", {"email": "new@example.com", "collections": [grant("c-mke", read_only=True)]})


def test_new_user_without_access_is_not_invited() -> None:
    vw, _ = run(
        api_users=[api_user("nobody@example.com")],  # in the API but no password roles
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "other@example.com")],
    )
    assert vw.mutations == []


def test_member_without_roles_loses_all_grants() -> None:
    vw, failures = run(
        api_users=[],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "gone@example.com", collections=[grant("c-mke", read_only=False)])],
    )
    assert failures == 0
    (call,) = vw.mutations
    assert call[0] == "set_member"
    assert call[1]["id"] == "m1"
    assert call[1]["collections"] == []  # stays a member, zero collection grants


def test_unchanged_member_is_not_written() -> None:
    vw, _ = run(
        api_users=[api_user("ok@example.com", (37004, "password_reader"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "ok@example.com", collections=[grant("c-mke", read_only=True)])],
    )
    assert vw.mutations == []


def test_promotion_to_read_write_only_flips_read_only() -> None:
    previous = dict(grant("c-mke", read_only=True), hidePasswords=True)
    vw, _ = run(
        api_users=[api_user("up@example.com", (37004, "password_manager"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "up@example.com", collections=[previous])],
    )
    (call,) = vw.mutations
    entry = call[1]["collections"][0]
    assert entry["readOnly"] is False
    assert entry["hidePasswords"] is True  # other flags on the existing row are preserved


def test_access_all_members_are_never_modified() -> None:
    vw, _ = run(
        api_users=[api_user("owner@example.com", (37004, "password_reader"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "owner@example.com", access_all=True, collections=[])],
    )
    assert vw.mutations == []


def test_revoked_members_are_never_modified() -> None:
    vw, _ = run(
        api_users=[api_user("revoked@example.com")],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "revoked@example.com", status=-1, collections=[grant("c-mke", read_only=True)])],
    )
    assert vw.mutations == []


def test_email_matching_is_case_and_whitespace_insensitive() -> None:
    vw, _ = run(
        api_users=[api_user("  Mark@Example.COM ", (37004, "password_manager"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "mark@example.com")],
    )
    (call,) = vw.mutations
    assert call[0] == "set_member"  # matched, and granted read/write
    assert call[1]["collections"] == [grant("c-mke", read_only=False)]


def test_grants_are_stable_across_runs() -> None:
    f3 = FakeF3(orgs=[ENABLED], users=[api_user("a@example.com", (37004, "password_reader"))])
    vw = FakeVW(collections=[COLLECTION], members=[member("m1", "a@example.com")])
    assert sync_users.run(f3, vw) == 0
    assert len(vw.mutations) == 1  # first run grants access
    assert sync_users.run(f3, vw) == 0
    assert len(vw.mutations) == 1  # second run is a no-op


def test_invited_member_grants_read_from_collection_side() -> None:
    """Invited (status 0) members show grants only on the collection record, not the
    member record — reconciliation must not re-send them on every run."""
    collection_with_grant = collection(
        "c-mke",
        "37004",
        "2.encrypted",
        users=[{"id": "m1", "readOnly": False, "hidePasswords": False, "manage": False}],
    )
    f3 = FakeF3(orgs=[ENABLED], users=[api_user("invited@example.com", (37004, "password_manager"))])
    vw = FakeVW(
        collections=[collection_with_grant],
        members=[member("m1", "invited@example.com", status=0, collections=[])],
    )
    assert sync_users.run(f3, vw) == 0
    assert vw.mutations == []  # grant already stored collection-side: no churn


def test_invited_member_with_wrong_grant_is_updated() -> None:
    collection_read_only = collection(
        "c-mke",
        "37004",
        "2.encrypted",
        users=[{"id": "m1", "readOnly": True, "hidePasswords": False, "manage": False}],
    )
    f3 = FakeF3(orgs=[ENABLED], users=[api_user("invited@example.com", (37004, "password_manager"))])
    vw = FakeVW(
        collections=[collection_read_only],
        members=[member("m1", "invited@example.com", status=0, collections=[])],
    )
    assert sync_users.run(f3, vw) == 0
    (call,) = vw.mutations
    assert call[1]["collections"][0]["readOnly"] is False  # promoted to rw as the role demands


def test_invited_member_without_grant_is_granted() -> None:
    """A role granted after the invite must still reach an invited member."""
    f3 = FakeF3(orgs=[ENABLED], users=[api_user("invited@example.com", (37004, "password_reader"))])
    vw = FakeVW(
        collections=[COLLECTION],
        members=[member("m1", "invited@example.com", status=0, collections=[])],
    )
    assert sync_users.run(f3, vw) == 0
    (call,) = vw.mutations
    assert call[1]["collections"][0]["id"] == "c-mke"


def test_dry_run_writes_nothing() -> None:
    vw, failures = run(
        api_users=[api_user("new@example.com", (37004, "password_manager"))],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "gone@example.com", collections=[grant("c-mke", read_only=True)])],
        dry_run=True,
    )
    assert failures == 0
    assert vw.mutations == []
    assert ("list_members",) in vw.calls


def test_per_member_failure_is_counted_and_the_rest_still_run() -> None:
    class ExplodingVW(FakeVW):
        def set_member_collections(self, member_id, member_type, groups, collections):
            if member_id == "boom":
                raise RuntimeError("boom")
            super().set_member_collections(member_id, member_type, groups, collections)

    f3 = FakeF3(orgs=[ENABLED], users=[api_user("a@example.com", (37004, "password_manager"))])
    vw = ExplodingVW(
        collections=[COLLECTION],
        members=[
            member("boom", "a@example.com"),  # needs an update, but will explode
            member("fine", "c@example.com", collections=[grant("c-mke", read_only=True)]),
        ],
    )
    assert sync_users.run(f3, vw) == 1
    # The healthy member was still stripped (no roles in the API).
    assert [call[1]["id"] for call in vw.mutations] == ["fine"]


def test_malformed_api_user_without_email_is_skipped() -> None:
    vw, failures = run(
        api_users=[{"email": "", "roles": [{"orgId": 37004, "roleName": "password_manager"}]}],
        orgs=[ENABLED],
        collections=[COLLECTION],
        members=[member("m1", "someone@example.com", collections=[grant("c-mke", read_only=True)])],
    )
    assert failures == 0
    # No grants were derived, so the existing member was stripped instead.
    (call,) = vw.mutations
    assert call[1]["collections"] == []
