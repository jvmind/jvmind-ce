"""JVM 命令行参数校验：对照 jdk_args_ref 中的官方 flag 列表检查参数是否存在。"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

_RE_FLAG = re.compile(
    r"^\s*(?P<type>\S+)\s+(?P<name>\S+)\s+(?P<op>:=|=)\s+"
    r"(?P<value>\S*)\s+\{(?P<category>[^}]+)\}"
    r"(?:\s*\{(?P<origin>[^}]+)\})?\s*$"
)

_VALID_VERSIONS = (8, 11, 17, 21, 25)

_REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jdk_args_ref")

# Common -X flag aliases → internal -XX flag names.
# Sorted by length descending so longer aliases match before shorter prefixes.
_X_ALIASES = sorted([
    ("Xmx", "MaxHeapSize"),
    ("Xms", "InitialHeapSize"),
    ("Xss", "ThreadStackSize"),
    ("Xmn", "NewSize"),
    ("Xint", "InterpretedMode"),
    ("Xbatch", "BackgroundCompilation"),
], key=lambda x: -len(x[0]))

_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}


def _parse_flags_file(version: int) -> Dict[str, Dict[str, Any]]:
    filepath = os.path.join(_REF_DIR, f"jdk{version}-flags.txt")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"JDK flags reference file not found: {filepath}")
    flags: Dict[str, Dict[str, Any]] = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            m = _RE_FLAG.match(line)
            if m:
                name = m.group("name")
                flags[name] = {
                    "type": m.group("type").strip(),
                    "value": m.group("value").strip(),
                    "op": m.group("op"),
                    "category": m.group("category").strip(),
                    "origin": (m.group("origin") or "").strip(),
                }
    return flags


def _load(version: int) -> Dict[str, Dict[str, Any]]:
    if version not in _cache:
        _cache[version] = _parse_flags_file(version)
    return _cache[version]


def _normalize_flag_name(raw: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Normalize a JVM flag name, resolving aliases and extracting embedded values.

    Returns ``(lookup_name, display_name, extracted_value)``.
    ``display_name`` differs from ``lookup_name`` only when an alias is resolved,
    in which case it shows the original alias for the output.
    """
    name = raw.lstrip("-")

    # Strip -XX: prefix and optional +/- bool toggle
    if name.startswith("XX:"):
        name = name[3:]
    if name.startswith("+") or name.startswith("-"):
        name = name[1:]

    # Check -X aliases (sorted longest-first above)
    for alias, internal in _X_ALIASES:
        if name == alias:
            return internal, raw, None
        if name.startswith(alias):
            return internal, raw, name[len(alias):] or None

    return name, raw, None


def _suggest_similar(name: str, known: Set[str], limit: int = 3) -> List[str]:
    """Return up to `limit` similar flag names using bigram Jaccard similarity."""
    name_lower = name.lower()
    candidates: List[Tuple[float, str]] = []
    name_bigrams = {name_lower[i:i+2] for i in range(len(name_lower)-1)}
    for k in known:
        kl = k.lower()
        bigrams_k = {kl[i:i+2] for i in range(len(kl)-1)}
        union = name_bigrams | bigrams_k
        if not union:
            continue
        intersection_size = len(name_bigrams & bigrams_k)
        jaccard = intersection_size / len(union)
        if jaccard > 0.3:
            candidates.append((jaccard, k))
    candidates.sort(key=lambda x: (-x[0], len(x[1])))
    return [c[1] for c in candidates[:limit]]


def _check_value_type(flag_type: str, value: str) -> Optional[str]:
    """Return None if value type is compatible, or an error message string."""
    value = value.strip()
    if not value:
        return None
    flag_type_lower = flag_type.lower()
    if flag_type_lower in ("bool",):
        if value.lower() not in ("true", "false", "+", "-"):
            return f"expected bool (true/false/+/-), got '{value}'"
    elif flag_type_lower in ("intx", "uintx", "int", "uint", "uint64_t", "size_t"):
        cleaned = value.lower().rstrip("kmg")
        if not cleaned.lstrip("-").isdigit():
            return f"expected integer [{flag_type}], got '{value}'"
    elif flag_type_lower in ("double",):
        try:
            float(value)
        except ValueError:
            return f"expected double, got '{value}'"
    elif flag_type_lower in ("ccstr", "ccstrlist"):
        pass
    return None


