"""The generate → verify → repair loop shared by the code-producing stages.

Factored out because ``hppgen`` and ``query_codegen`` need the same control
flow with different verifiers (a header that must compile; a query that must
also match gold), and because the loop has three failure modes that are easy to
omit and expensive to omit:

- **A bounded budget.** Without one, a model that cannot fix an error will burn
  the whole run rediscovering it.
- **A no-progress guard.** A model that returns byte-identical content, or
  nothing at all, will never converge; spending the remaining rounds on it is
  pure waste, so the loop stops and says which it was.
- **A per-round record.** "Failed after 3 rounds" is not actionable. The
  verdict from each round is kept so the stage can report what it was still
  failing on, and so a header that only compiles on round 3 is visible as a
  signal that its input was too thin.
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

# (ok, human-readable report) — decoupled from CompileResult and Comparison so
# either can drive the loop.
Verdict = tuple[bool, str]


class FixRound(BaseModel):
    """One pass through the loop."""

    model_config = ConfigDict(extra="forbid")

    index: int
    ok: bool
    report: str = ""


class FixOutcome(BaseModel):
    """The result of a whole loop."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    content: str = ""
    rounds: list[FixRound] = Field(default_factory=list)
    # Why the loop stopped: "verified", "budget exhausted", "no progress",
    # "empty repair", or an error string from the repair callable.
    stopped: str = ""

    @property
    def repair_rounds(self) -> int:
        """How many repair attempts were made. Zero means it worked first time."""
        return max(0, len(self.rounds) - 1)

    @property
    def last_report(self) -> str:
        return self.rounds[-1].report if self.rounds else ""

    def brief(self) -> str:
        state = "ok" if self.ok else "failed"
        return f"{state} after {self.repair_rounds} repair round(s) ({self.stopped})"


def fix_loop(
    content: str,
    apply: Callable[[str], None],
    verify: Callable[[], Verdict],
    repair: Callable[[str, str], str],
    max_rounds: int = 2,
) -> FixOutcome:
    """Apply content, verify it, and let ``repair`` rewrite it until it passes.

    Args:
        content: The first candidate.
        apply: Persists a candidate (typically a workspace write). Called before
            every verification, so the verifier always sees what it is judging.
        verify: Returns ``(ok, report)``. The report is handed to ``repair`` and
            kept in the outcome.
        repair: ``(current_content, report) -> new_content``. Usually an LLM
            call. Returning the same content or nothing ends the loop.
        max_rounds: Maximum number of repair attempts. Zero means generate and
            verify once, with no repairs.

    Returns:
        A :class:`FixOutcome` whose ``content`` is the last applied candidate —
        the accepted one when ``ok``, so callers can cache exactly what passed.
    """
    outcome = FixOutcome(ok=False, content=content)
    current = content

    for index in range(max_rounds + 1):
        apply(current)
        ok, report = verify()
        outcome.rounds.append(FixRound(index=index, ok=ok, report=report))
        outcome.content = current
        if ok:
            outcome.ok = True
            outcome.stopped = "verified"
            return outcome
        if index == max_rounds:
            outcome.stopped = "budget exhausted"
            return outcome

        try:
            candidate = repair(current, report)
        except Exception as exc:
            # A failed repair call ends the loop with the last real verdict
            # intact, rather than discarding the work done so far.
            outcome.stopped = f"repair failed: {type(exc).__name__}: {exc}"
            return outcome

        if not candidate or not candidate.strip():
            outcome.stopped = "empty repair"
            return outcome
        if candidate.strip() == current.strip():
            outcome.stopped = "no progress"
            return outcome
        current = candidate

    return outcome  # pragma: no cover - the loop always returns inside


def no_progress_reason(previous: str, candidate: str) -> Optional[str]:
    """Why a repaired candidate is not worth verifying, or None if it is.

    The same two guards :func:`fix_loop` applies, exposed for the stages whose
    loops interleave two budgets and so cannot use ``fix_loop`` directly.
    """
    if not candidate or not candidate.strip():
        return "the model returned an empty file"
    if candidate.strip() == previous.strip():
        return "the model returned the file unchanged"
    return None


def compile_verdict(result: object, allow_missing_compiler: bool = True) -> Verdict:
    """Turn a :class:`agent.rlm.compile.CompileResult` into a verdict.

    ``compiler_missing`` is treated as "cannot verify" rather than as a compile
    failure when ``allow_missing_compiler`` is set. Conflating the two would
    send the model into a fix loop against an environment problem, rewriting
    correct code until the budget ran out.
    """
    ok = bool(getattr(result, "ok", False))
    brief = getattr(result, "brief", None)
    report = str(brief()) if callable(brief) else str(result)
    if not ok and allow_missing_compiler and getattr(result, "compiler_missing", False):
        return True, report
    return ok, report


def repair_signature() -> object:
    """The DSPy signature both code stages use to repair a rejected file.

    Shared rather than duplicated because the repair task is identical in every
    case that matters: here is the file, here is what the verifier said, return
    the whole file again. Built in a function so importing this module does not
    require dspy.
    """
    import dspy

    class RepairCode(dspy.Signature):
        """Fix a C++ file that failed verification.

        Return the complete corrected file, not a diff and not an excerpt: the
        caller overwrites the file with what you return, so anything you omit is
        deleted.

        Fix the cause named by the report. Do not silence a diagnostic by
        deleting the code that triggers it, by casting a type away, or by
        widening a signature until it compiles. Change as little as possible —
        a rewrite hides which edit actually fixed the problem.
        """

        instruction: str = dspy.InputField(desc="Rules the fix must obey.")
        report: str = dspy.InputField(desc="Compiler diagnostics or the result mismatch.")
        current_code: str = dspy.InputField(desc="The file as it stands now, in full.")
        fixed_code: str = dspy.OutputField(desc="The complete corrected file. Source only.")
        explanation: str = dspy.OutputField(
            desc="One sentence: what was wrong and what you changed."
        )

    return RepairCode
