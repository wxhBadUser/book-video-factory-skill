"""原创性与泄漏检测。

用途：把候选口播稿与「参考创作者转录稿 + 用户满意稿」做连续字符比对，
防止无意识复制参考创作者的固定话术、比喻或句式。

设计约束：
- 只做重合检测，不做任何质量判断。质量由 blind_review 负责。
- 空语料视为「没有检测」，直接抛错，绝不静默通过（fail-closed）。
- 阈值 MAX_CONSECUTIVE_CHARS：连续重合 **超过** 该字数即判 blocking。
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Mapping

__all__ = [
    "MAX_CONSECUTIVE_CHARS",
    "OriginalityError",
    "check_originality",
    "normalize",
]

MAX_CONSECUTIVE_CHARS = 20

_ANNOT = re.compile(r"【[^】]*】")
_WS = re.compile(r"\s+")
_MD = re.compile(r"^\s*(#+|>)\s*", re.MULTILINE)


class OriginalityError(RuntimeError):
    """原创性检测无法执行，或在 raise 模式下检出泄漏。"""


def normalize(text: str) -> str:
    """去掉 markdown 结构、功能标注与所有空白，只留可比对的正文字符。"""
    text = _MD.sub("", text or "")
    text = _ANNOT.sub("", text)
    return _WS.sub("", text)


def _ngrams(text: str, n: int) -> set[str]:
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _find_overlaps(candidate: str, corpus: str, n: int) -> list[tuple[int, int]]:
    """返回候选稿中与语料重合、长度 >= n 的最大区间 [(start, length), ...]。"""
    grams = _ngrams(corpus, n)
    if not grams:
        return []
    hits = [i for i in range(len(candidate) - n + 1) if candidate[i:i + n] in grams]
    if not hits:
        return []
    spans: list[tuple[int, int]] = []
    run_start = prev = hits[0]
    for i in hits[1:]:
        if i == prev + 1:
            prev = i
            continue
        spans.append((run_start, prev - run_start + n))
        run_start = prev = i
    spans.append((run_start, prev - run_start + n))
    return spans


def check_originality(
    candidate_text: str,
    corpus: Mapping[str, str],
    *,
    max_consecutive: int = MAX_CONSECUTIVE_CHARS,
    raise_on_violation: bool = False,
    corpus_labels: Mapping[str, str] | None = None,
) -> dict:
    """比对候选稿与语料，返回原创性报告。

    corpus: {source_id: text}，至少一项，否则抛 OriginalityError。
    """
    if not corpus:
        raise OriginalityError(
            "原创性检测语料为空：没有参考转录稿或满意稿可比对，"
            "此时不得判定通过（fail-closed）。"
        )

    cand = normalize(candidate_text)
    if not cand:
        raise OriginalityError("候选稿正文为空，无法执行原创性检测。")

    n = max_consecutive + 1  # 严格「超过」阈值才算违规
    violations: list[dict] = []
    per_source: dict[str, dict] = {}

    for source_id, raw in corpus.items():
        src = normalize(raw)
        spans = _find_overlaps(cand, src, n) if src else []
        longest = max((ln for _, ln in spans), default=0)
        per_source[source_id] = {
            "source_id": source_id,
            "label": (corpus_labels or {}).get(source_id, source_id),
            "normalized_chars": len(src),
            "longest_overlap": longest,
            "overlap_count": len(spans),
        }
        for start, length in spans:
            violations.append({
                "source_id": source_id,
                "label": (corpus_labels or {}).get(source_id, source_id),
                "start": start,
                "length": length,
                "excerpt": cand[start:start + min(length, 60)],
            })

    violations.sort(key=lambda v: v["length"], reverse=True)
    report = {
        "schema_version": "originality-report.v1",
        "max_consecutive_chars": max_consecutive,
        "candidate_chars": len(cand),
        "candidate_sha256": hashlib.sha256(cand.encode("utf-8")).hexdigest(),
        "corpus_sources": list(corpus.keys()),
        "corpus_hashes": {
            source_id: hashlib.sha256(normalize(raw).encode("utf-8")).hexdigest()
            for source_id, raw in corpus.items()
        },
        "per_source": per_source,
        "longest_overlap": max((v["length"] for v in violations), default=0),
        "violations": violations,
        "passed": not violations,
    }
    if violations and raise_on_violation:
        top = violations[0]
        raise OriginalityError(
            f"检出连续重合 {top['length']} 字（来源 {top['source_id']}）："
            f"{top['excerpt'][:40]}…"
        )
    return report


def build_corpus(paths: Iterable[tuple[str, "object"]]) -> dict[str, str]:
    """从 (source_id, path) 读取语料；缺失文件不静默跳过，直接抛错。"""
    from pathlib import Path

    out: dict[str, str] = {}
    for source_id, p in paths:
        path = Path(str(p))
        if not path.is_file():
            raise OriginalityError(f"原创性语料缺失：{source_id} -> {path}")
        out[source_id] = path.read_text(encoding="utf-8")
    return out
