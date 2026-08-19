"""Regression tests for M5B caption-to-image evidence precision.

Each test names a real R1 failure.  Removing the corresponding hard dimension
check would make that failure incorrectly classify as DIRECT again.
"""

from book_video_factory.semantic_alignment.coverage_precision import (
    CoverageObservation,
    classify_coverage,
)


def observation(**overrides: object) -> CoverageObservation:
    values: dict[str, object] = {
        "who": ("C006",),
        "action": ("execution",),
        "state": ("dead",),
        "relation": (),
        "location": ("execution_ground",),
        "temporal_phase": ("aftermath",),
        "salient_subjects": ("C006",),
        "body_parts": (),
        "concrete_caption": True,
    }
    values.update(overrides)
    return CoverageObservation(**values)


def test_dead_caption_conflicts_with_alive_before_execution_pixels() -> None:
    """Catches removal of STATE/TEMPORAL_PHASE hard-conflict checks."""

    expected = observation()
    observed = observation(state=("alive",), temporal_phase=("before_execution",))

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "CONFLICT"
    assert {finding.dimension for finding in decision.findings if finding.status == "conflict"} >= {
        "state",
        "temporal_phase",
    }


def test_firing_caption_is_not_direct_when_pixels_only_show_aftermath() -> None:
    """Catches treating one general execution event as exact action evidence."""

    expected = observation(action=("rifles_firing",), temporal_phase=("during_event",))
    observed = observation(action=("bodies_on_ground",), temporal_phase=("aftermath",))

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "CONFLICT"
    assert any(
        finding.dimension == "action" and finding.status == "conflict"
        for finding in decision.findings
    )


def test_face_and_forearm_caption_conflicts_with_neck_only_pixels() -> None:
    """Catches collapsing explicit body-part actions into generic self-touching."""

    expected = observation(
        who=("C002",),
        action=("self_check",),
        state=("alive",),
        location=("village_road",),
        temporal_phase=("after_execution",),
        salient_subjects=("C002",),
        body_parts=("face", "forearm"),
    )
    observed = observation(
        who=("C002",),
        action=("self_check",),
        state=("alive",),
        location=("village_road",),
        temporal_phase=("after_execution",),
        salient_subjects=("C002",),
        body_parts=("neck",),
    )

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "CONFLICT"
    assert any(
        finding.dimension == "body_parts" and finding.status == "conflict"
        for finding in decision.findings
    )


def test_named_subject_hidden_in_crowd_is_not_direct() -> None:
    """Catches presence-without-normal-scale-identifiability passing as DIRECT."""

    expected = observation(
        who=("C002",),
        action=("watch_execution",),
        state=("alive",),
        temporal_phase=("before_execution",),
        salient_subjects=("C002",),
    )
    observed = observation(
        who=("C002",),
        action=("watch_execution",),
        state=("alive",),
        temporal_phase=("before_execution",),
        salient_subjects=(),
    )

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "UNCERTAIN"
    assert any(
        finding.dimension == "salience" and finding.status == "missing"
        for finding in decision.findings
    )


def test_all_caption_critical_dimensions_are_required_for_direct() -> None:
    expected = observation(
        relation=("C006_to_C002",),
        body_parts=("face", "forearm"),
    )

    decision = classify_coverage(expected, expected)

    assert decision.coverage == "DIRECT"
    assert all(finding.status == "match" for finding in decision.findings)


def test_nonconcrete_caption_with_support_and_no_conflict_is_supported() -> None:
    expected = observation(
        who=("C002", "C003", "C004", "C005"),
        action=(),
        state=(),
        temporal_phase=(),
        salient_subjects=(),
        concrete_caption=False,
    )
    observed = observation(
        who=("C002", "C003", "C004", "C005"),
        action=("family_together",),
        state=("stable",),
        temporal_phase=("ongoing",),
        salient_subjects=("C002", "C003", "C004", "C005"),
        concrete_caption=False,
    )

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "SUPPORTED"


def test_one_of_two_named_people_missing_is_uncertain_not_hard_identity_conflict() -> None:
    """Catches participant incompleteness being mislabeled as a wrong identity."""

    expected = observation(
        who=("C002", "C006"),
        action=("relationship_encounter",),
        relation=("C006_to_C002",),
        salient_subjects=("C002", "C006"),
    )
    observed = observation(
        who=("C006",),
        action=("relationship_encounter",),
        relation=("C006_to_C002",),
        salient_subjects=("C006",),
    )

    decision = classify_coverage(expected, observed)

    assert decision.coverage == "UNCERTAIN"
    assert any(f.dimension == "who" and f.status == "missing" for f in decision.findings)
