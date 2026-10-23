"""Tests for the conventional-commit message checker (commitlint analog)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_commit_msg.py"
_spec = importlib.util.spec_from_file_location("check_commit_msg", _SCRIPT)
assert _spec and _spec.loader
check_commit_msg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_commit_msg)


def errors(message: str) -> list[str]:
    return check_commit_msg.check_message(message)


def test_valid_message_passes() -> None:
    assert errors("feat(sync): merge invited-member views") == []


def test_valid_multiple_scopes_pass() -> None:
    assert errors("fix(sync,infra): stop double-putting collections") == []


def test_release_please_title_passes() -> None:
    assert errors("chore(main): release password-vault-sync 1.1.0") == []


def test_breaking_change_marker_passes() -> None:
    assert errors("feat(sync)!: drop python 3.12") == []


def test_merge_commits_are_ignored() -> None:
    assert errors("Merge pull request #1 from dminnich/main") == []
    assert errors('Revert "feat(sync): something"') == []


def test_scope_is_required() -> None:
    assert any("[scope-empty]" in e for e in errors("feat: something"))


def test_scope_must_be_in_enum() -> None:
    got = errors("feat(backend): something")
    assert any("[scope-enum]" in e for e in got)
    assert "'backend'" in got[0]


def test_each_part_of_multi_scope_is_checked() -> None:
    assert any("[scope-enum]" in e for e in errors("feat(sync,backend): something"))


def test_type_must_be_in_enum() -> None:
    assert any("[type-enum]" in e for e in errors("feature: something"))


def test_type_must_be_lowercase() -> None:
    assert any("[type-case]" in e for e in errors("Feat: something"))


def test_subject_must_be_lowercase_start() -> None:
    assert any("[subject-case]" in e for e in errors("feat(sync): Add a thing"))


def test_subject_must_not_end_with_period() -> None:
    assert any("[subject-full-stop]" in e for e in errors("feat(sync): add a thing."))


def test_subject_may_not_be_empty() -> None:
    assert any("[subject-empty]" in e for e in errors("feat(sync): "))


def test_space_required_after_colon() -> None:
    assert any("[header-format]" in e for e in errors("feat(sync):add a thing"))


def test_plain_message_is_rejected() -> None:
    got = errors("baseline automation")
    assert any("[scope-empty]" in e or "[header-format]" in e for e in got)


def test_empty_message_is_rejected() -> None:
    assert errors("") == ["message is empty [empty]"]


def test_git_comment_lines_are_stripped() -> None:
    assert errors("feat(sync): add a thing\n# Please enter the commit") == []


def test_body_must_follow_blank_line() -> None:
    message = "feat(sync): add a thing\nbody right after header\n"
    assert any("[body-leading-blank]" in e for e in errors(message))
    assert errors("feat(sync): add a thing\n\nbody after blank line\n") == []


def test_header_max_length() -> None:
    header = "feat(sync): " + "x" * 100
    assert any("[header-max-length]" in e for e in errors(header))


def test_main_reads_message_from_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("fix(sync): read from stdin\n"))
    assert check_commit_msg.main([]) == 0


def test_main_fails_on_bad_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", _FakeStdin("nope\n"))
    assert check_commit_msg.main([]) == 1
    assert "[header-format]" in capsys.readouterr().out


def test_main_checks_revision_range(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(check_commit_msg, "_subjects_in", lambda rev: ["feat(sync): one", "bad message"])
    assert check_commit_msg.main(["--range", "a..b"]) == 1
    assert "bad message" in capsys.readouterr().out


class _FakeStdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._text
