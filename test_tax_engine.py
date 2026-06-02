"""
test_tax_engine.py — TaxAgent-OpenEnv | Pytest Suite for tax_engine.py
=======================================================================

These tests verify that our bracket math matches IRS published figures.
Every number here is cross-checked against IRS Publication 505 (2024)
and Rev. Proc. 2023-34.

If any test fails, your benchmark ground truth is wrong.
Fix the engine before running any model evaluation — otherwise you are
scoring agents against incorrect answers, which is worse than having
no benchmark at all.

Run with:
    pip install pytest
    pytest test_tax_engine.py -v
"""

import pytest
from tax_engine import (
    compute_liability,
    apply_standard_deduction,
    get_marginal_rate,
    score_agent_answer,
    _STANDARD_DEDUCTIONS_2024,
    _SOCIAL_SECURITY_WAGE_BASE,
)

# Tolerance: within $1 of the IRS published figure is acceptable.
# The IRS rounds differently in some publications; $1 tolerance catches
# real errors while ignoring rounding differences.
DOLLAR_TOLERANCE = 1.00
PCT_TOLERANCE    = 0.005   # 0.5% for score_agent_answer


# ── Helper ────────────────────────────────────────────────────────────────────

def assert_near(actual: float, expected: float, tol: float = DOLLAR_TOLERANCE,
                label: str = ""):
    diff = abs(actual - expected)
    assert diff <= tol, (
        f"{label}: expected ${expected:,.2f}, got ${actual:,.2f} "
        f"(difference: ${diff:,.2f}, tolerance: ${tol:,.2f})"
    )


# ── Standard Deduction Tests ──────────────────────────────────────────────────

class TestStandardDeductions:
    """Verify 2024 standard deduction amounts per Rev. Proc. 2023-34 §3.11."""

    def test_single(self):
        assert _STANDARD_DEDUCTIONS_2024["single"] == 14_600.0

    def test_married_filing_jointly(self):
        assert _STANDARD_DEDUCTIONS_2024["married_filing_jointly"] == 29_200.0

    def test_head_of_household(self):
        assert _STANDARD_DEDUCTIONS_2024["head_of_household"] == 21_900.0

    def test_apply_deduction_single(self):
        # $50,000 gross single → $35,400 taxable
        assert apply_standard_deduction(50_000, 2024, "single") == 35_400.0

    def test_apply_deduction_below_threshold(self):
        # Income below standard deduction → taxable income is ZERO, not negative
        result = apply_standard_deduction(10_000, 2024, "single")
        assert result == 0.0, "Taxable income cannot go below zero"

    def test_apply_deduction_exact_threshold(self):
        # Income exactly equal to deduction → zero taxable income
        result = apply_standard_deduction(14_600, 2024, "single")
        assert result == 0.0

    def test_unsupported_year(self):
        with pytest.raises(NotImplementedError):
            apply_standard_deduction(50_000, 2023, "single")


# ── Federal Income Tax — Single Filer ─────────────────────────────────────────

