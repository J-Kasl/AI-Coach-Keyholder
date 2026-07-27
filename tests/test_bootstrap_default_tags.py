"""
tests/test_bootstrap_default_tags.py

Repository-wide convention guard, the same kind as
tests/infrastructure/test_clock.py's direct-datetime-call guard: this
test does NOT require any BOOTSTRAP_DEFAULT to be resolved, and it does
not impose a maximum count. Its only job is to keep the debt visible
and consistently documented -- every occurrence of the tag must follow
the agreed structured form, so `grep -r "BOOTSTRAP_DEFAULT"` reliably
finds every pending ownership decision in one pass.

Canonical form (agreed during the "governance vs. bootstrap default"
discussion, Phase 2.7 architecture review):

    # BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
    # Temporary executable value pending an explicit ownership decision.

`owner` may later become something other than `undecided` (e.g.
`user`, `developer`, `architecture`, `system_safety_policy`) once a
classification is actually made -- this test only requires the two
keys to be present, never a specific value for either.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

# Directories never expected to contain BOOTSTRAP_DEFAULT-tagged
# production code (mirrors test_clock.py's own exclusion list).
_EXCLUDED_DIRS = {".venv", "__pycache__", ".git", "docs", "tests"}

_TAG_LINE_RE = re.compile(r"#\s*BOOTSTRAP_DEFAULT\((?P<params>[^)]*)\)\s*:")
_REQUIRED_KEYS = ("owner", "mechanism")


def _iter_python_files() -> list[Path]:
    files = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def _find_tag_lines() -> list[tuple[Path, int, str]]:
    """Returns (file, line_number, raw_line) for every line that looks
    like it is ATTEMPTING to be a BOOTSTRAP_DEFAULT tag (contains the
    literal string), whether or not it matches the strict structured
    form -- so a malformed attempt is reported as a failure, not
    silently skipped."""
    found = []
    for path in _iter_python_files():
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if "BOOTSTRAP_DEFAULT" in line:
                found.append((path, i, line))
    return found


def test_every_bootstrap_default_tag_uses_the_agreed_structured_form() -> None:
    malformed = []
    for path, lineno, line in _find_tag_lines():
        match = _TAG_LINE_RE.search(line)
        if match is None:
            malformed.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: does not match the required "
                              f"'# BOOTSTRAP_DEFAULT(owner=..., mechanism=...):' form -- {line.strip()!r}")
            continue
        params = match.group("params")
        missing = [key for key in _REQUIRED_KEYS if f"{key}=" not in params]
        if missing:
            malformed.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: missing required key(s) "
                              f"{missing} in tag params {params!r}")

    assert not malformed, (
        "Every BOOTSTRAP_DEFAULT tag must use the agreed form "
        "'# BOOTSTRAP_DEFAULT(owner=..., mechanism=...):' with both keys present. "
        "Found malformed tag(s):\n" + "\n".join(malformed)
    )


def test_at_least_the_currently_known_bootstrap_defaults_are_tagged() -> None:
    """
    Not a completeness guarantee (a human/reviewer must still judge new
    constants against the definition) -- but pins the specific set
    identified during the Phase 2.7 architecture review, so a future
    refactor that accidentally deletes a tag (e.g. while renaming a
    constant) is caught immediately rather than silently losing the
    annotation.
    """
    tagged_files = {path for path, _lineno, _line in _find_tag_lines()}
    expected_files = {
        REPO_ROOT / "trust_manager" / "severity.py",
        REPO_ROOT / "trust_manager" / "recalculation.py",
        REPO_ROOT / "penalty_engine" / "window.py",
        REPO_ROOT / "penalty_engine" / "extension.py",
    }
    missing = expected_files - tagged_files
    assert not missing, f"Expected BOOTSTRAP_DEFAULT tags no longer found in: {missing}"


@pytest.mark.parametrize(
    "expected_constant_hint",
    [
        "COOPERATION_SELF_DISCLOSURE_OFFSET",
        "severity_base_weight",
        "MAX_ABS_EFFECTIVE_WEIGHT",
        "CONFIDENCE_K",
        "DEFAULT_BASE_DURATION_HOURS",
        "BASE_HOURS_BY_SEVERITY",
        "REPETITION_INCREMENT_HOURS",
        "MINIMUM_RETAINED_FRACTION",
        "_SELF_DISCLOSED_MITIGATION",
    ],
)
def test_each_known_bootstrap_default_constant_has_a_tag_within_a_few_lines(expected_constant_hint: str) -> None:
    """Loosely verifies the tag actually sits next to the constant it
    documents, not just somewhere in the file -- checks the tag comment
    and the constant definition are within 15 lines of each other (the
    rationale comments here are often several lines long, by design)."""
    for path in _iter_python_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if expected_constant_hint in line and ("=" in line or ":" in line) and "BOOTSTRAP_DEFAULT" not in line:
                window = lines[max(0, i - 15):i]
                if any("BOOTSTRAP_DEFAULT" in w for w in window):
                    return
    pytest.fail(f"No BOOTSTRAP_DEFAULT tag found within 15 lines above a definition of {expected_constant_hint!r}")
