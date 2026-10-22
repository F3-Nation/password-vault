"""Tests for the Vaultwarden API client (auth, routes, payload shapes)."""

from __future__ import annotations

import pytest
import requests

from password_vault_sync.vaultwarden import (
    MEMBER_TYPE_USER,
    STATUS_CONFIRMED,
    STATUS_INVITED,
    STATUS_REVOKED,
    VaultwardenApiClient,
    collection_entry,
)
from tests.helpers import FakeResponse, FakeSession

VW = "https://vault.example.test"
ORG = "org-uuid-1"


def token_response(access_token: str = "tok-1") -> FakeResponse:
    return FakeResponse(200, {"access_token": access_token, "expires_in": 3600})


def data_response(payload: dict | list) -> FakeResponse:
    return FakeResponse(200, {"data": payload})


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


def make_client(session: FakeSession) -> VaultwardenApiClient:
    return VaultwardenApiClient(
        vw_url=f"{VW}/",  # trailing slash must be normalized
        client_id="user.abc",
        client_secret="s3cret",
        org_id=ORG,
        session=session,
    )


def test_constructor_validates_inputs(session: FakeSession) -> None:
    with pytest.raises(ValueError, match="vw_url"):
        VaultwardenApiClient("", "id", "secret", ORG, session=session)
    with pytest.raises(ValueError, match="client_id"):
        VaultwardenApiClient(VW, "", "secret", ORG, session=session)
    with pytest.raises(ValueError, match="client_secret"):
        VaultwardenApiClient(VW, "id", "", ORG, session=session)
    with pytest.raises(ValueError, match="org_id"):
        VaultwardenApiClient(VW, "id", "secret", "", session=session)


def test_login_uses_client_credentials_grant(session: FakeSession) -> None:
    session.queue(token_response("tok-42"))
    token = make_client(session).login()
    assert token == "tok-42"
    sent = session.requests[0]
    assert sent["url"] == f"{VW}/identity/connect/token"
    assert sent["data"]["grant_type"] == "client_credentials"
    assert sent["data"]["client_id"] == "user.abc"
    assert sent["data"]["client_secret"] == "s3cret"
    assert sent["data"]["scope"] == "api"


def test_first_request_logs_in_lazily_and_reuses_token(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(data_response([{"id": "c1"}]))
    session.queue(data_response([{"id": "m1"}]))
    api = make_client(session)
    assert api.list_collections() == [{"id": "c1"}]
    assert api.list_members() == [{"id": "m1"}]
    # One token request + two API requests, and only the first API call logged in.
    assert session.requests[0]["url"].endswith("/identity/connect/token")
    assert session.requests[1]["headers"] == {"Authorization": "Bearer tok-1"}
    assert session.requests[2]["headers"] == {"Authorization": "Bearer tok-1"}
    assert len(session.requests) == 3


def test_list_collection_details_route(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(data_response([{"id": "c1", "users": []}]))
    make_client(session).list_collection_details()
    assert session.requests[1]["url"] == f"{VW}/api/organizations/{ORG}/collections/details"


def test_list_members_requests_includes_and_groups(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(data_response([]))
    make_client(session).list_members()
    sent = session.requests[1]
    assert sent["url"] == f"{VW}/api/organizations/{ORG}/users"
    assert sent["params"] == {"includeCollections": "true", "includeGroups": "true"}


def test_create_collection_payload(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(200, {"id": "c-new", "name": "2.enc", "externalId": "37004"}))
    created = make_client(session).create_collection("2.enc", 37004)
    sent = session.requests[1]
    assert sent["url"] == f"{VW}/api/organizations/{ORG}/collections"
    assert sent["json"] == {"name": "2.enc", "externalId": "37004", "groups": [], "users": []}
    assert created["id"] == "c-new"


def test_update_collection_replaces_name_and_selections(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(200, {"id": "c1"}))
    users = [{"id": "member-1", "readOnly": True, "hidePasswords": False, "manage": False}]
    make_client(session).update_collection("c1", "2.newname", 25220, users=users, groups=[])
    sent = session.requests[1]
    assert sent["url"] == f"{VW}/api/organizations/{ORG}/collections/c1"
    assert sent["json"] == {"name": "2.newname", "externalId": "25220", "groups": [], "users": users}


def test_update_collection_passes_through_none_external_id(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(200, {"id": "c1"}))
    make_client(session).update_collection("c1", "2.n", None, users=[], groups=[])
    assert session.requests[1]["json"]["externalId"] is None


def test_invite_member_payload(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(200, {}))
    entries = [collection_entry("c1", read_only=True)]
    make_client(session).invite_member("mark@example.com", entries)
    sent = session.requests[1]
    assert sent["url"] == f"{VW}/api/organizations/{ORG}/users/invite"
    assert sent["json"] == {
        "emails": ["mark@example.com"],
        "groups": [],
        "type": MEMBER_TYPE_USER,
        "collections": entries,
    }


def test_set_member_collections_payload(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(200, {}))
    entries = [collection_entry("c1", read_only=False)]
    make_client(session).set_member_collections("member-9", 2, [{"id": "g1"}], entries)
    sent = session.requests[1]
    assert sent["url"] == f"{VW}/api/organizations/{ORG}/users/member-9"
    assert sent["json"] == {"type": 2, "groups": [{"id": "g1"}], "collections": entries}


def test_collection_entry_shape() -> None:
    assert collection_entry("c1", read_only=True) == {
        "id": "c1",
        "readOnly": True,
        "hidePasswords": False,
        "manage": False,
    }
    assert collection_entry("c1", read_only=False)["readOnly"] is False


def test_error_responses_include_the_body(session: FakeSession) -> None:
    session.queue(token_response())
    session.queue(FakeResponse(400, {"message": "User is already invited."}))
    with pytest.raises(requests.HTTPError) as exc:
        make_client(session).invite_member("dup@example.com", [])
    assert "400" in str(exc.value)
    assert "already invited" in str(exc.value)


def test_failed_login_surfaces_the_error(session: FakeSession) -> None:
    session.queue(FakeResponse(400, {"error": "invalid_client"}))
    with pytest.raises(requests.HTTPError) as exc:
        make_client(session).login()
    assert "invalid_client" in str(exc.value)


def test_status_and_member_type_constants() -> None:
    assert (STATUS_REVOKED, STATUS_INVITED, STATUS_CONFIRMED) == (-1, 0, 2)
    assert MEMBER_TYPE_USER == 2
