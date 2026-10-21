"""Combine rule outcomes into one verdict."""

from __future__ import annotations

from steadygate.config import Config
from steadygate.models import GateResult, Outcome, Result, Status, Verdict
from steadygate.rules import Context, History


def evaluate(
    config: Config, results: list[Result], history: History, run_id: str | None = None
) -> GateResult:
    """Run every rule and decide.

    * Any **required** rule FAILs -> REGRESSION
    * else any **required** rule is INCONCLUSIVE -> INCONCLUSIVE
    * else -> PASS

    Optional rules are reported but never change the verdict.
    """
    ctx = Context(history, config.window, config.min_history, run_id)
    outcomes = [rule.evaluate(results, ctx) for rule in config.rules]
    required = [o for o in outcomes if o.required]

    if any(o.outcome is Outcome.FAIL for o in required):
        verdict = Verdict.REGRESSION
    elif any(o.outcome is Outcome.INCONCLUSIVE for o in required):
        verdict = Verdict.INCONCLUSIVE
    else:
        verdict = Verdict.PASS

    return GateResult(
        verdict=verdict,
        outcomes=outcomes,
        errors=[r for r in results if r.status is Status.ERROR],
        total_results=len(results),
    )