def validate_jvm_args(input_str: str) -> str:
    """Validate JVM flags against the JDK version reference.

    Input format: ``jdk_version,flag1[=value1][,flag2[=value2],...]``
    Example: ``17,G1HeapRegionSize=4m,MaxHeapSize=8g,UseG1GC``
    """
    parts = [p.strip() for p in input_str.split(",")]
    parts = [p for p in parts if p]

    if not parts:
        return (
            "Error: requires JDK major version and at least one flag name.\n"
            "Usage: jdk_version,flag1[=value1][,flag2[=value2],...]\n"
            "Example: 17,G1HeapRegionSize=4m,MaxHeapSize=8g"
        )

    try:
        version = int(parts[0])
    except ValueError:
        return (
            f"Error: invalid JDK version '{parts[0]}'.\n"
            f"Supported versions: {', '.join(str(v) for v in _VALID_VERSIONS)}"
        )

    if version not in _VALID_VERSIONS:
        return (
            f"Error: JDK {version} is not supported.\n"
            f"Supported versions: {', '.join(str(v) for v in _VALID_VERSIONS)}"
        )

    try:
        flags = _load(version)
    except FileNotFoundError:
        return f"Error: reference file for JDK {version} not found."

    flag_parts = parts[1:]
    if not flag_parts:
        return (
            f"Error: at least one flag name is required.\n"
            f"Usage: {version},flag1[=value1][,flag2[=value2],...]\n"
            f"Example: {version},G1HeapRegionSize=4m,MaxHeapSize=8g"
        )

    items: List[Tuple[str, Optional[str], str]] = []
    # Each item: (lookup_name, value_or_None, display_name)
    for fp in flag_parts:
        if "=" in fp:
            raw_name, value = fp.split("=", 1)
            lookup, display, embedded = _normalize_flag_name(raw_name.strip())
            actual_value = value.strip()
            items.append((lookup, actual_value, display))
        else:
            lookup, display, embedded = _normalize_flag_name(fp.strip())
            items.append((lookup, embedded, display))

    lines: List[str] = []
    lines.append(f"## JVM Flag Validation for JDK {version}")
    lines.append("")

    valid_count = 0
    missing_count = 0
    type_errors = 0

    for lookup_name, value, display_name in items:
        if not lookup_name:
            continue
        info = flags.get(lookup_name)
        if info is not None:
            valid_count += 1
            ftype = info["type"]
            fval = info["value"]
            fop = info["op"]
            fcat = info["category"]
            forig = info["origin"]

            if display_name != lookup_name:
                buf = f"  [OK] {display_name}  ->  {lookup_name}"
            else:
                buf = f"  [OK] {lookup_name}"
            if value is not None:
                buf += f"={value}"
            buf += f"    {ftype}  {fop} {fval}  {{{fcat}}}"
            if forig:
                buf += f" {{{forig}}}"
            if forig == "ergonomic":
                buf += "  (ergonomic: overridden at startup)"
            lines.append(buf)

            if value is not None:
                err = _check_value_type(ftype, value)
                if err:
                    type_errors += 1
                    lines.append(f"    -> [TYPE MISMATCH] {err}")
                else:
                    lines.append(f"    -> [OK] type matches")
        else:
            missing_count += 1
            buf = f"  [MISS] {display_name}"
            if value is not None:
                buf += f"={value}"
            similar = _suggest_similar(lookup_name, set(flags.keys()))
            if similar:
                buf += f"    Did you mean: {', '.join(similar)}?"
            else:
                buf += f"    NOT FOUND in JDK {version}"
            lines.append(buf)

    lines.append("")
    lines.append("---")
    summary = f"Summary: {valid_count} valid, {missing_count} not found"
    if type_errors:
        summary += f", {type_errors} type mismatch(es)"
    lines.append(summary)

    return "\n".join(lines)
