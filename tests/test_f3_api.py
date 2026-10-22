"""Tests for the F3 Nation API client (auth headers, pagination, error surfacing)."""

from __future__ import annotations

import pytest
import requests

from password_vault_sync.f3_api import (
    F3ApiError,
    F3Client,
    password_vault_enabled,
)
from tests.helpers import FakeResponse, FakeSession

BASE = "https://api.f3nation.test"


def client(session: FakeSession, page_size: int = 2) -> F3Client:
    return F3Client(BASE, "key-123", "vault-sync", session=session, page_size=page_size)


def org_page(orgs, total):
    return FakeResponse(200, {"orgs": orgs, "total": total})


def user_page(users, total):
    return FakeResponse(200, {"users": users, "totalCount": total})


def test_api_key_is_required() -> None:
    with pytest.raises(ValueError, match="api_key"):
        F3Client(BASE, "", "client")


def test_requests_carry_bearer_and_client_headers() -> None:
    session = FakeSession()
    session.queue(org_page([], 0))
    client(session).list_region_orgs()
    sent = session.requests[0]
    assert sent["headers"] == {"Authorization": "Bearer key-123", "Client": "vault-sync"}
    assert sent["url"] == f"{BASE}/v1/org"
    assert sent["timeout"] == 30


def test_base_url_trailing_slash_is_stripped() -> None:
    session = FakeSession()
    session.queue(org_page([], 0))
    F3Client(f"{BASE}/", "key-123", "vault-sync", session=session).list_region_orgs()
    assert session.requests[0]["url"] == f"{BASE}/v1/org"


def test_single_page_when_result_smaller_than_page_size() -> None:
    session = FakeSession()
    session.queue(org_page([{"id": 1}, {"id": 2}], 2))
    orgs = client(session, page_size=100).list_region_orgs()
    assert [o["id"] for o in orgs] == [1, 2]
    assert len(session.requests) == 1
    assert ("pageIndex", "0") in session.requests[0]["params"]
    assert ("pageSize", "100") in session.requests[0]["params"]
    # Orgs are requested as region-type only.
    assert ("orgTypes", "region") in session.requests[0]["params"]


def test_pages_until_total_reached() -> None:
    session = FakeSession()
    # page_size=2: two full pages then a partial one.
    session.queue(org_page([{"id": 1}, {"id": 2}], 5))
    session.queue(org_page([{"id": 3}, {"id": 4}], 5))
    session.queue(org_page([{"id": 5}], 5))
    orgs = client(session, page_size=2).list_region_orgs()
    assert [o["id"] for o in orgs] == [1, 2, 3, 4, 5]
    assert [r["params"][-2] for r in session.requests] == [
        ("pageIndex", "0"),
        ("pageIndex", "1"),
        ("pageIndex", "2"),
    ]


def test_stops_on_empty_page_even_if_total_lies() -> None:
    session = FakeSession()
    session.queue(org_page([{"id": 1}, {"id": 2}], 99))
    session.queue(org_page([], 99))
    orgs = client(session, page_size=2).list_region_orgs()
    assert len(orgs) == 2
    assert len(session.requests) == 2


def test_user_query_uses_both_password_roles() -> None:
    session = FakeSession()
    session.queue(user_page([], 0))
    client(session).list_password_role_users()
    sent = session.requests[0]
    assert sent["url"] == f"{BASE}/v1/user"
    assert ("roles", "password_manager") in sent["params"]
    assert ("roles", "password_reader") in sent["params"]


def test_http_error_includes_response_body() -> None:
    session = FakeSession()
    session.queue(FakeResponse(401, {"detail": "Invalid or expired bearer token."}))
    with pytest.raises(requests.HTTPError) as exc:
        client(session).list_region_orgs()
    assert "401" in str(exc.value)
    assert "Invalid or expired bearer token." in str(exc.value)


def test_non_object_response_raises() -> None:
    session = FakeSession()
    session.queue(FakeResponse(200, [1, 2, 3]))
    with pytest.raises(F3ApiError, match="Expected an object"):
        client(session).list_region_orgs()


def test_missing_list_key_raises() -> None:
    session = FakeSession()
    session.queue(FakeResponse(200, {"unexpected": []}))
    with pytest.raises(F3ApiError, match="no 'orgs' property"):
        client(session).list_region_orgs()


def test_wrong_type_list_key_raises() -> None:
    session = FakeSession()
    session.queue(FakeResponse(200, {"orgs": "nope", "total": 1}))
    with pytest.raises(F3ApiError, match="to be a list"):
        client(session).list_region_orgs()


def test_total_mismatch_is_logged_not_fatal() -> None:
    session = FakeSession()
    # total says 10 but the first page is short -> stops, warns, returns what it has.
    session.queue(org_page([{"id": 1}], 10))
    assert len(client(session).list_region_orgs()) == 1


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"password_vault_enabled": True}, True),
        ({"password_vault_enabled": False}, False),
        ({"password_vault_enabled": None}, False),
        ({}, False),
        (None, False),
        ("not-a-dict", False),
        ({"password_vault_enabled": "true"}, False),  # must be strictly True
    ],
)
def test_password_vault_enabled(meta, expected) -> None:
    assert password_vault_enabled({"id": 1, "meta": meta}) is expected


def test_iter_org_meta_pairs_org_with_flag() -> None:
    session = FakeSession()
    session.queue(
        org_page(
            [
                {"id": 1, "meta": {"password_vault_enabled": True}},
                {"id": 2, "meta": None},
            ],
            2,
        )
    )
    pairs = list(client(session).iter_org_meta())
    assert [(org["id"], enabled) for org, enabled in pairs] == [(1, True), (2, False)]
