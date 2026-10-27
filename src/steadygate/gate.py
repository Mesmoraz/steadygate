"""Combine rule outcomes into one verdict."""

from __future__ import annotations

from steadygate.config import Config
from steadygate.models import GateResult, Outcome, Result, RuleOutcome, Status, Verdict
from steadygate.rules import Context, History

_MAX_MISSING_SHOWN = 10


def _missing_outcome(missing: list[str], total: int, label: str) -> RuleOutcome:
    shown = ", ".join(missing[:_MAX_MISSING_SHOWN])
    more = " ..." if len(missing) > _MAX_MISSING_SHOWN else ""
    return RuleOutcome(
        "coverage",
        "*",
        True,
        Outcome.INCONCLUSIVE,
        f"{len(missing)}/{total} {label} case(s) missing: {shown}{more}",
    )


def coverage(config: Config, results: list[Result]) -> RuleOutcome | None:
    """Check every expected case reported every metric a required rule reads.

    A case that crashed before writing its result, or a metric a run quietly stopped
    emitting, otherwise just vanishes from the averages and the run looks green. Errored
    and skipped results count as reported: the rules already deal with those.
    """
    if config.expected_cases is None:
        return None
    metrics = sorted({rule.metric for rule in config.rules if rule.required})
    reported = {(r.case, r.metric) for r in results}
    cases_seen = {r.case for r in results}

    missing: list[str] = []
    for case in config.expected_cases:
        if case not in cases_seen:
            missing.append(case)  # never ran, or crashed before reporting anything
            continue
        absent = [m for m in metrics if (case, m) not in reported]
        if absent:
            missing.append(f"{case} [{', '.join(absent)}]")  # ran, but dropped a metric

    total = len(config.expected_cases)
    if not missing:
        return RuleOutcome(
            "coverage", "*", True, Outcome.PASS, f"all {total}/{total} expected case(s) reported"
        )
    return _missing_outcome(missing, total, "expected")


def baseline_coverage(results: list[Result], ctx: Context) -> RuleOutcome | None:
    """Without ``expected_cases``, expect every case that every baseline run reported.

    Only kicks in once the baseline has ``min_history`` runs. A case missing from even one
    of them proves nothing about the candidate, so it isn't required.
    """
    runs = ctx.history.recent_cases(ctx.window, exclude_run=ctx.run_id)
    if len(runs) < ctx.min_history:
        return None
    expected = sorted(set.intersection(*runs))
    if not expected:
        return None
    cases_seen = {r.case for r in results}
    missing = [case for case in expected if case not in cases_seen]
    total = len(expected)
    if not missing:
        return RuleOutcome(
            "coverage",
            "*",
            True,
            Outcome.PASS,
            f"all {total}/{total} case(s) from the last {len(runs)} baseline run(s) reported",
        )
    return _missing_outcome(missing, total, "baseline")


def missing_history(
    config: Config, outcomes: list[RuleOutcome], ctx: Context
) -> RuleOutcome | None:
    """INCONCLUSIVE when the baseline has no runs at all and the gate needs one.

    For a run that should have a baseline (a pull request gated against main), an empty
    history means it was lost, such as an expired CI cache. Rules that compare against it
    would say "not gating yet" and pass, and without ``expected_cases`` nothing would
    notice dropped cases. A gate whose required rules and case list stand on their own
    doesn't need history, so it isn't held up.
    """
    needs_history = config.expected_cases is None or any(
        o.required and o.outcome is Outcome.NO_BASELINE for o in outcomes
    )
    if not needs_history or ctx.history.recent_cases(ctx.window, exclude_run=ctx.run_id):
        return None
    return RuleOutcome(
        "history",
        "*",
        True,
        Outcome.INCONCLUSIVE,
        "no baseline runs found, so there is nothing to compare against. Record runs on "
        "your main branch first. (In GitHub Actions, the history cache is deleted after "
        "7 days without use.)",
    )


def evaluate(
    config: Config,
    results: list[Result],
    history: History,
    run_id: str | None = None,
    require_history: bool = False,
) -> GateResult:
    """Run every rule and decide.

    * Any **required** rule FAILs -> REGRESSION
    * else any **required** rule is INCONCLUSIVE (including missing expected cases, or,
      without ``expected_cases``, a case every baseline run reported; or, with
      ``require_history``, a baseline with no runs that the gate needs) -> INCONCLUSIVE
    * else -> PASS

    Optional rules are reported but never change the verdict.
    """
    ctx = Context(history, config.window, config.min_history, run_id)
    outcomes = [rule.evaluate(results, ctx) for rule in config.rules]
    cov = (
        coverage(config, results)
        if config.expected_cases is not None
        else baseline_coverage(results, ctx)
    )
    if cov is not None:
        outcomes.insert(0, cov)
    hist = missing_history(config, outcomes, ctx) if require_history else None
    if hist is not None:
        outcomes.insert(0, hist)
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
