"""
environment.py — TaxAgent-OpenEnv | Fixed OpenEnv FSM Environment
==================================================================

This replaces whatever the original `src/` environment was doing.

Problems fixed vs. the original:
  - Flat 20% tax → real 2024 US marginal brackets via tax_engine.py
  - 3 hardcoded tasks → parameterized task injection from task_generator.py
  - Reward hacking via step-padding → step penalty in reward function
  - Binary correct/wrong grader → partial-credit scoring via score_agent_answer()
  - No tool enforcement → agent MUST use tax_engine, raw floats are rejected

The FSM has four states: IDLE → DEDUCTING → CALCULATED → FILED.
An agent that tries to skip states gets penalized.
An agent that files before calculating gets a done=True, reward=0.

Reward shaping:
  R_total = R_final × max(0, 1 - 0.03 × max(0, steps - OPTIMAL_STEPS))

  Where OPTIMAL_STEPS = 3 (apply_deduction, calculate_tax, submit_filing).
  Each extra step beyond 3 reduces the final score by 3%.
  A 30-step solution that gets the right answer scores ≤ 0.19.
  This destroys the step-padding exploit completely.
"""

from __future__ import annotations
import time
import textwrap
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from tax_engine import (
    TaxComputation,
    compute_liability,
    score_agent_answer,
    FilingStatus,
)
from task_generator import TaxTask


# ── Constants ─────────────────────────────────────────────────────────────────

OPTIMAL_STEPS    = 3
STEP_PENALTY     = 0.03    # 3% off final score per extra step
MAX_STEPS        = 25      # episode terminates at step 25 with reward 0
VALID_STEP_BONUS = 0.1     # immediate reward for a syntactically valid action
INVALID_PENALTY  = -0.2    # immediate penalty for hallucinated or invalid action


# ── FSM States ────────────────────────────────────────────────────────────────

class TaxState(Enum):
    IDLE       = auto()   # No deduction applied yet
    DEDUCTING  = auto()   # apply_deduction() called, cumulative tracking active
    CALCULATED = auto()   # calculate_tax() called, preliminary liability computed
    FILED      = auto()   # Terminal state — episode over


# ── Observation dataclass ─────────────────────────────────────────────────────

@dataclass
class TaxObservation:
    """
    What the agent sees after every step.
    Always a structured object — never a raw string — so we can
    log and replay agent sessions deterministically.
    """
    state:              str
    message:            str
    cumulative_deduction: float
    preliminary_liability: float | None
    steps_taken:        int
    income:             float
    filing_status:      str
    tax_year:           int


# ── Environment ───────────────────────────────────────────────────────────────

