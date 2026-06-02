"""
test_international_taxes.py — Pytest Suite for international_taxes.py
======================================================================
Verifies the mathematical accuracy of the multi-jurisdictional tax engine
supporting 10 global tax codes (India, UK, Canada, Germany, Australia,
Japan, Singapore, France, UAE, Brazil).
"""

import pytest
from international_taxes import compute_country_liability, CountryTaxResult

# Tolerance for rounding variations
TOLERANCE = 1.00

def test_india_new_regime():
    # ₹5,00,000 gross in India (New Regime)
    # Gross <= 7L gets Section 87A rebate -> Zero tax liability
    res = compute_country_liability(500_000, "IN", regime="new")
    assert res.income_tax == 0.0
    assert res.social_contributions > 0.0  # EPF contribution

    # ₹10,00,000 gross in India (New Regime)
    # Std deduction = ₹75,000 -> Taxable = ₹9,25,000
    # Brackets: 0-3L (0), 3-7L (5% on 4L = 20k), 7-9.25L (10% on 2.25L = 22.5k)
    # Base Tax = 42,500
    # 4% Cess = 1,700
    # Expected Tax = 44,200
    res2 = compute_country_liability(1_000_000, "IN", regime="new")
    assert abs(res2.income_tax - 44200.0) <= TOLERANCE
    assert res2.currency == "₹"


def test_uk_tapering():
    # £50,000 gross (below tapering threshold £100,000)
    # Personal Allowance = 12,570
    # Taxable = 37,430
    # 20% on 37,430 = 7,486
    res = compute_country_liability(50_000, "GB")
    assert abs(res.income_tax - 7486.0) <= TOLERANCE
    assert res.currency == "£"

    # £150,000 gross (PA fully withdrawn since gross > 125,140)
    res2 = compute_country_liability(150_000, "GB")
    # PA should be 0. Taxable = 150,000
    # Brackets: 0-37700 (20% = 7540), 37700-125140 (40% = 34976), 125140-150000 (45% = 11187)
    # Total Expected = 53,703
    assert abs(res2.income_tax - 53703.0) <= TOLERANCE


def test_canada_bpa_credit():
    # CA$80,000 gross
    # Basic Personal Amount credit = 15,705 * 15% = 2,355.75
    # Brackets: 15% on first 55,867 = 8380.05
    # 20.5% on (80,000 - BPA - 55,867) -> Wait, Canada applies BPA as deduction first in our engine:
    # Taxable = 80,000 - 15,705 = 64,295
    # Brackets: 15% on 55,867 = 8,380.05, 20.5% on (64,295 - 55,867) = 20.5% * 8,428 = 1,727.74
    # Total before credit = 10,107.79
    # BPA Credit deduction = 15,705 * 15% = 2,355.75
    # Expected Federal Tax = 7,752.04
    res = compute_country_liability(80_000, "CA")
    assert abs(res.income_tax - 7752.04) <= TOLERANCE
    assert res.currency == "CA$"


def test_germany_free_allowance():
    # €10,000 gross is below Grundfreibetrag (€11,784) -> Zero tax
    res = compute_country_liability(10_000, "DE")
    assert res.income_tax == 0.0
    assert res.currency == "€"


def test_australia_stage_3():
    # A$100,000 gross
    # Stage 3 brackets 2024-25: 18200-45000 (19%), 45000-135000 (32.5%)
    # Tax = (45,000 - 18,200) * 0.19 + (100,000 - 45,000) * 0.325 = 5,092 + 17,875 = 22,967
    res = compute_country_liability(100_000, "AU")
    assert abs(res.income_tax - 22967.0) <= TOLERANCE
    assert res.social_contributions == round(100_000 * 0.02, 2)  # Medicare Levy (2%)
    assert res.currency == "A$"


def test_singapore_low_tax():
    # S$100,000 gross
    # Brackets: 0-20k (0), 20-30k (2% = 200), 30-40k (3.5% = 350), 40-80k (7% = 2800), 80-99k (11.5% on 19k = 2185)
    # Total Expected = 5,535
    res = compute_country_liability(100_000, "SG", age=30)
    assert abs(res.income_tax - 5535.0) <= TOLERANCE
    assert res.currency == "S$"


def test_uae_zero_tax():
    # AED 500,000 gross -> Zero tax
    res = compute_country_liability(500_000, "AE")
    assert res.income_tax == 0.0
    assert res.total_liability == 0.0
    assert res.currency == "AED"


def test_france_quotient():
    # €80,000 single situation -> 1 family parts
    res_single = compute_country_liability(80_000, "FR", situation="single")
    # €80,000 married situation -> 2 family parts (split income, lower tax)
    res_married = compute_country_liability(80_000, "FR", situation="married")
    assert res_married.income_tax < res_single.income_tax


def test_brazil_inss():
    # R$60,000 gross, 0 dependents
    res = compute_country_liability(60_000, "BR", dependents=0)
    assert res.income_tax > 0.0
    assert res.social_contributions > 0.0  # INSS
    assert res.currency == "R$"
