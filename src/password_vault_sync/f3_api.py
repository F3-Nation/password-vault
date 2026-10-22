"""Client for the F3 Nation API.

Auth follows the API reference (https://api.f3nation.com/docs):

* ``Authorization: Bearer <API_KEY>`` — a key from https://admin.f3nation.com/api-keys
* ``Client: <identifier>`` — free-form identifier required on every request

Both list endpoints paginate: ``pageSize`` is capped at 100 and omitting
``pageIndex``/``pageSize`` returns only the first default-sized page, so every
caller here pages through until ``total``/``totalCount`` is reached.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import requests

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
DEFAULT_TIMEOUT = 30

PASSWORD_ROLES = ("password_manager", "password_reader")
REGION_ORG_TYPES = ("region",)


def _raise_for_status(resp: requests.Response) -> None:
    """Like ``Response.raise_for_status`` but includes the response body."""
    if resp.ok:
        return
    detail = (resp.text or "").strip()
    message = f"{resp.status_code} {resp.reason} for url: {resp.url}"
    if detail:
        message = f"{message}: {detail}"
    raise requests.HTTPError(message, response=resp)


class F3ApiError(RuntimeError):
    """Raised for unexpected F3 API response shapes."""


class F3Client:
    """Read-only client for the F3 Nation org and user endpoints used by the sync."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: str,
        session: requests.Session | None = None,
        page_size: int = PAGE_SIZE,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        if not api_key:
            raise ValueError("F3 API key ('api_key') is required.")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.page_size = page_size
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Client": client,
        }

    def _get_json(self, path: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        resp = self.session.get(
            f"{self.base_url}{path}",
            params=params or [],
            headers=self.headers,
            timeout=self.timeout,
        )
        _raise_for_status(resp)
        payload = resp.json()
        if not isinstance(payload, dict):
            raise F3ApiError(f"Expected an object from {path}, got {type(payload).__name__}")
        return payload

    def _iter_pages(self, path: str, list_key: str, total_key: str, params: list[tuple[str, str]]) -> list[dict]:
        items: list[dict] = []
        page_index = 0
        while True:
            page = self._get_json(
                path,
                params=params + [("pageIndex", str(page_index)), ("pageSize", str(self.page_size))],
            )
            chunk = page.get(list_key)
            if chunk is None:
                raise F3ApiError(f"Response from {path} has no '{list_key}' property")
            if not isinstance(chunk, list):
                raise F3ApiError(f"Expected '{list_key}' to be a list in {path}")
            items.extend(chunk)
            page_index += 1
            total = page.get(total_key)
            if not chunk:
                break
            if total is not None and len(items) >= int(total):
                break
            if len(chunk) < self.page_size:
                break
        total = int(total) if total is not None and str(total).isdigit() else len(items)
        if total != len(items):
            logger.warning("%s: expected %s items from total, collected %s", path, total, len(items))
        return items

    def list_region_orgs(self) -> list[dict]:
        """All orgs of type ``region`` (id, name, meta, ...). Paginated."""
        items = self._iter_pages(
            "/v1/org",
            list_key="orgs",
            total_key="total",
            params=[("orgTypes", org_type) for org_type in REGION_ORG_TYPES],
        )
        logger.info("F3 API: %s region orgs", len(items))
        return items

    def list_password_role_users(self) -> list[dict]:
        """All users holding a ``password_manager`` or ``password_reader`` role. Paginated.

        ``includePii=true`` is required for the ``email`` field, without which the
        records cannot be matched to Vaultwarden members.
        """
        items = self._iter_pages(
            "/v1/user",
            list_key="users",
            total_key="totalCount",
            params=[("roles", role) for role in PASSWORD_ROLES] + [("includePii", "true")],
        )
        logger.info("F3 API: %s users with password roles", len(items))
        return items

    def iter_org_meta(self) -> Iterator[tuple[dict, bool]]:
        """Yields ``(org, password_vault_enabled)`` pairs for region orgs."""
        for org in self.list_region_orgs():
            yield org, password_vault_enabled(org)


def password_vault_enabled(org: dict) -> bool:
    """True only when ``meta.password_vault_enabled`` is present and strictly ``True``.

    Missing or falsy means the org does not want a collection (per the API contract).
    """
    meta = org.get("meta")
    if not isinstance(meta, dict):
        return False
    return meta.get("password_vault_enabled") is True