class TestFederalTaxSingle:
    """
    IRS 2024 tax tables for single filers.
    Cross-referenced against IRS Publication 505 Table 1 (2024).
    """

    def test_zero_income(self):
        r = compute_liability(0.0, 2024, "single", fica=False)
        assert r.federal_income_tax == 0.0
        assert r.taxable_income == 0.0

    def test_below_standard_deduction(self):
        # $10,000 income — below $14,600 deduction — zero federal tax
        r = compute_liability(10_000, 2024, "single", fica=False)
        assert r.federal_income_tax == 0.0
        assert r.taxable_income == 0.0

    def test_at_standard_deduction(self):
        # Exactly $14,600 — taxable income zero, federal tax zero
        r = compute_liability(14_600, 2024, "single", fica=False)
        assert r.federal_income_tax == 0.0

    def test_10pct_bracket_only(self):
        # $20,000 gross → $5,400 taxable → all in 10% bracket
        # Tax = $5,400 × 10% = $540
        r = compute_liability(20_000, 2024, "single", fica=False)
        assert_near(r.taxable_income, 5_400.0, label="taxable_income")
        assert_near(r.federal_income_tax, 540.0, label="federal_income_tax")
        assert r.marginal_rate == 0.10

    def test_crosses_10_12_bracket(self):
        # $50,000 gross → $35,400 taxable
        # 10% on $11,600 = $1,160
        # 12% on ($35,400 - $11,600) = 12% × $23,800 = $2,856
        # Total = $4,016
        r = compute_liability(50_000, 2024, "single", fica=False)
        assert_near(r.taxable_income, 35_400.0, label="taxable_income")
        assert_near(r.federal_income_tax, 4_016.0, label="federal_income_tax")
        assert r.marginal_rate == 0.12

    def test_crosses_into_22pct(self):
        # $80,000 gross → $65,400 taxable
        # 10% on $11,600 = $1,160
        # 12% on ($47,150 - $11,600) = 12% × $35,550 = $4,266
        # 22% on ($65,400 - $47,150) = 22% × $18,250 = $4,015
        # Total = $9,441
        r = compute_liability(80_000, 2024, "single", fica=False)
        assert_near(r.taxable_income, 65_400.0, label="taxable_income")
        assert_near(r.federal_income_tax, 9_441.0, label="federal_income_tax")
        assert r.marginal_rate == 0.22

    def test_100k_crosses_22pct(self):
        # $100,000 gross → $85,400 taxable
        # 10% on $11,600 = $1,160
        # 12% on $35,550  = $4,266
        # 22% on ($85,400 - $47,150) = 22% × $38,250 = $8,415
        # Total = $13,841
        r = compute_liability(100_000, 2024, "single", fica=False)
        assert_near(r.taxable_income, 85_400.0, label="taxable_income")
        assert_near(r.federal_income_tax, 13_841.0, label="federal_income_tax")
        assert r.marginal_rate == 0.22

    def test_crosses_24pct(self):
        # $200,000 gross → $185,400 taxable
        # 10% × $11,600   = $1,160
        # 12% × $35,550   = $4,266
        # 22% × $53,375   = $11,742.50  (100,525-47,150)
        # 24% × ($185,400-$100,525) = 24% × $84,875 = $20,370
        # Total ≈ $37,538
        r = compute_liability(200_000, 2024, "single", fica=False)
        assert_near(r.taxable_income, 185_400.0, label="taxable_income")
        assert_near(r.federal_income_tax, 37_538.50, tol=2.0, label="federal_income_tax")
        assert r.marginal_rate == 0.24

    def test_top_bracket(self):
        # $700,000 gross → $685,400 taxable → hits 37% bracket
        r = compute_liability(700_000, 2024, "single", fica=False)
        assert r.marginal_rate == 0.37

    def test_effective_rate_is_not_marginal(self):
        # This is THE most common LLM failure mode.
        # $100,000 single: marginal rate = 22%, effective rate ≈ 13.8%
        # An LLM that reports "22%" has confused marginal and effective.
        r = compute_liability(100_000, 2024, "single", fica=False)
        assert r.marginal_rate == 0.22
        # Effective rate must be less than marginal rate for multi-bracket income
        assert r.effective_rate < r.marginal_rate
        # And it must be less than 22%
        assert r.effective_rate < 0.22


# ── Federal Income Tax — Married Filing Jointly ───────────────────────────────

class TestFederalTaxMFJ:
    """
    MFJ brackets are NOT simply double the single brackets in all cases.
    Most LLMs apply single brackets to MFJ filers. These tests catch that.
    """

    def test_standard_deduction_is_doubled(self):
        # $29,200 vs $14,600 for single — MFJ gets double
        assert _STANDARD_DEDUCTIONS_2024["married_filing_jointly"] == 29_200.0

    def test_50k_mfj_vs_single(self):
        # Same income, MFJ should pay LESS because higher deduction + wider brackets
        mfj    = compute_liability(50_000, 2024, "married_filing_jointly", fica=False)
        single = compute_liability(50_000, 2024, "single", fica=False)
        assert mfj.federal_income_tax < single.federal_income_tax, (
            "MFJ should always pay less federal tax than single at same income"
        )

    def test_mfj_100k(self):
        # $100,000 gross MFJ → $70,800 taxable
        # 10% × $23,200 = $2,320
        # 12% × ($70,800 - $23,200) = 12% × $47,600 = $5,712
        # Total = $8,032
        r = compute_liability(100_000, 2024, "married_filing_jointly", fica=False)
        assert_near(r.taxable_income, 70_800.0, label="MFJ taxable_income")
        assert_near(r.federal_income_tax, 8_032.0, label="MFJ federal_tax")
        assert r.marginal_rate == 0.12

    def test_mfj_250k_crosses_22_24(self):
        r = compute_liability(250_000, 2024, "married_filing_jointly", fica=False)
        assert r.marginal_rate == 0.24


