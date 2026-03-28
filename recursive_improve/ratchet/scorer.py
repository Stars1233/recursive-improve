"""Composite metric scoring for ratchet keep/revert decisions."""

from __future__ import annotations

from recursive_improve.ratchet.config import RatchetConfig


def composite_score(metrics: dict, config: RatchetConfig) -> float:
    """Compute a single scalar score from metrics using configured weights.

    For "minimize" metrics the value is inverted (1 - value) so that
    higher composite scores always mean better performance.
    """
    score = 0.0
    total_weight = 0.0

    for name, spec in config.metrics.items():
        if name not in metrics:
            continue
        value = metrics[name]["value"]
        if spec.direction == "minimize":
            value = 1.0 - value
        score += value * spec.weight
        total_weight += spec.weight

    if total_weight == 0:
        return 0.0
    return round(score / total_weight, 4)
