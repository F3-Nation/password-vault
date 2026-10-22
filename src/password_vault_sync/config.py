"""Environment configuration for the sync job.

Secrets come from Secret Manager in Cloud Run (bound as env vars) or from a local
``.env`` file during development. See ``.env.example`` for the full list.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

DEFAULT_F3_API_URL = "https://api.f3nation.com"
DEFAULT_F3_API_CLIENT = "password-vault-sync"


@dataclass(frozen=True)
class Config:
    vaultwarden_url: str
    vaultwarden_client_id: str
    vaultwarden_client_secret: str
    vaultwarden_org_id: str
    vaultwarden_org_key: bytes | None
    f3_api_url: str
    f3_api_key: str
    f3_api_client: str

    @classmethod
    def from_env(cls, require_org_key: bool = False) -> Config:
        """Builds a config from environment variables, failing fast on anything missing."""
        missing: list[str] = []
        for name in (
            "VAULTWARDEN_URL",
            "VAULTWARDEN_CLIENT_ID",
            "VAULTWARDEN_CLIENT_SECRET",
            "VAULTWARDEN_ORG_ID",
            "F3_API_KEY",
        ):
            if not os.getenv(name):
                missing.append(name)

        org_key: bytes | None = None
        raw_org_key = (os.getenv("VAULTWARDEN_ORG_KEY") or "").strip()
        if raw_org_key:
            try:
                org_key = base64.b64decode(raw_org_key)
            except Exception as exc:
                raise ValueError("VAULTWARDEN_ORG_KEY must be valid base64") from exc
        elif require_org_key:
            missing.append("VAULTWARDEN_ORG_KEY")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(
            vaultwarden_url=os.environ["VAULTWARDEN_URL"].rstrip("/"),
            vaultwarden_client_id=os.environ["VAULTWARDEN_CLIENT_ID"],
            vaultwarden_client_secret=os.environ["VAULTWARDEN_CLIENT_SECRET"],
            vaultwarden_org_id=os.environ["VAULTWARDEN_ORG_ID"],
            vaultwarden_org_key=org_key,
            f3_api_url=(os.getenv("F3_API_URL") or DEFAULT_F3_API_URL).rstrip("/"),
            f3_api_key=os.environ["F3_API_KEY"],
            f3_api_client=os.getenv("F3_API_CLIENT") or DEFAULT_F3_API_CLIENT,
        )