# ── Federal Income Tax — Head of Household ────────────────────────────────────

class TestFederalTaxHOH:
    """
    HOH is a separate filing status with its own bracket table.
    Virtually every LLM tested applies either single or MFJ brackets to HOH.
    """

    def test_hoh_standard_deduction(self):
        assert _STANDARD_DEDUCTIONS_2024["head_of_household"] == 21_900.0

    def test_hoh_different_from_single(self):
        # At $60,000, HOH should pay less than single
        hoh    = compute_liability(60_000, 2024, "head_of_household", fica=False)
        single = compute_liability(60_000, 2024, "single", fica=False)
        assert hoh.federal_income_tax < single.federal_income_tax

    def test_hoh_75k(self):
        # $75,000 HOH → $75,000 - $21,900 = $53,100 taxable
        # 10% × $16,550 = $1,655
        # 12% × ($53,100 - $16,550) = 12% × $36,550 = $4,386
        # Total = $6,041
        r = compute_liability(75_000, 2024, "head_of_household", fica=False)
        assert_near(r.taxable_income, 53_100.0, label="HOH taxable_income")
        assert_near(r.federal_income_tax, 6_041.0, label="HOH federal_tax")


# ── FICA Tests ────────────────────────────────────────────────────────────────

class TestFICA:
    """
    FICA is where models fail on high-income scenarios.
    SS caps at $168,600 wage base. Medicare has no cap.
    Additional Medicare kicks in at $200K.
    """

    def test_social_security_below_wage_base(self):
        # $100,000: SS = $100,000 × 6.2% = $6,200
        r = compute_liability(100_000, 2024, "single")
        assert_near(r.social_security_tax, 6_200.0, label="SS tax $100k")

    def test_social_security_caps_at_wage_base(self):
        # $200,000: SS = $168,600 × 6.2% = $10,453.20 (NOT $200k × 6.2%)
        r = compute_liability(200_000, 2024, "single")
        assert_near(r.social_security_tax, 10_453.20, label="SS tax $200k")

    def test_social_security_above_wage_base(self):
        # $300,000: SS still capped at $168,600 × 6.2% = $10,453.20
        r300 = compute_liability(300_000, 2024, "single")
        r200 = compute_liability(200_000, 2024, "single")
        assert_near(r300.social_security_tax, r200.social_security_tax,
                    label="SS should be same above wage base")

    def test_medicare_no_cap(self):
        # Medicare applies to full income with no cap
        r = compute_liability(500_000, 2024, "single")
        base_medicare = 500_000 * 0.0145
        # Plus additional 0.9% on ($500k - $200k) = $300k × 0.009 = $2,700
        additional    = 300_000 * 0.009
        expected      = base_medicare + additional
        assert_near(r.medicare_tax, expected, label="Medicare $500k")

    def test_fica_vs_no_fica(self):
        # FICA flag should change total but not federal_income_tax
        with_fica    = compute_liability(100_000, 2024, "single", fica=True)
        without_fica = compute_liability(100_000, 2024, "single", fica=False)
        assert with_fica.federal_income_tax == without_fica.federal_income_tax
        assert with_fica.total_tax_liability > without_fica.total_tax_liability


# ── Edge Case Tests ───────────────────────────────────────────────────────────

class TestEdgeCases:
    """
    Bracket boundary cases — exactly at the transition points.
    These are the cases the task_generator puts in 'adversarial' difficulty.
    """

    def test_exactly_at_10_12_boundary_single(self):
        # $11,600 taxable income — just at the 10/12% boundary
        # Tax = $11,600 × 10% = $1,160. The $11,600 itself is taxed at 10%.
        r = compute_liability(11_600 + 14_600, 2024, "single", fica=False)
        # taxable = $11,600 exactly
        assert_near(r.taxable_income, 11_600.0, label="at 10/12 boundary")
        assert_near(r.federal_income_tax, 1_160.0, label="tax at 10/12 boundary")
        assert r.marginal_rate == 0.10

    def test_one_dollar_above_10_12_boundary(self):
        # $11,601 taxable — the $1 above enters the 12% bracket
        # But the marginal rate flips to 12%
        gross = 11_601 + 14_600
        r = compute_liability(gross, 2024, "single", fica=False)
        assert r.marginal_rate == 0.12

    def test_fica_wage_base_boundary(self):
        # Exactly at $168,600 — SS applies to full amount
        r1 = compute_liability(168_600, 2024, "single")
        r2 = compute_liability(168_601, 2024, "single")
        # The extra $1 should not increase SS tax (it's above the cap)
        assert abs(r1.social_security_tax - r2.social_security_tax) < 0.01

    def test_negative_income_raises(self):
        with pytest.raises(ValueError):
            compute_liability(-1, 2024, "single")

    def test_invalid_filing_status_raises(self):
        with pytest.raises((ValueError, KeyError)):
            compute_liability(50_000, 2024, "invalid_status")  # type: ignore

    def test_unsupported_tax_year_raises(self):
        with pytest.raises(NotImplementedError):
            compute_liability(50_000, 2023, "single")


