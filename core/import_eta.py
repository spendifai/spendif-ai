"""How long an import will take: rows times the seconds per row seen so far.

Plain arithmetic on this computer's own past imports, not a fitted model:
before the start the estimate comes from history (or a first guess when there
is none), during the run it follows the pace actually observed.
"""
from __future__ import annotations

# First guess when nothing was ever imported on this computer. Measured on
# Apple Silicon with Gemma 3 12B (about 4 s per movement); a slower machine
# is corrected as soon as the run shows its real pace.
DEFAULT_SECONDS_PER_ROW = 4.0

# Share of the run after which the observed pace replaces the prior estimate
# completely; before it, the two are blended.
TRUST_OBSERVED_AFTER = 0.10


def seconds_per_row(history: list[tuple[int, float]]) -> float | None:
    """Seconds per row over the given (rows, seconds) imports, None if empty."""
    rows = sum(r for r, _ in history if r > 0)
    secs = sum(s for r, s in history if r > 0)
    return secs / rows if rows else None


def estimate_total(n_rows: int, history: list[tuple[int, float]]) -> tuple[float, bool]:
    """Expected seconds for ``n_rows``, and whether history backed it."""
    rate = seconds_per_row(history)
    if rate is None:
        return n_rows * DEFAULT_SECONDS_PER_ROW, False
    return n_rows * rate, True


def remaining_seconds(elapsed: float, progress: float, prior_total: float) -> float:
    """Seconds left after ``elapsed`` seconds at ``progress`` (0..1).

    Early on the pace is noisy (loading the model, the first file's header),
    so the prior estimate weighs most; from TRUST_OBSERVED_AFTER on, only the
    observed pace counts.
    """
    progress = min(max(progress, 0.0), 1.0)
    if progress >= 1.0:
        return 0.0
    prior_left = max(prior_total - elapsed, 0.0)
    if progress <= 0.0:
        return prior_left
    observed_left = elapsed / progress * (1.0 - progress)
    w = min(progress / TRUST_OBSERVED_AFTER, 1.0)
    return w * observed_left + (1.0 - w) * prior_left


def duration_parts(seconds: float) -> tuple[int, int]:
    """(hours, minutes) rounded up to the minute; under a minute gives (0, 0)."""
    if seconds < 60:
        return 0, 0
    minutes = int(-(-seconds // 60))
    return minutes // 60, minutes % 60
