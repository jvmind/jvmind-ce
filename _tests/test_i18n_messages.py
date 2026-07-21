"""i18n consistency guard for backend user-facing messages.

Convention (see CONVENTIONS.md): user-facing error messages are bilingual
"中文 / English" and the frontend i18nText() splits on " / " (space-slash-space).
This test scans HTTPException messages and dict error returns to ensure any
message containing CJK characters also carries the " / " separator, preventing
zh-only messages and the "。/" (missing-space) split bug.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = [_ROOT / "app", _ROOT / "react_agent"]

_CJK = re.compile(r"[\u4e00-\u9fff]")
# HTTPException(<code>, "<msg>") and dict-style error/detail/message/reason = "<msg>"
# (?:\\.|[^"\\])* tolerates escaped quotes inside the string literal.
_PATTERNS = [
    re.compile(r'''HTTPException\(\s*\d+\s*,\s*"((?:\\.|[^"\\])*)"''', re.S),
    re.compile(r"""HTTPException\(\s*\d+\s*,\s*'((?:\\.|[^'\\])*)'""", re.S),
    re.compile(r'''(?:error|detail|message|reason)["']?\s*[:=]\s*"((?:\\.|[^"\\])*)"''', re.S),
    re.compile(r"""(?:error|detail|message|reason)["']?\s*[:=]\s*'((?:\\.|[^'\\])*)'""", re.S),
]

# Known acceptable exceptions: pure-symbol / non-user-facing internal strings.
_ALLOW_SUBSTR = (
    # add intentional exceptions here if ever needed
)


def _iter_messages():
    for base in _SCAN_DIRS:
        for py in base.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            src = py.read_text(encoding="utf-8", errors="replace")
            for pat in _PATTERNS:
                for m in pat.finditer(src):
                    msg = m.group(1)
                    if not msg:
                        continue
                    line = src[: m.start()].count("\n") + 1
                    yield (py.relative_to(_ROOT).as_posix(), line, msg)


def test_user_facing_messages_are_bilingual():
    offenders = []
    for path, line, msg in _iter_messages():
        if not _CJK.search(msg):
            continue
        if " / " in msg:
            continue
        if any(s in msg for s in _ALLOW_SUBSTR):
            continue
        offenders.append(f"{path}:{line}  {msg[:60]}")
    assert not offenders, "zh-only user-facing messages (need ' / English'):\n" + "\n".join(offenders)


def test_no_missing_space_bilingual_separator():
    """Catch the '。/ English' bug: CJK immediately followed by '/English'
    without the surrounding spaces i18nText expects."""
    offenders = []
    bad_sep = re.compile(r"[\u4e00-\u9fff][。，；：]?/\s*[A-Za-z]")
    for path, line, msg in _iter_messages():
        if " / " in msg:
            continue
        if bad_sep.search(msg):
            offenders.append(f"{path}:{line}  {msg[:60]}")
    assert not offenders, "bilingual messages with missing-space separator:\n" + "\n".join(offenders)
