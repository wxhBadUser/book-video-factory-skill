"""Evidence-precise caption-to-image coverage classification.

The classifier accepts observations of actual pixels.  It deliberately knows
nothing about prompts or loose event similarity: a concrete caption is DIRECT
only when every caption-critical visible dimension matches.
"""

from __future__ import annotations

from dataclasses import dataclass

COVERAGE_TYPES = ("DIRECT", "SUPPORTED", "CONFLICT", "UNCERTAIN")


def _clean(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass(frozen=True)
class CoverageObservation:
    who: tuple[str, ...] = ()
    action: tuple[str, ...] = ()
    state: tuple[str, ...] = ()
    relation: tuple[str, ...] = ()
    location: tuple[str, ...] = ()
    temporal_phase: tuple[str, ...] = ()
    salient_subjects: tuple[str, ...] = ()
    body_parts: tuple[str, ...] = ()
    concrete_caption: bool = True

    def __post_init__(self) -> None:
        for name in (
            "who",
            "action",
            "state",
            "relation",
            "location",
            "temporal_phase",
            "salient_subjects",
            "body_parts",
        ):
            object.__setattr__(self, name, _clean(getattr(self, name)))


@dataclass(frozen=True)
class DimensionFinding:
    dimension: str
    status: str
    expected: tuple[str, ...]
    observed: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CoverageDecision:
    coverage: str
    findings: tuple[DimensionFinding, ...]


def _set_finding(
    dimension: str,
    expected: tuple[str, ...],
    observed: tuple[str, ...],
) -> DimensionFinding:
    wanted = set(expected)
    seen = set(observed)
    if not wanted:
        return DimensionFinding(dimension, "not_required", expected, observed, "caption does not require this dimension")
    if wanted <= seen:
        return DimensionFinding(dimension, "match", expected, observed, "all caption-critical values are visibly present")
    if wanted & seen:
        status = "conflict" if dimension in {"action", "state", "relation", "location", "temporal_phase", "body_parts"} else "missing"
        return DimensionFinding(
            dimension,
            status,
            expected,
            observed,
            "some caption-critical values are missing from the observed pixels",
        )
    return DimensionFinding(
        dimension,
        "conflict",
        expected,
        observed,
        "one or more caption-critical values are absent or contradicted",
    )


def classify_coverage(
    expected: CoverageObservation,
    observed: CoverageObservation,
) -> CoverageDecision:
    """Classify one image using seven hard semantic dimensions plus body action."""

    findings = [
        _set_finding("who", expected.who, observed.who),
        _set_finding("action", expected.action, observed.action),
        _set_finding("state", expected.state, observed.state),
        _set_finding("relation", expected.relation, observed.relation),
        _set_finding("location", expected.location, observed.location),
        _set_finding("temporal_phase", expected.temporal_phase, observed.temporal_phase),
        _set_finding("body_parts", expected.body_parts, observed.body_parts),
    ]

    required_salience = expected.salient_subjects if expected.concrete_caption else ()
    salient = _set_finding("salience", required_salience, observed.salient_subjects)
    if salient.status in {"conflict", "missing"}:
        salient = DimensionFinding(
            "salience",
            "missing",
            salient.expected,
            salient.observed,
            "named subject is not identifiable at normal viewing scale",
        )
    findings.append(salient)

    if any(item.status == "conflict" for item in findings):
        coverage = "CONFLICT"
    elif any(item.status == "missing" for item in findings):
        coverage = "UNCERTAIN"
    elif not expected.concrete_caption:
        coverage = "SUPPORTED"
    elif all(item.status in {"match", "not_required"} for item in findings):
        coverage = "DIRECT"
    else:
        coverage = "UNCERTAIN"
    return CoverageDecision(coverage=coverage, findings=tuple(findings))


__all__ = [
    "COVERAGE_TYPES",
    "CoverageDecision",
    "CoverageObservation",
    "DimensionFinding",
    "classify_coverage",
]
