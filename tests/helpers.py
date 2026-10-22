"""Shared test doubles for the sync tests."""

from __future__ import annotations

import json

import requests


class FakeResponse(requests.Response):
    """Minimal stand-in for ``requests.Response``."""

    def __init__(
        self, status_code: int = 200, payload=None, text: str | None = None, url: str = "https://example.test"
    ):
        super().__init__()
        self.status_code = status_code
        self.reason = "OK" if 200 <= status_code < 400 else "Error"
        self.url = url
        self._payload = payload
        # ``text`` on the real class derives from ``_content``; seed it instead of shadowing.
        self._content = (text if text is not None else json.dumps(payload)).encode("utf-8")
        self.encoding = "utf-8"

    def json(self, **kwargs):  # noqa: ARG002 - match requests.Response's signature
        if self._payload is not None:
            return self._payload
        return json.loads(self.text)


class FakeSession(requests.Session):
    """A ``requests.Session``-alike that replays queued responses and records requests."""

    def __init__(self):
        super().__init__()
        self.queued: list[FakeResponse] = []
        self.requests: list[dict] = []

    def queue(self, *responses: FakeResponse) -> None:
        self.queued.extend(responses)

    def _record(self, url, **record) -> FakeResponse:
        self.requests.append({"url": url, **record})
        if not self.queued:
            raise AssertionError(f"Unexpected request to {url}")
        return self.queued.pop(0)

    def get(self, url, params=None, **kwargs):
        return self._record(
            url, method="GET", params=params, headers=kwargs.get("headers"), timeout=kwargs.get("timeout")
        )

    def post(self, url, data=None, json=None, **kwargs):
        return self._record(
            url, method="POST", data=data, json=json, headers=kwargs.get("headers"), timeout=kwargs.get("timeout")
        )

    def put(self, url, data=None, json=None, **kwargs):
        return self._record(
            url, method="PUT", data=data, json=json, headers=kwargs.get("headers"), timeout=kwargs.get("timeout")
        )


class FakeF3:
    """Stands in for ``F3Client`` in sync tests."""

    def __init__(self, orgs=None, users=None):
        self.orgs = list(orgs or [])
        self.users = list(users or [])

    def list_region_orgs(self):
        return list(self.orgs)

    def list_password_role_users(self):
        return list(self.users)


class FakeVW:
    """Stands in for ``VaultwardenApiClient`` in sync tests, recording every mutation."""

    MUTATIONS = {"create", "update", "invite", "set_member"}

    def __init__(self, collections=None, members=None):
        self.collections = list(collections or [])
        self.members = list(members or [])
        self.calls: list[tuple] = []
        self._next_id = 0

    def list_collection_details(self):
        self.calls.append(("list_details",))
        return list(self.collections)

    def list_members(self):
        self.calls.append(("list_members",))
        return list(self.members)

    def create_collection(self, encrypted_name, external_id):
        self._next_id += 1
        record = {
            "id": f"new-collection-{self._next_id}",
            "name": encrypted_name,
            "externalId": str(external_id),
            "users": [],
            "groups": [],
        }
        self.calls.append(("create", dict(record)))
        self.collections.append(record)
        return record

    def update_collection(self, collection_id, encrypted_name, external_id, users, groups):
        record = {
            "id": collection_id,
            "name": encrypted_name,
            "externalId": external_id,
            "users": users,
            "groups": groups,
        }
        self.calls.append(("update", dict(record)))
        for collection in self.collections:
            if collection["id"] == collection_id:
                collection.update(record)
        return record

    def invite_member(self, email, collections):
        self.calls.append(("invite", {"email": email, "collections": collections}))

    def set_member_collections(self, member_id, member_type, groups, collections):
        self.calls.append(
            ("set_member", {"id": member_id, "type": member_type, "groups": groups, "collections": collections})
        )
        for member in self.members:
            if member["id"] == member_id:
                member["collections"] = list(collections)

    @property
    def mutations(self) -> list[tuple]:
        return [call for call in self.calls if call[0] in self.MUTATIONS]


def collection(collection_id: str, external_id: str | None, name: str, users=None, groups=None) -> dict:
    return {
        "id": collection_id,
        "name": name,
        "externalId": external_id,
        "users": list(users or []),
        "groups": list(groups or []),
    }


def member(
    member_id: str,
    email: str,
    status: int = 2,
    access_all: bool = False,
    collections=None,
    member_type: int = 2,
    groups=None,
) -> dict:
    return {
        "id": member_id,
        "email": email,
        "status": status,
        "type": member_type,
        "accessAll": access_all,
        "collections": list(collections or []),
        "groups": list(groups or []),
    }


def grant(collection_id: str, read_only: bool, hide_passwords: bool = False, manage: bool = False) -> dict:
    return {
        "id": collection_id,
        "readOnly": read_only,
        "hidePasswords": hide_passwords,
        "manage": manage,
    }
