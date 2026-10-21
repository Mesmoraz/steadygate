"""Gate rules.

Every rule follows one principle: **errors can never make you pass.** A rule judges the
usable (``Status.OK``) values it has. If that judgement is FAIL, it stands. If it would be
PASS but some results for the metric errored, the rule is INCONCLUSIVE instead, because the
missing values might have changed the answer.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from steadygate.models import Outcome, Result, RuleOutcome, Status


class History(Protocol):
    def run_aggregates(
        self, metric: str, window: int, exclude_run: str | None = None
    ) -> list[float]: ...


@dataclass(frozen=True)
class Context:
    history: History
    window: int
    min_history: int
    run_id: str | None = None


def _split(results: list[Result], metric: str) -> tuple[list[Result], list[Result]]:
    ok = [
        r for r in results if r.metric == metric and r.status is Status.OK and r.value is not None
    ]
    errors = [r for r in results if r.metric == metric and r.status is Status.ERROR]
    return ok, errors


def _required(spec: dict[str, Any]) -> bool:
    value = spec.get("required", True)
    if not isinstance(value, bool):
        raise ValueError("'required' must be true or false")
    return value


def _number(spec: dict[str, Any], key: str) -> float | None:
    value = spec.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"'{key}' must be a number")
    return float(value)


def _guard_errors(outcome: RuleOutcome, errors: list[Result]) -> RuleOutcome:
    if outcome.outcome is Outcome.PASS and errors:
        return RuleOutcome(
            rule=outcome.rule,
            metric=outcome.metric,
            required=outcome.required,
            outcome=Outcome.INCONCLUSIVE,
            detail=f"{outcome.detail}; but {len(errors)} result(s) errored, so it can't pass",
            observed=outcome.observed,
            baseline=outcome.baseline,
        )
    return outcome


class Rule(Protocol):
    @property
    def metric(self) -> str: ...

    @property
    def required(self) -> bool: ...

    @property
    def name(self) -> str: ...

    def evaluate(self, results: list[Result], ctx: Context) -> RuleOutcome: ...


@dataclass(frozen=True)
class Absolute:
    """A hard contract: the value must stay within ``min``/``max``.

    ``scope: aggregate`` (default) checks the mean across cases; ``scope: case`` checks
    every case individually. Use it for deterministic checks such as "every test passed"
    or "output is valid JSON".
    """

    type_name: ClassVar[str] = "absolute"

    metric: str
    minimum: float | None
    maximum: float | None
    scope: str = "aggregate"
    required: bool = True

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> Absolute:
        minimum, maximum = _number(spec, "min"), _number(spec, "max")
        if minimum is None and maximum is None:
            raise ValueError("needs 'min', 'max', or both")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("'min' is greater than 'max'")
        scope = spec.get("scope", "aggregate")
        if scope not in {"aggregate", "case"}:
            raise ValueError("'scope' must be 'aggregate' or 'case'")
        return cls(str(spec["metric"]), minimum, maximum, scope, _required(spec))

    @property
    def name(self) -> str:
        return f"absolute:{self.metric}"

    def _bounds(self) -> str:
        parts = []
        if self.minimum is not None:
            parts.append(f">= {self.minimum:g}")
        if self.maximum is not None:
            parts.append(f"<= {self.maximum:g}")
        return " and ".join(parts)

    def _within(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum:
            return False
        return not (self.maximum is not None and value > self.maximum)

    def evaluate(self, results: list[Result], ctx: Context) -> RuleOutcome:
        ok, errors = _split(results, self.metric)

        def outcome(o: Outcome, detail: str, observed: float | None = None) -> RuleOutcome:
            return RuleOutcome(self.name, self.metric, self.required, o, detail, observed)

        if not ok:
            return outcome(Outcome.INCONCLUSIVE, f"no usable '{self.metric}' results")

        if self.scope == "case":
            bad = [r for r in ok if r.value is not None and not self._within(r.value)]
            if bad:
                names = ", ".join(r.case for r in bad[:5]) + (" ..." if len(bad) > 5 else "")
                return outcome(
                    Outcome.FAIL, f"{len(bad)}/{len(ok)} case(s) not {self._bounds()}: {names}"
                )
            res = outcome(Outcome.PASS, f"all {len(ok)} case(s) {self._bounds()}")
        else:
            mean = statistics.fmean(r.value for r in ok if r.value is not None)
            o = Outcome.PASS if self._within(mean) else Outcome.FAIL
            verb = "is" if o is Outcome.PASS else "is not"
            res = outcome(o, f"mean {mean:.4g} {verb} {self._bounds()}", mean)
        return _guard_errors(res, errors)


@dataclass(frozen=True)
class RelativeDelta:
    """The mean must not regress more than ``max_regression`` (a fraction) versus the
    median of the last ``window`` recorded runs.

    The median, rather than the mean, keeps one unusually good or bad past run from
    moving the baseline.
    """

    type_name: ClassVar[str] = "relative_delta"

    metric: str
    max_regression: float
    higher_is_better: bool = True
    required: bool = True

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> RelativeDelta:
        max_regression = _number(spec, "max_regression")
        if max_regression is None or max_regression < 0:
            raise ValueError("'max_regression' must be a number >= 0 (e.g. 0.05 for 5%)")
        direction = spec.get("direction", "higher_is_better")
        if direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("'direction' must be 'higher_is_better' or 'lower_is_better'")
        return cls(
            str(spec["metric"]), max_regression, direction == "higher_is_better", _required(spec)
        )

    @property
    def name(self) -> str:
        return f"relative_delta:{self.metric}"

    def evaluate(self, results: list[Result], ctx: Context) -> RuleOutcome:
        ok, errors = _split(results, self.metric)

        def outcome(
            o: Outcome, detail: str, observed: float | None = None, baseline: float | None = None
        ) -> RuleOutcome:
            return RuleOutcome(self.name, self.metric, self.required, o, detail, observed, baseline)

        if not ok:
            return outcome(Outcome.INCONCLUSIVE, f"no usable '{self.metric}' results")
        observed = statistics.fmean(r.value for r in ok if r.value is not None)

        history = ctx.history.run_aggregates(self.metric, ctx.window, exclude_run=ctx.run_id)
        if len(history) < ctx.min_history:
            return outcome(
                Outcome.NO_BASELINE,
                f"{len(history)}/{ctx.min_history} baseline run(s) recorded; not gating yet",
                observed,
            )
        baseline = float(statistics.median(history))

        # Positive regression = worse, whichever direction is "better".
        diff = baseline - observed if self.higher_is_better else observed - baseline
        # With a zero baseline any worsening is an infinite relative regression.
        zero_base = 0.0 if diff <= 0 else float("inf")
        regression = diff / abs(baseline) if baseline else zero_base

        change = (observed - baseline) / abs(baseline) if baseline else 0.0
        summary = (
            f"{observed:.4g} vs baseline median {baseline:.4g} "
            f"({change:+.1%}, limit {self.max_regression:.1%} worse, n={len(history)} runs)"
        )
        o = Outcome.FAIL if regression > self.max_regression else Outcome.PASS
        return _guard_errors(outcome(o, summary, observed, baseline), errors)


RULE_TYPES: dict[str, type[Absolute] | type[RelativeDelta]] = {
    Absolute.type_name: Absolute,
    RelativeDelta.type_name: RelativeDelta,
}