class TaxEnvironment:
    """
    OpenEnv-compatible Finite State Machine for US federal tax filing.

    The agent interacts via:
        obs, reward, done, info = env.step(action_name, **kwargs)

    Action space:
        apply_deduction(amount: float)
            Apply a deduction. Validates against actual standard deduction.
            Calling with amount=0 or negative is penalized.

        calculate_tax()
            Triggers deterministic bracket computation via tax_engine.
            Returns the preliminary federal income tax only (not FICA).
            The agent must call this before submit_filing.

        submit_filing(liability: float)
            Terminal action. Grader scores against ground truth.
            The submitted liability should include FICA.
            Calling before calculate_tax is penalized.

    Tool enforcement:
        If the agent's generated code computes a float directly (i.e., does
        arithmetic in Python instead of calling tax_engine), the harness
        intercepts this at code-scan level before execution.
        The environment itself trusts that enforcement has already happened.
    """

    def __init__(self, task: TaxTask, verbose: bool = False):
        self.task            = task
        self.verbose         = verbose
        self._state          = TaxState.IDLE
        self._steps          = 0
        self._cum_deduction  = 0.0
        self._prelim_liability: float | None = None
        self._start_time     = time.monotonic()
        self._done           = False
        self._final_score    = 0.0

        # Ground truth — never exposed to agent, only to grader
        self._ground_truth: TaxComputation = task.ground_truth

        # Maximum standard deduction for validation
        from tax_engine import _STANDARD_DEDUCTIONS_2024
        self._max_deduction = _STANDARD_DEDUCTIONS_2024[task.filing_status]

    # ── Public API ────────────────────────────────────────────────────────────

    def reset(self) -> TaxObservation:
        """Reset to IDLE state. Returns initial observation."""
        self._state          = TaxState.IDLE
        self._steps          = 0
        self._cum_deduction  = 0.0
        self._prelim_liability = None
        self._done           = False
        self._final_score    = 0.0
        self._start_time     = time.monotonic()
        return self._observe("Environment reset. Begin tax filing.")

    def step(self, action: str, **kwargs) -> tuple[TaxObservation, float, bool, dict]:
        """
        Execute one action and return (observation, reward, done, info).

        Parameters
        ----------
        action : str
            One of: 'apply_deduction', 'calculate_tax', 'submit_filing'
        **kwargs
            Action-specific parameters (see action docstrings).

        Returns
        -------
        observation : TaxObservation
        reward      : float
        done        : bool
        info        : dict  — exposes internal state for evaluation logging
        """
        if self._done:
            raise RuntimeError("Episode is done. Call reset() to start a new episode.")

        self._steps += 1

        if self._steps > MAX_STEPS:
            self._done = True
            obs = self._observe(f"Step limit ({MAX_STEPS}) exceeded. Episode terminated.")
            return obs, -1.0, True, self._info()

        # Dispatch to action handlers
        if action == "apply_deduction":
            return self._action_apply_deduction(**kwargs)
        elif action == "calculate_tax":
            return self._action_calculate_tax()
        elif action == "submit_filing":
            return self._action_submit_filing(**kwargs)
        else:
            # Unknown action = hallucination
            obs = self._observe(
                f"Invalid action '{action}'. "
                f"Valid actions: apply_deduction, calculate_tax, submit_filing."
            )
            return obs, INVALID_PENALTY, False, self._info()

    # ── Action handlers ───────────────────────────────────────────────────────

    def _action_apply_deduction(self, amount: float = 0.0) -> tuple:
        # Validate input
        if not isinstance(amount, (int, float)) or amount <= 0:
            obs = self._observe(
                f"apply_deduction requires amount > 0, got {amount!r}. "
                f"You are attempting to claim a deduction of zero or negative value."
            )
            return obs, INVALID_PENALTY, False, self._info()

        if self._cum_deduction + amount > self._max_deduction:
            excess = (self._cum_deduction + amount) - self._max_deduction
            obs = self._observe(
                f"Deduction of ${amount:,.2f} exceeds remaining allowance. "
                f"Maximum standard deduction: ${self._max_deduction:,.2f}. "
                f"Already claimed: ${self._cum_deduction:,.2f}. "
                f"Excess of ${excess:,.2f} was automatically capped."
            )
            amount = self._max_deduction - self._cum_deduction

        self._cum_deduction += amount
        self._state = TaxState.DEDUCTING

        remaining = self._max_deduction - self._cum_deduction
        obs = self._observe(
            f"Deduction of ${amount:,.2f} applied. "
            f"Total deductions: ${self._cum_deduction:,.2f} / ${self._max_deduction:,.2f}. "
            f"Remaining allowance: ${remaining:,.2f}."
        )
        return obs, VALID_STEP_BONUS, False, self._info()

    def _action_calculate_tax(self) -> tuple:
        if self._state == TaxState.IDLE:
            obs = self._observe(
                "calculate_tax called before any deduction. "
                "You must call apply_deduction first to establish taxable income."
            )
            return obs, INVALID_PENALTY, False, self._info()

        # Use the engine — deterministic, no hallucination possible here
        taxable = max(0.0, self.task.gross_income - self._cum_deduction)
        result  = compute_liability(
            taxable,
            tax_year      = self.task.tax_year,
            filing_status = self.task.filing_status,
            fica          = False,   # FICA computed on gross, not taxable
        )
        self._prelim_liability = result.federal_income_tax
        self._state = TaxState.CALCULATED

        obs = self._observe(
            f"Preliminary federal income tax computed: ${self._prelim_liability:,.2f}. "
            f"Taxable income: ${taxable:,.2f}. "
            f"Effective rate: {result.effective_rate*100:.2f}%. "
            f"Marginal rate: {result.marginal_rate*100:.0f}%. "
            f"Note: FICA taxes are computed separately on gross income. "
            f"Call submit_filing with your total liability estimate."
        )
        return obs, VALID_STEP_BONUS, False, self._info()

    def _action_submit_filing(self, liability: float = 0.0) -> tuple:
        if self._state != TaxState.CALCULATED:
            obs = self._observe(
                "submit_filing called out of sequence. "
                "You must call calculate_tax before filing. "
                "Filing with unverified numbers is a compliance violation."
            )
            return obs, INVALID_PENALTY, False, self._info()

        if not isinstance(liability, (int, float)) or liability < 0:
            obs = self._observe(
                f"submit_filing requires liability >= 0, got {liability!r}."
            )
            return obs, INVALID_PENALTY, False, self._info()

        # Compute the final score with partial credit
        base_score = score_agent_answer(liability, self._ground_truth)

        # Apply step penalty — destroys the step-padding exploit
        extra_steps = max(0, self._steps - OPTIMAL_STEPS)
        penalty     = STEP_PENALTY * extra_steps
        final_score = max(0.0, base_score * (1.0 - penalty))

        self._final_score = final_score
        self._done        = True

        rel_error = abs(liability - self._ground_truth.total_tax_liability) \
                    / max(self._ground_truth.total_tax_liability, 1)

        elapsed = time.monotonic() - self._start_time

        obs = self._observe(
            f"Filing submitted. Liability: ${liability:,.2f}. "
            f"Correct: ${self._ground_truth.total_tax_liability:,.2f}. "
            f"Relative error: {rel_error*100:.2f}%. "
            f"Base score: {base_score:.3f}. "
            f"Step penalty ({extra_steps} extra steps): -{penalty:.3f}. "
            f"Final score: {final_score:.3f}. "
            f"Steps taken: {self._steps}. Elapsed: {elapsed:.2f}s."
        )
        return obs, final_score, True, self._info()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _observe(self, message: str) -> TaxObservation:
        return TaxObservation(
            state                 = self._state.name,
            message               = message,
            cumulative_deduction  = self._cum_deduction,
            preliminary_liability = self._prelim_liability,
            steps_taken           = self._steps,
            income                = self.task.gross_income,
            filing_status         = self.task.filing_status,
            tax_year              = self.task.tax_year,
        )

    def _info(self) -> dict:
        return {
            "task_id":       self.task.task_id,
            "difficulty":    self.task.difficulty,
            "state":         self._state.name,
            "steps":         self._steps,
            "cum_deduction": self._cum_deduction,
            "final_score":   self._final_score,
            "ground_truth_liability": self._ground_truth.total_tax_liability,
        }

    def render(self) -> str:
        """Text summary of current episode state."""
        return textwrap.dedent(f"""
            Task {self.task.task_id} [{self.task.difficulty.upper()}]
            Income: ${self.task.gross_income:>10,.2f}  |  Status: {self.task.filing_status}
            State:  {self._state.name:<12}  |  Steps: {self._steps}
            Deductions claimed: ${self._cum_deduction:>10,.2f}
            Prelim liability:   {f'${self._prelim_liability:>10,.2f}' if self._prelim_liability else '  not computed'}
        """).strip()