# ── Marginal Rate Tests ───────────────────────────────────────────────────────

class TestMarginalRate:
    def test_zero_taxable(self):
        assert get_marginal_rate(0, "single") == 0.0

    def test_in_10pct(self):
        assert get_marginal_rate(5_000, "single") == 0.10

    def test_in_22pct(self):
        assert get_marginal_rate(60_000, "single") == 0.22

    def test_top_bracket(self):
        assert get_marginal_rate(700_000, "single") == 0.37


# ── Grader / Scoring Tests ────────────────────────────────────────────────────

class TestScoring:
    """
    Verify the partial-credit grader behaves correctly.
    This is the evaluation metric — it must be airtight.
    """

    @pytest.fixture
    def truth(self):
        # Ground truth: $100k single, total liability ~$21,491
        return compute_liability(100_000, 2024, "single")

    def test_exact_answer_scores_1(self, truth):
        score = score_agent_answer(truth.total_tax_liability, truth)
        assert score == 1.0

    def test_within_tolerance_scores_1(self, truth):
        # Within 0.5% of correct → full score
        near = truth.total_tax_liability * 1.004
        score = score_agent_answer(near, truth)
        assert score == 1.0

    def test_forgot_fica_scores_07(self, truth):
        # Agent returned only federal income tax, forgot FICA
        score = score_agent_answer(truth.federal_income_tax, truth)
        assert score == 0.7, f"Expected 0.7 for forgot-FICA, got {score}"

    def test_wildly_wrong_scores_0(self, truth):
        score = score_agent_answer(999_999.0, truth)
        assert score == 0.0

    def test_zero_score_not_negative(self, truth):
        score = score_agent_answer(-100.0, truth)
        assert score >= 0.0


# ── Consistency / Regression Tests ───────────────────────────────────────────

class TestConsistency:
    """Sanity checks that should always pass regardless of implementation."""

    def test_after_tax_income_adds_up(self):
        for income in [30_000, 100_000, 300_000]:
            r = compute_liability(income, 2024, "single")
            expected_after_tax = round(income - r.total_tax_liability, 2)
            assert r.after_tax_income == expected_after_tax

    def test_effective_rate_between_0_and_1(self):
        for income in [0, 10_000, 50_000, 200_000, 800_000]:
            r = compute_liability(income, 2024, "single")
            assert 0.0 <= r.effective_rate <= 1.0

    def test_higher_income_higher_tax(self):
        # Progressive system: more income must mean more tax
        incomes = [30_000, 50_000, 100_000, 200_000, 500_000]
        taxes   = [compute_liability(i, 2024, "single").total_tax_liability
                   for i in incomes]
        for i in range(1, len(taxes)):
            assert taxes[i] > taxes[i - 1], \
                f"Tax at ${incomes[i]:,} <= tax at ${incomes[i-1]:,} — not progressive"

    def test_higher_income_higher_effective_rate(self):
        # Effective rate must also be monotonically increasing (progressive)
        incomes = [50_000, 100_000, 200_000, 500_000]
        rates   = [compute_liability(i, 2024, "single").effective_rate
                   for i in incomes]
        for i in range(1, len(rates)):
            assert rates[i] > rates[i - 1], \
                f"Effective rate at ${incomes[i]:,} <= rate at ${incomes[i-1]:,}"

    def test_all_filing_statuses_produce_valid_results(self):
        for status in ["single", "married_filing_jointly", "head_of_household"]:
            r = compute_liability(75_000, 2024, status)
            assert r.total_tax_liability >= 0
            assert r.federal_income_tax >= 0
            assert r.taxable_income >= 0
