"""Backend i18n helper — generates language-specific strings for API error messages.

Usage: _b("中文", "English", lang) returns the appropriate string.
When lang is empty, returns "中文 / English" (frontend splits on ' / ').
"""
from __future__ import annotations


def _b(zh: str, en: str, lang: str = "") -> str:
    """Bilingual string helper. lang='zh' → Chinese, lang='en' → English,
    otherwise returns '中文 / English' for frontend language splitting."""
    if lang == "zh":
        return zh
    if lang == "en":
        return en
    return f"{zh} / {en}"


def _bf(zh_template: str, en_template: str, lang: str = "", **kwargs) -> str:
    """Format a bilingual template string."""
    if lang == "zh":
        return zh_template.format(**kwargs)
    if lang == "en":
        return en_template.format(**kwargs)
    return f"{zh_template.format(**kwargs)} / {en_template.format(**kwargs)}"
