from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


MANDARIN_ASR_CANONICALIZATIONS = (
    ("训养", "驯养"),
    ("战友", "占有"),
    ("严不由衷", "言不由衷"),
    ("照过玻璃罩", "罩过玻璃罩"),
)


def normalize_transcript(value: str) -> str:
    normalized = "".join(
        re.findall(
            r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]+",
            str(value).lower(),
        )
    )
    for observed, canonical in MANDARIN_ASR_CANONICALIZATIONS:
        normalized = normalized.replace(observed, canonical)
    return normalized.translate(str.maketrans({"她": "他", "它": "他"}))


def compare_transcript(
    intended: str,
    transcribed: str,
    *,
    minimum_similarity: float = 0.96,
    maximum_length_delta_share: float = 0.08,
) -> dict[str, Any]:
    source = normalize_transcript(intended)
    actual = normalize_transcript(transcribed)
    if not source:
        raise ValueError("intended transcript is empty")
    similarity = SequenceMatcher(None, source, actual, autojunk=False).ratio()
    length_delta_share = abs(len(source) - len(actual)) / len(source)
    passed = bool(
        actual
        and similarity >= minimum_similarity
        and length_delta_share <= maximum_length_delta_share
    )
    return {
        "status": "pass" if passed else "fail",
        "similarity": round(similarity, 6),
        "length_delta_share": round(length_delta_share, 6),
        "minimum_similarity": minimum_similarity,
        "maximum_length_delta_share": maximum_length_delta_share,
        "intended_normalized": source,
        "transcribed_normalized": actual,
    }
