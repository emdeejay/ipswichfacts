"""
Extract place mentions (streets, suburbs) from free text so records can be
cross-linked. Deliberately simple: a gazetteer of known street/suburb names
matched case-insensitively as whole tokens.

Usage:
    from scrape.extract_mentions import Extractor
    ex = Extractor(streets=["Gordon Street", ...], suburbs=["Ipswich", ...])
    ex.find("...the Gordon Street underpass in Ipswich...")
    # → [{'kind': 'street', 'value': 'Gordon Street'},
    #    {'kind': 'suburb', 'value': 'Ipswich'}]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass
class Extractor:
    streets: list[str]
    suburbs: list[str]

    def __post_init__(self) -> None:
        # Sort longer first so "Old Toowoomba Road" wins over "Toowoomba Road".
        self._street_re = _make_pattern(self.streets)
        self._suburb_re = _make_pattern(self.suburbs)

    def find(self, text: str | None) -> list[dict[str, str]]:
        if not text:
            return []
        found: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for m in self._street_re.finditer(text):
            key = ("street", m.group(0).title())
            if key not in seen:
                seen.add(key)
                found.append({"kind": key[0], "value": key[1]})
        for m in self._suburb_re.finditer(text):
            key = ("suburb", m.group(0).title())
            if key not in seen:
                seen.add(key)
                found.append({"kind": key[0], "value": key[1]})
        return found


def _make_pattern(names: Iterable[str]) -> re.Pattern[str]:
    parts = sorted({n.strip() for n in names if n}, key=len, reverse=True)
    escaped = [re.escape(n) for n in parts]
    if not escaped:
        return re.compile(r"(?!)")  # never matches
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)
