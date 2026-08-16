"""ManySight is the only project name in the tracked tree.

This is a regression, not a formality: the previous name reached into
environment variables, HTTP headers, the SDK module, localStorage keys, a
persisted enum value and the database filename, so a partial revert is easy to
make and hard to notice. One test over `git ls-files` catches it.

The forbidden token is assembled at runtime rather than written out, because a
test that hard-codes it would itself be a match and would make the repository
audit permanently fail. That is the one construction the acceptance criteria
sanctions; nothing else here weakens the check — the scan covers every tracked
file, in any case, in any of the separator spellings.
"""
from __future__ import annotations

import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_HEAD, _TAIL = "stor" + "e", "lens"
# The separators the retired name was actually written with. A space is
# deliberately not one of them: it was never a two-word brand, and allowing one
# would flag ordinary English in which the two words happen to sit next to each
# other (see the negative examples below).
_SEPARATORS = ("", "_", "-", ".")
_PATTERN = _HEAD + "[_.\\-]?" + _TAIL
LEGACY = re.compile(_PATTERN, re.IGNORECASE)
# Byte-level scan, so a file that is not valid UTF-8 still gets checked.
LEGACY_BYTES = re.compile(_PATTERN.encode(), re.IGNORECASE)


def spelling(separator: str = "", case=str) -> str:
    """One way the retired name was written, assembled rather than spelled out."""
    return case(_HEAD + separator + _TAIL)


def tracked_files() -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                            capture_output=True, check=True)
    return [name for name in result.stdout.decode().split("\0") if name]


def test_the_audit_would_actually_catch_the_old_name():
    """Guard against a matcher that passes because it matches nothing."""
    candidates = [spelling(separator, case)
                  for separator in _SEPARATORS
                  for case in (str, str.lower, str.upper, str.title)]
    assert len(set(candidates)) >= 12
    for candidate in candidates:
        assert LEGACY.search(candidate), candidate
        assert LEGACY_BYTES.search(candidate.encode()), candidate
    # Ordinary English that merely contains both words must not be flagged.
    for allowed in ("ManySight", "manysight_managed", "a store and a lens",
                    "restore the lenses"):
        assert not LEGACY.search(allowed), allowed


def test_no_tracked_file_content_mentions_the_previous_project_name():
    offenders = []
    for name in tracked_files():
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            content = handle.read()
        for match in LEGACY_BYTES.finditer(content):
            line = content.count(b"\n", 0, match.start()) + 1
            offenders.append(f"{name}:{line}: {match.group().decode('utf-8', 'replace')}")
    assert offenders == [], "tracked files still name the previous project:\n" + \
        "\n".join(offenders[:40])


def test_no_tracked_path_contains_the_previous_project_name():
    offenders = [name for name in tracked_files() if LEGACY.search(name)]
    assert offenders == [], f"tracked paths still name the previous project: {offenders}"


def test_the_project_names_itself_consistently():
    """The replacement is present, not merely the old name absent."""
    from server import app as server_app
    from server import db

    assert server_app.app.title == "ManySight"
    assert os.path.basename(db.DB_PATH) == "manysight.db"
    assert db.DATA_DIR == os.environ.get("MANYSIGHT_DATA", os.path.join(ROOT, "data"))
