"""HTTP client for Vaultwarden organization member and collection management.

Authenticates non-interactively with an organization owner/admin API key via the
OAuth2 ``client_credentials`` grant, so it runs unattended (as a Cloud Run Job)
without a master password or an interactive ``bw`` CLI session.

Collection *names* are encrypted client-side by this codebase (see
``bitwarden_crypto``) — Vaultwarden stores whatever string it receives and the
Web Vault decrypts it with the organization key.

Routes and payload shapes were verified against the live Vaultwarden instance and
``dani-garcia/vaultwarden`` sources:

* ``GET  /api/organizations/{org}/collections``          — list (name already encrypted)
* ``GET  /api/organizations/{org}/collections/details``  — list incl. users/groups selections
* ``POST /api/organizations/{org}/collections``          — create: ``{name, externalId, groups, users}``
* ``PUT  /api/organizations/{org}/collections/{id}`      — replace name/externalId AND all selections
* ``GET  /api/organizations/{org}/users``                — members incl. collection grants
* ``POST /api/organizations/{org}/users/invite``         — invite with collection entries
* ``PUT  /api/organizations/{org}/users/{id}`            — replace a member's collection grants
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

# Vaultwarden/Bitwarden organization user status codes.
STATUS_REVOKED = -1
STATUS_INVITED = 0
STATUS_ACCEPTED = 1
STATUS_CONFIRMED = 2

# Organization user type: 2 = User (standard member).
MEMBER_TYPE_USER = 2

DEFAULT_TIMEOUT = 30


def _raise_for_status(resp: requests.Response) -> None:
    """Like ``Response.raise_for_status`` but includes the response body.

    Vaultwarden returns a JSON error payload (e.g. an invalid collection id or an
    already-invited user) that explains *why* a request failed. The default
    ``raise_for_status`` hides it, leaving only a bare status code, so we surface
    the body to make failures diagnosable.
    """
    if resp.ok:
        return
    detail = (resp.text or "").strip()
    message = f"{resp.status_code} {resp.reason} for url: {resp.url}"
    if detail:
        message = f"{message}: {detail}"
    raise requests.HTTPError(message, response=resp)


def collection_entry(collection_id: str, read_only: bool) -> dict:
    """A collection grant entry as used by invites and member updates."""
    return {
        "id": collection_id,
        "readOnly": read_only,
        "hidePasswords": False,
        "manage": False,
    }


class VaultwardenApiClient:
    """HTTP client for Vaultwarden organization member and collection management."""

    def __init__(
        self,
        vw_url: str,
        client_id: str,
        client_secret: str,
        org_id: str,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not vw_url:
            raise ValueError("Vaultwarden URL ('vw_url') is required.")
        if not client_id or not client_secret:
            raise ValueError("API credentials ('client_id'/'client_secret') are required.")
        if not org_id:
            raise ValueError("Organization ID ('org_id') is required.")

        self.vw_url = vw_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.org_id = org_id
        self.timeout = timeout
        self.session = session or requests.Session()
        self._token: str | None = None
        self._device_identifier = "f3-password-vault-sync"

    def login(self) -> str:
        """Obtains an API access token via the client_credentials grant."""
        resp = self.session.post(
            f"{self.vw_url}/identity/connect/token",
            data={
                "grant_type": "client_credentials",
                "scope": "api",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "device_identifier": self._device_identifier,
                "device_name": "f3-password-vault-sync",
                "device_type": "14",
            },
            timeout=self.timeout,
        )
        _raise_for_status(resp)
        token = resp.json()["access_token"]
        self._token = token
        return token

    def _auth_header(self) -> dict:
        if self._token is None:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self.session.get(
            f"{self.vw_url}{path}",
            params=params,
            headers=self._auth_header(),
            timeout=self.timeout,
        )
        _raise_for_status(resp)
        return resp.json()

    def list_collections(self) -> list:
        """Returns the organization's collections (names as stored: encrypted)."""
        return self._get(f"/api/organizations/{self.org_id}/collections")["data"]

    def list_collection_details(self) -> list:
        """Returns collections including their current ``users``/``groups`` selections.

        This is the source of truth for preserving access when renaming a collection
        via PUT (PUT replaces *all* selections).
        """
        return self._get(f"/api/organizations/{self.org_id}/collections/details")["data"]

    def list_members(self) -> list:
        """Returns the organization's members, including their collection grants."""
        return self._get(
            f"/api/organizations/{self.org_id}/users",
            params={"includeCollections": "true", "includeGroups": "true"},
        )["data"]

    def invite_member(self, email: str, collections: list, member_type: int = MEMBER_TYPE_USER) -> None:
        """Invites a new member to the organization with the given collection access."""
        resp = self.session.post(
            f"{self.vw_url}/api/organizations/{self.org_id}/users/invite",
            json={
                "emails": [email],
                "groups": [],
                "type": member_type,
                "collections": collections,
            },
            headers=self._auth_header(),
            timeout=self.timeout,
        )
        _raise_for_status(resp)

    def set_member_collections(self, member_id: str, member_type: int, groups: list, collections: list) -> None:
        """Replaces an existing member's collection grants (and preserves group/type)."""
        resp = self.session.put(
            f"{self.vw_url}/api/organizations/{self.org_id}/users/{member_id}",
            json={"type": member_type, "groups": groups, "collections": collections},
            headers=self._auth_header(),
            timeout=self.timeout,
        )
        _raise_for_status(resp)

    def create_collection(self, encrypted_name: str, external_id: str | int) -> dict:
        """Creates a collection. ``encrypted_name`` must already be encrypted with the org key."""
        resp = self.session.post(
            f"{self.vw_url}/api/organizations/{self.org_id}/collections",
            json={
                "name": encrypted_name,
                "externalId": str(external_id),
                "groups": [],
                "users": [],
            },
            headers=self._auth_header(),
            timeout=self.timeout,
        )
        _raise_for_status(resp)
        return resp.json()

    def update_collection(
        self,
        collection_id: str,
        encrypted_name: str,
        external_id: str | int | None,
        users: list,
        groups: list,
    ) -> dict:
        """Replaces a collection's name/externalId AND its users/groups selections.

        Vaultwarden's PUT deletes and re-inserts every selection row, so callers
        must pass the selections they want to keep (see ``list_collection_details``).
        """
        resp = self.session.put(
            f"{self.vw_url}/api/organizations/{self.org_id}/collections/{collection_id}",
            json={
                "name": encrypted_name,
                "externalId": str(external_id) if external_id is not None else None,
                "groups": groups,
                "users": users,
            },
            headers=self._auth_header(),
            timeout=self.timeout,
        )
        _raise_for_status(resp)
        return resp.json()
