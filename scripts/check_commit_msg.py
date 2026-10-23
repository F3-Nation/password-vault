#!/usr/bin/env python3
"""Conventional-commits message checker — this repo's commitlint, without Node.

f3-nation enforces commit messages with commitlint (Node); this repo stays
Python-only, so the same rules live here in stdlib Python and are enforced two
ways, mirroring f3-nation's setup:

* **locally** — a pre-commit ``commit-msg`` hook (``.pre-commit-config.yaml``)
  runs this file on every ``git commit``;
* **in CI** — ``.github/workflows/commitlint.yml`` lints the PR title (what
  release-please reads after a squash-merge) and every commit subject in the
  PR's range.

Rules (equivalent to ``@commitlint/config-conventional`` plus f3-nation's
scope overrides):

* header must be ``type(scope): subject`` and at most 100 characters
* ``type`` must be one of: build, chore, ci, docs, feat, fix, perf, refactor,
  revert, style, test (lowercase)
* ``scope`` is required and each comma-separated part must be one of: main,
  sync, infra, ci, scripts, docs, deps, repo, release — ``main`` exists
  because release-please titles use the branch name
* subject must be non-empty, must not end with ``.``, must not start uppercase
* a body must be preceded by a blank line
* git-generated messages (``Merge …``, ``Revert "…"``, ``fixup!``/``squash!``/
  ``amend!``) are ignored

Usage:
    python3 scripts/check_commit_msg.py .git/COMMIT_EDITMSG   # pre-commit passes this
    printf '%s\\n' "$PR_TITLE" | python3 scripts/check_commit_msg.py   # PR title (CI)
    python3 scripts/check_commit_msg.py --range origin/main..HEAD       # commit subjects
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TYPES: tuple[str, ...] = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)
SCOPES: tuple[str, ...] = ("main", "sync", "infra", "ci", "scripts", "docs", "deps", "repo", "release")
MAX_HEADER_LENGTH = 100
IGNORED_PREFIXES: tuple[str, ...] = ("Merge ", 'Revert "', "fixup! ", "squash! ", "amend! ")

# Loosely structured header; each part is validated (and reported) separately
# so the user gets precise rule names instead of one opaque "bad format".
_HEADER_RE = re.compile(
    r"^(?P<type>[A-Za-z]*)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?(?P<colon>:\s*)(?P<subject>.*)$"
)


def _message_lines(message: str) -> list[str]:
    """Split into lines, dropping git's ``#`` comment lines."""
    return [line for line in message.splitlines() if not line.startswith("#")]


def _check_header(header: str) -> list[str]:
    errors: list[str] = []
    if len(header) > MAX_HEADER_LENGTH:
        errors.append(f"header must be at most {MAX_HEADER_LENGTH} characters [header-max-length]")

    match = _HEADER_RE.match(header)
    if match is None:
        errors.append('header must look like "type(scope): subject" [header-format]')
        return errors

    type_ = match.group("type")
    scope = match.group("scope")
    colon = match.group("colon")
    subject = match.group("subject").strip()

    if not colon.startswith(": ") and subject:
        errors.append('header must look like "type(scope): subject" — space after the colon [header-format]')
        return errors

    if not type_:
        errors.append("type may not be empty [type-empty]")
    elif type_ not in TYPES:
        if type_.lower() in TYPES:
            errors.append(f"type must be lowercase, e.g. {type_.lower()!r} [type-case]")
        else:
            errors.append(f"type must be one of: {', '.join(TYPES)} [type-enum]")

    if scope is None or not scope.strip():
        errors.append(f"scope may not be empty; use one of: {', '.join(SCOPES)} [scope-empty]")
    else:
        for part in scope.split(","):
            part = part.strip()
            if part not in SCOPES:
                errors.append(f"scope must be one of: {', '.join(SCOPES)} (got {part!r}) [scope-enum]")

    if not subject:
        errors.append("subject may not be empty [subject-empty]")
    else:
        if subject.endswith("."):
            errors.append("subject may not end with '.' [subject-full-stop]")
        if subject[0].isupper():
            errors.append("subject must not start with an uppercase letter [subject-case]")

    return errors


def check_message(message: str) -> list[str]:
    """Return commitlint-style violations for a full commit message."""
    lines = _message_lines(message)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ["message is empty [empty]"]

    header = lines[0].strip()
    if header.startswith(IGNORED_PREFIXES):
        return []

    errors = _check_header(header)
    if len(lines) > 1 and lines[1].strip() and any(line.strip() for line in lines[1:]):
        errors.append("body must be preceded by a blank line [body-leading-blank]")
    return errors


def _subjects_in(rev_range: str) -> list[str]:
    proc = subprocess.run(
        ["git", "log", "--format=%s", rev_range],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git log {rev_range} failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _label(text: str) -> str:
    lines = [line for line in _message_lines(text) if line.strip()]
    return lines[0].strip() if lines else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check commit messages against Conventional Commits.")
    parser.add_argument(
        "message_file",
        nargs="?",
        help="file containing the commit message (passed by the commit-msg hook)",
    )
    parser.add_argument(
        "--range",
        dest="rev_range",
        metavar="REV_RANGE",
        help="check every commit subject in a git revision range, e.g. origin/main..HEAD",
    )
    args = parser.parse_args(argv)

    items: list[tuple[str, str]] = []
    if args.rev_range:
        items = [(subject, subject) for subject in _subjects_in(args.rev_range)]
    elif args.message_file:
        text = Path(args.message_file).read_text(encoding="utf-8", errors="replace")
        items = [(text, _label(text))]
    else:
        if sys.stdin.isatty():
            parser.error("provide a message file or pipe the message on stdin")
        text = sys.stdin.read()
        items = [(text, _label(text))]

    failed = False
    for message, label in items:
        errors = check_message(message)
        if not errors:
            continue
        failed = True
        print(f"input: {label}")
        for error in errors:
            print(f"\u2716 {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
