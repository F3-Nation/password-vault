"""Tests for the CLI entry point (``python -m password_vault_sync``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from password_vault_sync import __main__ as entry


@pytest.fixture
def wiring(monkeypatch):
    """Replaces config/clients/sync tasks with recorders and returns the call log."""
    calls: list[tuple] = []

    class StubConfig:
        @classmethod
        def from_env(cls, require_org_key: bool = False):
            calls.append(("config", require_org_key))
            if getattr(cls, "boom", False):
                raise ValueError("Missing required environment variables: F3_API_KEY")
            return SimpleNamespace(
                vaultwarden_url="https://vault.test",
                vaultwarden_client_id="id",
                vaultwarden_client_secret="secret",
                vaultwarden_org_id="org-1",
                vaultwarden_org_key=b"org-key",
                f3_api_url="https://api.test",
                f3_api_key="key",
                f3_api_client="client",
            )

    class StubF3:
        def __init__(self, *args, **kwargs):
            calls.append(("f3", args))

    class StubVW:
        def __init__(self, **kwargs):
            calls.append(("vw", sorted(kwargs)))

        def login(self):
            calls.append(("vw_login",))

    def stub_orgs(f3, vw, org_key, dry_run=False):
        calls.append(("orgs", org_key, dry_run))
        return 0

    def stub_users(f3, vw, dry_run=False):
        calls.append(("users", dry_run))
        return 0

    monkeypatch.setattr(entry, "Config", StubConfig)
    monkeypatch.setattr(entry, "F3Client", StubF3)
    monkeypatch.setattr(entry, "VaultwardenApiClient", StubVW)
    monkeypatch.setattr(entry.sync_orgs, "run", stub_orgs)
    monkeypatch.setattr(entry.sync_users, "run", stub_users)
    # Don't let a local .env leak into the test environment.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    return calls


def test_parser_defaults_to_all_tasks() -> None:
    args = entry.build_parser().parse_args([])
    assert args.task == "all"
    assert args.dry_run is False
    assert args.verbose is False


def test_parser_accepts_each_task_and_flags() -> None:
    for task in ("orgs", "users", "all"):
        assert entry.build_parser().parse_args(["--task", task]).task == task
    args = entry.build_parser().parse_args(["--task", "users", "--dry-run", "-v"])
    assert args.dry_run is True
    assert args.verbose is True


def test_parser_rejects_unknown_task() -> None:
    with pytest.raises(SystemExit):
        entry.build_parser().parse_args(["--task", "everything"])


def test_orgs_task_requires_the_org_key_and_skips_users(wiring) -> None:
    assert entry.main(["--task", "orgs"]) == 0
    assert ("config", True) in wiring
    assert ("orgs", b"org-key", False) in wiring
    assert not any(call[0] == "users" for call in wiring)


def test_users_task_does_not_require_the_org_key(wiring) -> None:
    assert entry.main(["--task", "users"]) == 0
    assert ("config", False) in wiring
    assert ("users", False) in wiring
    assert not any(call[0] == "orgs" for call in wiring)


def test_all_task_runs_orgs_before_users(wiring) -> None:
    assert entry.main(["--task", "all"]) == 0
    kinds = [call[0] for call in wiring]
    assert kinds.index("orgs") < kinds.index("users")


def test_dry_run_is_forwarded_to_both_tasks(wiring) -> None:
    assert entry.main(["--task", "all", "--dry-run"]) == 0
    assert ("orgs", b"org-key", True) in wiring
    assert ("users", True) in wiring


def test_configuration_error_exits_with_code_2(wiring, monkeypatch) -> None:
    class BrokenConfig:
        @classmethod
        def from_env(cls, require_org_key: bool = False):
            raise ValueError("Missing required environment variables: F3_API_KEY")

    monkeypatch.setattr(entry, "Config", BrokenConfig)
    assert entry.main(["--task", "orgs"]) == 2
    # Nothing ran past configuration.
    assert not any(call[0] == "orgs" for call in wiring)


def test_task_failure_exits_with_code_1(wiring, monkeypatch) -> None:
    monkeypatch.setattr(entry.sync_users, "run", lambda *a, **k: 3)
    assert entry.main(["--task", "users"]) == 1


def test_fatal_error_exits_with_code_1(wiring, monkeypatch) -> None:
    def explode(*a, **k):
        raise RuntimeError("Vaultwarden unreachable")

    monkeypatch.setattr(entry.sync_orgs, "run", explode)
    assert entry.main(["--task", "orgs"]) == 1
