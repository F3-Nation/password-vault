"""Tests for the org → collection sync rules."""

from __future__ import annotations

import pytest

from password_vault_sync import sync_orgs
from password_vault_sync.bitwarden_crypto import decrypt_enc_string, encrypt_string
from tests.helpers import FakeF3, FakeVW, collection

ORG_KEY = bytes([0x11]) * 32


def org(org_id, name, enabled: bool | None = True):
    return {"id": org_id, "name": name, "meta": {"password_vault_enabled": enabled} if enabled is not None else None}


def stored_name(name: str) -> str:
    return encrypt_string(name, ORG_KEY)


def test_org_key_is_required() -> None:
    with pytest.raises(ValueError, match="VAULTWARDEN_ORG_KEY"):
        sync_orgs.run(FakeF3(), FakeVW(), org_key=None)


def test_creates_missing_collection_for_enabled_org() -> None:
    f3 = FakeF3(orgs=[org(37004, "Milwaukee")])
    vw = FakeVW()
    failures = sync_orgs.run(f3, vw, ORG_KEY)
    assert failures == 0
    assert len(vw.mutations) == 1
    kind, payload = vw.mutations[0]
    assert kind == "create"
    assert payload["externalId"] == "37004"
    assert decrypt_enc_string(payload["name"], ORG_KEY) == b"Milwaukee"


def test_no_op_when_collection_already_matches() -> None:
    f3 = FakeF3(orgs=[org(37004, "Milwaukee")])
    vw = FakeVW(collections=[collection("c1", "37004", stored_name("Milwaukee"))])
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0
    assert vw.mutations == []


def test_renames_collection_and_preserves_selections() -> None:
    users = [{"id": "member-1", "readOnly": True, "hidePasswords": False, "manage": False}]
    f3 = FakeF3(orgs=[org(25220, "Renamed City")])
    vw = FakeVW(collections=[collection("c1", "25220", stored_name("Old City"), users=users, groups=[{"id": "g1"}])])
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0

    kind, payload = vw.mutations[0]
    assert kind == "update"
    assert payload["id"] == "c1"
    assert payload["externalId"] == "25220"
    assert payload["users"] == users  # PUT replaces selections, so they must be replayed
    assert payload["groups"] == [{"id": "g1"}]
    assert decrypt_enc_string(payload["name"], ORG_KEY) == b"Renamed City"


def test_undecryptable_name_is_renamed_to_the_api_name() -> None:
    f3 = FakeF3(orgs=[org(1, "Correct Name")])
    vw = FakeVW(collections=[collection("c1", "1", "2.not|really|valid")])
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0
    assert [call[0] for call in vw.mutations] == ["update"]


def test_disabled_org_strips_grants_but_keeps_collection() -> None:
    users = [{"id": "member-1", "readOnly": False, "hidePasswords": False, "manage": False}]
    f3 = FakeF3(orgs=[org(37004, "Milwaukee", enabled=False)])
    stored = stored_name("Milwaukee")
    vw = FakeVW(collections=[collection("c1", "37004", stored, users=users, groups=[{"id": "g1"}])])
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0

    kind, payload = vw.mutations[0]
    assert kind == "update"
    assert payload["users"] == []  # every grant removed...
    assert payload["name"] == stored  # ...but the collection (and its name) survives
    assert payload["externalId"] == "37004"
    assert payload["groups"] == [{"id": "g1"}]


def test_disabled_org_without_collection_does_nothing() -> None:
    f3 = FakeF3(orgs=[org(9, "No Vault", enabled=False), org(10, "meta missing", enabled=None)])
    vw = FakeVW()
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0
    assert vw.mutations == []


def test_collection_without_external_id_is_left_alone() -> None:
    f3 = FakeF3(orgs=[org(1, "A", enabled=False)])
    vw = FakeVW(collections=[collection("manual", None, stored_name("Hand made"))])
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0
    assert vw.mutations == []


def test_orphan_collection_is_untouched() -> None:
    f3 = FakeF3(orgs=[org(1, "A")])
    vw = FakeVW(
        collections=[
            collection("c1", "1", stored_name("A")),  # owned by org 1
            collection("orphan", "99999", stored_name("Ghost")),
        ]
    )
    assert sync_orgs.run(f3, vw, ORG_KEY) == 0
    assert vw.mutations == []  # the orphan is never deleted or edited


def test_duplicate_external_id_counts_a_failure_and_skips_the_org() -> None:
    f3 = FakeF3(orgs=[org(1, "A")])
    vw = FakeVW(
        collections=[
            collection("c1", "1", stored_name("A")),
            collection("c2", "1", stored_name("A")),
        ]
    )
    assert sync_orgs.run(f3, vw, ORG_KEY) == 1
    assert vw.mutations == []


def test_per_org_failure_is_counted_and_the_rest_still_run() -> None:
    f3 = FakeF3(orgs=[org(1, "A"), org(2, "B")])

    class ExplodingVW(FakeVW):
        def create_collection(self, encrypted_name, external_id):
            if str(external_id) == "1":
                raise RuntimeError("boom")
            return super().create_collection(encrypted_name, external_id)

    vw = ExplodingVW()
    assert sync_orgs.run(f3, vw, ORG_KEY) == 1
    assert [call[0] for call in vw.mutations] == ["create"]
    assert vw.mutations[0][1]["externalId"] == "2"


def test_dry_run_plans_without_writing() -> None:
    f3 = FakeF3(orgs=[org(1, "New", enabled=True), org(2, "Off", enabled=False)])
    vw = FakeVW(collections=[collection("c2", "2", stored_name("Off"), users=[{"id": "m"}])])
    assert sync_orgs.run(f3, vw, ORG_KEY, dry_run=True) == 0
    assert vw.mutations == []
    # The collection list was still read to make the plan.
    assert ("list_details",) in vw.calls
