"""
state_taxes.py — TaxAgent-OpenEnv | State Income Tax Engine
============================================================

Con #6 was: US federal only. Real tax complexity is in state taxes.
California goes to 13.3%. New York adds a city surcharge.

This module extends tax_engine with CA, NY, and TX (no state income tax —
itself an important edge case that LLMs often get wrong by inventing a rate).

Sources:
  CA: FTB Publication 1005 (2024), Schedule CA (540)
  NY: NYS IT-201 instructions (2024), NYC Finance Dept
  TX: No state income tax — Texas Tax Code §171 (franchise tax, not personal)

Usage (standalone):
    from state_taxes import compute_state_liability, StateTaxResult
    result = compute_state_liability(100_000, "CA", "single", 2024)
    print(result.summary())

Usage (combined with federal):
    from tax_engine import compute_liability
    from state_taxes import compute_state_liability
    federal = compute_liability(100_000, 2024, "single")
    state   = compute_state_liability(100_000, "CA", "single", 2024)
    total   = federal.total_tax_liability + state.state_income_tax
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

State = Literal["CA", "NY", "TX"]

# ── California 2024 ───────────────────────────────────────────────────────────
# Source: FTB 2024 Tax Rate Schedule, Publication 1005
# Standard deduction: $4,803 single / $9,606 MFJ
# SDI (State Disability Insurance): 1.1% on all wages (no cap since 2024)

_CA_BRACKETS_2024: dict[str, list[tuple[float, float | None, float]]] = {
    "single": [
        (0,         10_412,  0.01),
        (10_412,    24_684,  0.02),
        (24_684,    38_959,  0.04),
        (38_959,    54_081,  0.06),
        (54_081,    68_350,  0.08),
        (68_350,    349_137, 0.093),
        (349_137,   418_961, 0.103),
        (418_961,   698_274, 0.113),
        (698_274,   None,    0.123),
        # Mental Health Services Tax: 1% on income > $1M (separate surtax)
    ],
    "married_filing_jointly": [
        (0,         20_824,  0.01),
        (20_824,    49_368,  0.02),
        (49_368,    77_918,  0.04),
        (77_918,    108_162, 0.06),
        (108_162,   136_700, 0.08),
        (136_700,   698_274, 0.093),
        (698_274,   837_922, 0.103),
        (837_922,   1_000_000, 0.113),
        (1_000_000, None,    0.123),
    ],
    "head_of_household": [
        (0,         20_839,  0.01),
        (20_839,    49_371,  0.02),
        (49_371,    63_644,  0.04),
        (63_644,    78_765,  0.06),
        (78_765,    93_037,  0.08),
        (93_037,    474_824, 0.093),
        (474_824,   569_790, 0.103),
        (569_790,   949_649, 0.113),
        (949_649,   None,    0.123),
    ],
}

_CA_STANDARD_DEDUCTIONS_2024: dict[str, float] = {
    "single":                 4_803.0,
    "married_filing_jointly": 9_606.0,
    "head_of_household":      9_606.0,
}

_CA_SDI_RATE      = 0.011   # 1.1% — no wage cap (removed 2024)
_CA_MHST_RATE     = 0.01    # 1% Mental Health Services Tax on income > $1M
_CA_MHST_THRESHOLD = 1_000_000.0


# ── New York State 2024 ───────────────────────────────────────────────────────
# Source: NYS IT-201 Tax Computation Instructions, 2024
# Standard deduction: $8,000 single / $16,050 MFJ

_NY_BRACKETS_2024: dict[str, list[tuple[float, float | None, float]]] = {
    "single": [
        (0,          17_150,   0.04),
        (17_150,     23_600,   0.045),
        (23_600,     27_900,   0.0525),
        (27_900,     161_550,  0.0585),
        (161_550,    323_200,  0.0625),
        (323_200,    2_155_350, 0.0685),
        (2_155_350,  5_000_000, 0.0965),
        (5_000_000,  25_000_000, 0.103),
        (25_000_000, None,      0.109),
    ],
    "married_filing_jointly": [
        (0,          27_900,   0.04),
        (27_900,     43_000,   0.045),
        (43_000,     161_550,  0.0525),
        (161_550,    323_200,  0.0585),
        (323_200,    2_155_350, 0.0625),
        (2_155_350,  5_000_000, 0.0685),
        (5_000_000,  25_000_000, 0.0965),
        (25_000_000, None,      0.109),
    ],
    "head_of_household": [
        (0,          17_150,   0.04),
        (17_150,     23_600,   0.045),
        (23_600,     27_900,   0.0525),
        (27_900,     161_550,  0.0585),
        (161_550,    323_200,  0.0625),
        (323_200,    2_155_350, 0.0685),
        (2_155_350,  5_000_000, 0.0965),
        (5_000_000,  25_000_000, 0.103),
        (25_000_000, None,      0.109),
    ],
}

_NY_STANDARD_DEDUCTIONS_2024: dict[str, float] = {
    "single":                 8_000.0,
    "married_filing_jointly": 16_050.0,
    "head_of_household":      11_200.0,
}

# New York City additional tax (residents only)
# Source: NYC Dept of Finance, Schedule NYC-1 (2024)
_NYC_BRACKETS_2024: list[tuple[float, float | None, float]] = [
    (0,      12_000, 0.03078),
    (12_000, 25_000, 0.03762),
    (25_000, 50_000, 0.03819),
    (50_000, None,   0.03876),
]


# ── Texas ─────────────────────────────────────────────────────────────────────
# Texas has NO personal state income tax. This is intentional.
# If an LLM outputs any positive state income tax for TX, it is wrong.

_TX_RATE = 0.0


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class StateTaxResult:
    state:             str
    gross_income:      float
    filing_status:     str
    tax_year:          int
    standard_deduction: float
    taxable_income:    float
    state_income_tax:  float
    sdi_or_sut:        float   # CA SDI, NY's withholding equiv, TX = 0
    city_tax:          float   # NYC surcharge where applicable
    total_state_liability: float
    effective_rate:    float
    notes:             list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"State             : {self.state}",
            f"Gross Income      : ${self.gross_income:>12,.2f}",
            f"Standard Deduction: ${self.standard_deduction:>12,.2f}",
            f"Taxable Income    : ${self.taxable_income:>12,.2f}",
            f"State Income Tax  : ${self.state_income_tax:>12,.2f}",
        ]
        if self.sdi_or_sut > 0:
            lines.append(
                f"SDI / Payroll Tax : ${self.sdi_or_sut:>12,.2f}"
            )
        if self.city_tax > 0:
            lines.append(
                f"City Tax (NYC)    : ${self.city_tax:>12,.2f}"
            )
        lines += [
            f"──────────────────────────────────────────",
            f"Total State       : ${self.total_state_liability:>12,.2f}",
            f"Effective Rate    : {self.effective_rate*100:.2f}%",
        ]
        for note in self.notes:
            lines.append(f"NOTE: {note}")
        return "\n".join(lines)


# ── Core function ─────────────────────────────────────────────────────────────

def _bracket_tax(taxable: float,
                  brackets: list[tuple[float, float | None, float]]) -> float:
    """Apply progressive brackets to taxable income. Returns total tax."""
    total = 0.0
    for (lower, upper_raw, rate) in brackets:
        upper = float('inf') if upper_raw is None else float(upper_raw)
        if taxable <= lower:
            break
        income_in = min(taxable, upper) - lower
        total    += income_in * rate
    return total


def compute_state_liability(
    gross_income:   float,
    state:          State,
    filing_status:  str = "single",
    tax_year:       int = 2024,
    nyc_resident:   bool = False,
) -> StateTaxResult:
    """
    Compute state income tax liability for CA, NY, or TX.

    Parameters
    ----------
    gross_income
        Total gross W-2 income (before any deductions).
    state
        Two-letter state code: 'CA', 'NY', or 'TX'.
    filing_status
        'single', 'married_filing_jointly', or 'head_of_household'.
    tax_year
        Currently supports 2024 only.
    nyc_resident
        NY only. If True, adds NYC city income tax on top of state tax.

    Returns
    -------
    StateTaxResult
        Full breakdown of state tax liability.
    """
    if tax_year != 2024:
        raise NotImplementedError(f"State tax year {tax_year} not supported.")
    if gross_income < 0:
        raise ValueError(f"gross_income must be >= 0, got {gross_income}")

    state = state.upper()
    notes: list[str] = []

    # ── Texas: zero state income tax ─────────────────────────────────────
    if state == "TX":
        return StateTaxResult(
            state             = "TX",
            gross_income      = gross_income,
            filing_status     = filing_status,
            tax_year          = tax_year,
            standard_deduction= 0.0,
            taxable_income    = gross_income,
            state_income_tax  = 0.0,
            sdi_or_sut        = 0.0,
            city_tax          = 0.0,
            total_state_liability = 0.0,
            effective_rate    = 0.0,
            notes             = ["Texas has no state personal income tax (Art. VIII, TX Const.)"],
        )

    # ── California ───────────────────────────────────────────────────────
    if state == "CA":
        if filing_status not in _CA_BRACKETS_2024:
            raise ValueError(f"Invalid filing_status for CA: {filing_status}")

        std_ded  = _CA_STANDARD_DEDUCTIONS_2024[filing_status]
        taxable  = max(0.0, gross_income - std_ded)
        ca_tax   = _bracket_tax(taxable, _CA_BRACKETS_2024[filing_status])

        # Mental Health Services Tax (Prop 63): 1% on income > $1M
        mhst = 0.0
        if gross_income > _CA_MHST_THRESHOLD:
            mhst = (gross_income - _CA_MHST_THRESHOLD) * _CA_MHST_RATE
            ca_tax += mhst
            notes.append(
                f"Mental Health Services Tax (Prop 63): ${mhst:,.2f} "
                f"on income above $1,000,000"
            )

        # SDI: 1.1% on all wages, no cap (cap removed Jan 1, 2024)
        sdi      = gross_income * _CA_SDI_RATE
        total    = ca_tax + sdi
        eff_rate = total / gross_income if gross_income > 0 else 0.0

        return StateTaxResult(
            state             = "CA",
            gross_income      = gross_income,
            filing_status     = filing_status,
            tax_year          = tax_year,
            standard_deduction= std_ded,
            taxable_income    = round(taxable, 2),
            state_income_tax  = round(ca_tax, 2),
            sdi_or_sut        = round(sdi, 2),
            city_tax          = 0.0,
            total_state_liability = round(total, 2),
            effective_rate    = round(eff_rate, 6),
            notes             = notes,
        )

    # ── New York ─────────────────────────────────────────────────────────
    if state == "NY":
        if filing_status not in _NY_BRACKETS_2024:
            raise ValueError(f"Invalid filing_status for NY: {filing_status}")

        std_ded = _NY_STANDARD_DEDUCTIONS_2024[filing_status]
        taxable = max(0.0, gross_income - std_ded)
        ny_tax  = _bracket_tax(taxable, _NY_BRACKETS_2024[filing_status])

        # NYC resident surcharge
        nyc_tax = 0.0
        if nyc_resident:
            # NYC tax applies to NY taxable income (same base)
            nyc_tax = _bracket_tax(taxable, _NYC_BRACKETS_2024)
            notes.append(
                f"NYC resident tax included: ${nyc_tax:,.2f} "
                f"(effective NYC rate: {nyc_tax/gross_income*100:.2f}%)"
            )

        total    = ny_tax + nyc_tax
        eff_rate = total / gross_income if gross_income > 0 else 0.0

        return StateTaxResult(
            state             = "NY",
            gross_income      = gross_income,
            filing_status     = filing_status,
            tax_year          = tax_year,
            standard_deduction= std_ded,
            taxable_income    = round(taxable, 2),
            state_income_tax  = round(ny_tax, 2),
            sdi_or_sut        = 0.0,
            city_tax          = round(nyc_tax, 2),
            total_state_liability = round(total, 2),
            effective_rate    = round(eff_rate, 6),
            notes             = notes,
        )

    raise ValueError(f"State '{state}' not supported. Supported: CA, NY, TX")


def compute_combined_liability(
    gross_income:   float,
    state:          State,
    filing_status:  str = "single",
    tax_year:       int = 2024,
    nyc_resident:   bool = False,
) -> dict:
    """
    Compute federal + state combined liability.
    Returns a dict for easy serialization into evaluation logs.
    """
    from tax_engine import compute_liability
    federal = compute_liability(gross_income, tax_year, filing_status, fica=True)
    state_r = compute_state_liability(gross_income, state, filing_status,
                                       tax_year, nyc_resident)
    combined = round(federal.total_tax_liability + state_r.total_state_liability, 2)
    combined_eff = combined / gross_income if gross_income > 0 else 0.0

    return {
        "gross_income":           gross_income,
        "state":                  state,
        "filing_status":          filing_status,
        "federal_total":          federal.total_tax_liability,
        "state_total":            state_r.total_state_liability,
        "combined_total":         combined,
        "combined_effective_rate": round(combined_eff, 6),
        "federal_breakdown":      federal.summary(),
        "state_breakdown":        state_r.summary(),
    }


if __name__ == "__main__":
    # Quick demo
    for state in ["CA", "NY", "TX"]:
        for income in [50_000, 150_000, 500_000]:
            result = compute_state_liability(income, state, "single", 2024,
                                              nyc_resident=(state == "NY"))
            print(f"\n{'='*50}")
            print(f"  {state} | ${income:,} | Single")
            print('='*50)
            print(result.summary())
