"""
tax_engine.py — TaxAgent-OpenEnv | Deterministic US Federal Tax Calculator
===========================================================================

This is the "Calculator" layer the original architecture described but never
built. Every number that comes out of this module is deterministic, auditable,
and correct. The LLM is NOT allowed to output raw floats for tax calculations.
It MUST call these functions. That is the entire point.

If the agent writes `tax = income * 0.20`, the harness rejects the code before
execution. If it writes `tax = tax_engine.compute_liability(income, 2024)`,
the sandbox allows it and the answer is guaranteed correct.

This eliminates mathematical hallucinations at the source instead of trying to
score them after the fact.

Tax Law Reference: IRC §1 (2024), Rev. Proc. 2023-34.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import math

# ── Type aliases ──────────────────────────────────────────────────────────────
FilingStatus = Literal["single", "married_filing_jointly", "head_of_household"]

# ── 2024 US Federal Income Tax Brackets (Rev. Proc. 2023-34) ─────────────────
#
# Each bracket: (lower_bound, upper_bound, marginal_rate)
# upper_bound of None means "no ceiling" (top bracket).
#
# This is how a tax system actually works.
# Taxable income $60,000 (single) is NOT taxed at 22% flat.
# The first $11,600 is taxed at 10%, the next $35,550 at 12%,
# and the remaining $12,850 at 22%.
# Effective rate ≈ 13.8%, not 22%.
# Every LLM that outputs "22%" for this scenario is wrong. Measure that.

_BRACKETS_2024: dict[FilingStatus, list[tuple[float, float | None, float]]] = {
    "single": [
        (0,        11_600,  0.10),
        (11_600,   47_150,  0.12),
        (47_150,   100_525, 0.22),
        (100_525,  191_950, 0.24),
        (191_950,  243_725, 0.32),
        (243_725,  609_350, 0.35),
        (609_350,  None,    0.37),
    ],
    "married_filing_jointly": [
        (0,        23_200,  0.10),
        (23_200,   94_300,  0.12),
        (94_300,   201_050, 0.22),
        (201_050,  383_900, 0.24),
        (383_900,  487_450, 0.32),
        (487_450,  731_200, 0.35),
        (731_200,  None,    0.37),
    ],
    "head_of_household": [
        (0,        16_550,  0.10),
        (16_550,   63_100,  0.12),
        (63_100,   100_500, 0.22),
        (100_500,  191_950, 0.24),
        (191_950,  243_700, 0.32),
        (243_700,  609_350, 0.35),
        (609_350,  None,    0.37),
    ],
}

# Standard deductions for 2024 (Rev. Proc. 2023-34 §3.11)
_STANDARD_DEDUCTIONS_2024: dict[FilingStatus, float] = {
    "single":                 14_600.0,
    "married_filing_jointly": 29_200.0,
    "head_of_household":      21_900.0,
}

# FICA rates (for W-2 employee, employee share only)
_SOCIAL_SECURITY_RATE   = 0.062
_SOCIAL_SECURITY_WAGE_BASE = 168_600.0   # 2024 wage base
_MEDICARE_RATE          = 0.0145
_ADDITIONAL_MEDICARE_RATE = 0.009        # on wages > $200K (single)
_ADDITIONAL_MEDICARE_THRESHOLD = 200_000.0


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class BracketDetail:
    """Tax applied within a single bracket."""
    rate:       float
    lower:      float
    upper:      float
    income_in_bracket: float
    tax_in_bracket:    float


@dataclass
class TaxComputation:
    """
    Full breakdown of a tax computation. Immutable after construction.
    This is what the LLM can read via the tool API — it sees every number
    that went into the final figure, making hallucination detectable.
    """
    gross_income:        float
    filing_status:       FilingStatus
    tax_year:            int
    standard_deduction:  float
    taxable_income:      float
    brackets:            list[BracketDetail] = field(default_factory=list)
    federal_income_tax:  float = 0.0
    effective_rate:      float = 0.0
    marginal_rate:       float = 0.0
    social_security_tax: float = 0.0
    medicare_tax:        float = 0.0
    total_tax_liability: float = 0.0
    after_tax_income:    float = 0.0

    def summary(self) -> str:
        """Human-readable summary for agent observation space."""
        lines = [
            f"Tax Year          : {self.tax_year}",
            f"Filing Status     : {self.filing_status.replace('_', ' ').title()}",
            f"Gross Income      : ${self.gross_income:>12,.2f}",
            f"Standard Deduction: ${self.standard_deduction:>12,.2f}",
            f"Taxable Income    : ${self.taxable_income:>12,.2f}",
            "",
            "Bracket Breakdown:",
        ]
        for b in self.brackets:
            if b.income_in_bracket <= 0:
                continue
            lines.append(
                f"  {b.rate*100:4.0f}%  "
                f"${b.lower:>9,.0f} – {'∞' if b.upper == float('inf') else f'${b.upper:>9,.0f}':>11}  "
                f"income: ${b.income_in_bracket:>9,.2f}  "
                f"tax: ${b.tax_in_bracket:>9,.2f}"
            )
        lines += [
            "",
            f"Federal Income Tax: ${self.federal_income_tax:>12,.2f}",
            f"Social Security   : ${self.social_security_tax:>12,.2f}",
            f"Medicare          : ${self.medicare_tax:>12,.2f}",
            f"──────────────────────────────────────────",
            f"Total Liability   : ${self.total_tax_liability:>12,.2f}",
            f"Effective Rate    : {self.effective_rate*100:.2f}%",
            f"Marginal Rate     : {self.marginal_rate*100:.0f}%",
            f"After-Tax Income  : ${self.after_tax_income:>12,.2f}",
        ]
        return "\n".join(lines)


# ── Core computation functions ────────────────────────────────────────────────

def compute_liability(
    gross_income:   float,
    tax_year:       int = 2024,
    filing_status:  FilingStatus = "single",
    fica:           bool = True,
) -> TaxComputation:
    """
    Compute complete US federal tax liability.

    This is the primary entry point for LLM agents. The agent calls this
    function and reads the TaxComputation result. It never does arithmetic
    itself.

    Parameters
    ----------
    gross_income
        Total gross income before any deductions (W-2 box 1 equivalent).
    tax_year
        The tax year to use for brackets and deductions. Currently supports
        2024 only; add years by extending _BRACKETS_2024.
    filing_status
        One of: 'single', 'married_filing_jointly', 'head_of_household'.
    fica
        Whether to include FICA (Social Security + Medicare) in total liability.

    Returns
    -------
    TaxComputation
        Fully populated breakdown including per-bracket details.

    Raises
    ------
    ValueError
        If inputs are out of valid range.
    NotImplementedError
        If tax_year is not supported.
    """
    if gross_income < 0:
        raise ValueError(f"gross_income must be >= 0, got {gross_income}")
    if tax_year != 2024:
        raise NotImplementedError(
            f"Tax year {tax_year} not yet implemented. Supported: 2024."
        )
    if filing_status not in _BRACKETS_2024:
        raise ValueError(
            f"Invalid filing_status '{filing_status}'. "
            f"Must be one of: {list(_BRACKETS_2024.keys())}"
        )

    std_ded      = _STANDARD_DEDUCTIONS_2024[filing_status]
    taxable      = max(0.0, gross_income - std_ded)
    brackets_raw = _BRACKETS_2024[filing_status]

    # ── Marginal bracket calculation ──────────────────────────────────────
    bracket_details: list[BracketDetail] = []
    federal_tax = 0.0
    marginal_rate = 0.0

    for (lower, upper_raw, rate) in brackets_raw:
        upper = float('inf') if upper_raw is None else float(upper_raw)
        if taxable <= lower:
            break
        income_in_bracket = min(taxable, upper) - lower
        tax_in_bracket    = income_in_bracket * rate
        federal_tax      += tax_in_bracket
        marginal_rate     = rate
        bracket_details.append(BracketDetail(
            rate=rate,
            lower=lower,
            upper=upper,
            income_in_bracket=income_in_bracket,
            tax_in_bracket=tax_in_bracket,
        ))

    effective_rate = (federal_tax / gross_income) if gross_income > 0 else 0.0

    # ── FICA ──────────────────────────────────────────────────────────────
    ss_tax = medicare_tax = 0.0
    if fica:
        ss_wage_base = min(gross_income, _SOCIAL_SECURITY_WAGE_BASE)
        ss_tax       = ss_wage_base * _SOCIAL_SECURITY_RATE
        medicare_tax = gross_income * _MEDICARE_RATE
        if gross_income > _ADDITIONAL_MEDICARE_THRESHOLD:
            medicare_tax += (
                (gross_income - _ADDITIONAL_MEDICARE_THRESHOLD)
                * _ADDITIONAL_MEDICARE_RATE
            )

    total = federal_tax + ss_tax + medicare_tax

    return TaxComputation(
        gross_income        = gross_income,
        filing_status       = filing_status,
        tax_year            = tax_year,
        standard_deduction  = std_ded,
        taxable_income      = taxable,
        brackets            = bracket_details,
        federal_income_tax  = round(federal_tax, 2),
        effective_rate      = round(effective_rate, 6),
        marginal_rate       = marginal_rate,
        social_security_tax = round(ss_tax, 2),
        medicare_tax        = round(medicare_tax, 2),
        total_tax_liability = round(total, 2),
        after_tax_income    = round(gross_income - total, 2),
    )


def apply_standard_deduction(gross_income: float,
                               tax_year: int = 2024,
                               filing_status: FilingStatus = "single") -> float:
    """
    Return taxable income after applying the standard deduction.
    Cannot go below zero — a taxpayer does not get a refund from the deduction
    alone if their income is below the deduction threshold.
    """
    if tax_year != 2024:
        raise NotImplementedError(f"Tax year {tax_year} not supported.")
    deduction = _STANDARD_DEDUCTIONS_2024[filing_status]
    return max(0.0, gross_income - deduction)


def get_marginal_rate(taxable_income: float,
                       filing_status: FilingStatus = "single",
                       tax_year: int = 2024) -> float:
    """
    Return the marginal (top) tax rate for a given taxable income.
    This is NOT the effective rate. The distinction matters and LLMs
    consistently confuse the two.
    """
    if tax_year != 2024:
        raise NotImplementedError(f"Tax year {tax_year} not supported.")
    for (lower, upper_raw, rate) in reversed(_BRACKETS_2024[filing_status]):
        if taxable_income > lower:
            return rate
    return 0.0


def score_agent_answer(agent_liability: float,
                        correct_result: TaxComputation,
                        tolerance_pct: float = 0.005) -> float:
    """
    Deterministic grader: score the agent's submitted liability against the
    correct computation. Returns a float in [0.0, 1.0].

    Scoring:
        - Exact match (within tolerance_pct): 1.0
        - Correct federal income tax but wrong FICA: 0.7
        - Correct taxable income but wrong brackets applied: 0.4
        - Wrong deduction application: 0.2
        - Completely wrong: 0.0

    This replaces the binary "right or wrong on the final float" grader that
    gave full credit to lucky guesses and zero credit to agents that got 99%
    of the reasoning correct.
    """
    correct = correct_result.total_tax_liability
    if correct == 0:
        return 1.0 if agent_liability == 0 else 0.0

    rel_error = abs(agent_liability - correct) / correct

    if rel_error <= tolerance_pct:
        return 1.0

    # Partial credit: check if they got federal income tax right
    fed_error = abs(agent_liability - correct_result.federal_income_tax) / max(correct_result.federal_income_tax, 1)
    if fed_error <= tolerance_pct:
        return 0.7   # Got federal tax right, forgot FICA

    # Partial credit: check if they correctly computed taxable income
    # (meaning they applied the deduction) but got brackets wrong
    manual_10pct = correct_result.taxable_income * 0.10
    taxable_error = abs(agent_liability - manual_10pct) / max(manual_10pct, 1)
    if taxable_error <= 0.5:
        return 0.4   # Applied deduction but used flat/wrong rate

    # Check if they ignored the deduction entirely
    raw_error = abs(agent_liability - correct_result.gross_income * 0.10) / correct
    if raw_error <= 0.5:
        return 0.2   # Computed on gross income, missed the deduction concept

    return 0.0
