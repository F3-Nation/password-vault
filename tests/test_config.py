"""Tests for environment-driven configuration (``Config.from_env``)."""

from __future__ import annotations

import base64

import pytest

from password_vault_sync.config import DEFAULT_F3_API_CLIENT, DEFAULT_F3_API_URL, Config

REQUIRED = (
    "VAULTWARDEN_URL",
    "VAULTWARDEN_CLIENT_ID",
    "VAULTWARDEN_CLIENT_SECRET",
    "VAULTWARDEN_ORG_ID",
    "F3_API_KEY",
)


@pytest.fixture
def full_env(monkeypatch):
    """A minimal, complete environment."""
    monkeypatch.setenv("VAULTWARDEN_URL", "https://vault.example.com/")
    monkeypatch.setenv("VAULTWARDEN_CLIENT_ID", "user.abc")
    monkeypatch.setenv("VAULTWARDEN_CLIENT_SECRET", "secret")
    monkeypatch.setenv("VAULTWARDEN_ORG_ID", "org-1")
    monkeypatch.setenv("F3_API_KEY", "api-key")
    monkeypatch.delenv("VAULTWARDEN_ORG_KEY", raising=False)
    monkeypatch.delenv("F3_API_URL", raising=False)
    monkeypatch.delenv("F3_API_CLIENT", raising=False)
    return monkeypatch


def test_from_env_reads_and_normalizes(monkeypatch, full_env) -> None:
    cfg = Config.from_env()
    assert cfg.vaultwarden_url == "https://vault.example.com"  # trailing slash stripped
    assert cfg.vaultwarden_client_id == "user.abc"
    assert cfg.vaultwarden_client_secret == "secret"
    assert cfg.vaultwarden_org_id == "org-1"
    assert cfg.f3_api_key == "api-key"
    assert cfg.f3_api_url == DEFAULT_F3_API_URL
    assert cfg.f3_api_client == DEFAULT_F3_API_CLIENT
    assert cfg.vaultwarden_org_key is None  # optional unless the org task runs


def test_from_env_custom_f3_urls_and_client(full_env) -> None:
    full_env.setenv("F3_API_URL", "https://api.test/")
    full_env.setenv("F3_API_CLIENT", "my-client")
    cfg = Config.from_env()
    assert cfg.f3_api_url == "https://api.test"
    assert cfg.f3_api_client == "my-client"


def test_org_key_is_base64_decoded(full_env) -> None:
    full_env.setenv("VAULTWARDEN_ORG_KEY", base64.b64encode(b"org-key-bytes").decode())
    cfg = Config.from_env(require_org_key=True)
    assert cfg.vaultwarden_org_key == b"org-key-bytes"


def test_org_key_required_for_org_task(full_env) -> None:
    with pytest.raises(ValueError, match="VAULTWARDEN_ORG_KEY"):
        Config.from_env(require_org_key=True)
    # ...but not for the users-only task.
    assert Config.from_env(require_org_key=False).vaultwarden_org_key is None


def test_invalid_org_key_base64_is_rejected(full_env) -> None:
    full_env.setenv("VAULTWARDEN_ORG_KEY", "abc")  # not valid base64 (bad length)
    with pytest.raises(ValueError, match="must be valid base64"):
        Config.from_env(require_org_key=True)


@pytest.mark.parametrize("missing", REQUIRED)
def test_missing_variables_are_listed(monkeypatch, missing) -> None:
    for name in REQUIRED:
        if name == missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, "x")
    monkeypatch.delenv("VAULTWARDEN_ORG_KEY", raising=False)
    with pytest.raises(ValueError, match=missing):
        Config.from_env()
