"""Automatic log format detection by scoring a sample of lines against each parser."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from logslice.errors import FormatDetectionError
from logslice.parsers import PARSERS, ParseError, get_parser

DETECTION_THRESHOLD = 0.5


@dataclass
class Detection:
    format: str
    scores: dict[str, float] = field(default_factory=dict)
    sampled: int = 0


def score_formats(lines: Sequence[bytes]) -> dict[str, float]:
    """Return the fraction of sampled lines each known parser accepts."""
    scores: dict[str, float] = {}
    for name in PARSERS:
        parser = get_parser(name)
        ok = 0
        for line in lines:
            try:
                parser.parse(line)
            except ParseError:
                continue
            ok += 1
        scores[name] = ok / len(lines) if lines else 0.0
    return scores


def detect_format(lines: Sequence[bytes]) -> Detection:
    """Pick the best-scoring format; fail if no format reaches the threshold."""
    scores = score_formats(lines)
    best = max(scores, key=lambda name: scores[name])
    if not lines or scores[best] < DETECTION_THRESHOLD:
        detail = ", ".join(f"{n}: {s:.0%}" for n, s in scores.items())
        raise FormatDetectionError(
            f"could not detect the log format ({detail}). "
            f"Specify it with --format {{{','.join(PARSERS)}}}."
        )
    return Detection(format=best, scores=scores, sampled=len(lines))
