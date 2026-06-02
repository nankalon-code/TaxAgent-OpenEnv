"""
international_taxes.py — Multi-Country Income Tax Engine (2024)
Sources: OECD Tax Database, official government publications.
"""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class CountryTaxResult:
    country: str; currency: str; gross_income: float
    taxable_income: float; income_tax: float
    social_contributions: float; total_liability: float
    effective_rate: float; marginal_rate: float
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Country           : {self.country}",
            f"Gross Income      : {self.currency}{self.gross_income:>12,.2f}",
            f"Taxable Income    : {self.currency}{self.taxable_income:>12,.2f}",
            f"Income Tax        : {self.currency}{self.income_tax:>12,.2f}",
            f"Social/Payroll    : {self.currency}{self.social_contributions:>12,.2f}",
            f"{'─'*42}",
            f"Total Liability   : {self.currency}{self.total_liability:>12,.2f}",
            f"Effective Rate    : {self.effective_rate*100:.2f}%",
            f"Marginal Rate     : {self.marginal_rate*100:.2f}%",
        ]
        for n in self.notes: lines.append(f"NOTE: {n}")
        return "\n".join(lines)


def _bracket_tax(taxable: float, brackets: list[tuple]) -> tuple[float, float]:
    """Returns (total_tax, marginal_rate)."""
    total = 0.0; marginal = 0.0
    for (lo, hi, rate) in brackets:
        if taxable <= lo: break
        top = float('inf') if hi is None else hi
        total += (min(taxable, top) - lo) * rate
        marginal = rate
    return round(total, 2), marginal


# ─── INDIA 2024-25 (New Tax Regime — default post Budget 2024) ───────────────
# Source: Finance Act 2024, Section 115BAC
_IN_BRACKETS = [(0,300000,0),(300000,700000,0.05),(700000,1000000,0.10),
                (1000000,1200000,0.15),(1200000,1500000,0.20),(1500000,None,0.30)]
_IN_STD_DED = 75_000  # Budget 2024 increased from ₹50k to ₹75k

def _india(gross: float, regime: str = "new") -> CountryTaxResult:
    notes = []
    if regime == "new":
        taxable = max(0.0, gross - _IN_STD_DED)
        tax, marg = _bracket_tax(taxable, _IN_BRACKETS)
        # Section 87A rebate: full rebate if income ≤ ₹7,00,000
        if gross <= 700_000 and tax > 0:
            tax = 0.0; notes.append("Section 87A rebate applied — zero tax liability")
        # Surcharge
        surcharge = 0.0
        if gross > 5_000_000: surcharge = tax * 0.25
        elif gross > 2_000_000: surcharge = tax * 0.25
        elif gross > 1_000_000: surcharge = tax * 0.15
        elif gross > 500_000: surcharge = tax * 0.10
        if surcharge: notes.append(f"Surcharge: ₹{surcharge:,.0f}")
        cess = (tax + surcharge) * 0.04  # 4% Health & Education Cess
        income_tax = round(tax + surcharge + cess, 2)
        notes.append(f"4% H&E Cess: ₹{cess:,.0f}")
    else:
        # Old regime (simplified)
        old_br = [(0,250000,0),(250000,500000,0.05),(500000,1000000,0.20),(1000000,None,0.30)]
        taxable = gross  # taxpayer claims own deductions
        income_tax, marg = _bracket_tax(taxable, old_br)
        income_tax = round(income_tax * 1.04, 2)  # cess
    # EPF employee contribution: 12% on basic (capped ₹15,000/month = ₹1,80,000/yr)
    epf = min(gross * 0.12, 180_000)
    notes.append(f"EPF employee contribution (12%, capped): ₹{epf:,.0f}")
    total = income_tax + epf
    eff = total / gross if gross > 0 else 0.0
    taxable_out = max(0.0, gross - _IN_STD_DED) if regime == "new" else gross
    return CountryTaxResult("India (New Regime 2024-25)", "₹", gross,
        taxable_out, income_tax, epf, round(total,2), round(eff,6), marg, notes)


# ─── UNITED KINGDOM 2024-25 ──────────────────────────────────────────────────
# Source: HMRC rates and thresholds 2024-25
_UK_BRACKETS = [(12570,50270,0.20),(50270,125140,0.40),(125140,None,0.45)]
_UK_PERSONAL_ALLOWANCE = 12_570

def _uk(gross: float) -> CountryTaxResult:
    notes = []
    # Personal allowance tapers: lose £1 for every £2 over £100,000
    pa = _UK_PERSONAL_ALLOWANCE
    if gross > 100_000:
        taper = min(pa, (gross - 100_000) / 2)
        pa = max(0, pa - taper)
        if pa == 0: notes.append("Personal allowance fully withdrawn (income > £125,140)")
    taxable = max(0.0, gross - pa)
    income_tax, marg = _bracket_tax(taxable, _UK_BRACKETS)
    # NI Class 1 (employee) 2024-25: 8% on £12,570-£50,270, 2% above
    ni_primary = min(max(0, gross - 12_570), 50_270 - 12_570) * 0.08
    ni_upper   = max(0, gross - 50_270) * 0.02
    ni = round(ni_primary + ni_upper, 2)
    notes.append(f"NI Class 1: £{ni:,.2f} (8% up to £50,270; 2% above)")
    total = round(income_tax + ni, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("United Kingdom 2024-25", "£", gross,
        round(taxable,2), round(income_tax,2), ni, total, round(eff,6), marg, notes)


# ─── CANADA 2024 (Federal only) ──────────────────────────────────────────────
# Source: CRA T1 General 2024, IT-497R4
# Note: Provincial tax varies. Alberta ~10% flat; Ontario ~5.05-13.16% progressive
_CA_BRACKETS = [(0,55867,0.15),(55867,111733,0.205),(111733,154906,0.26),
                (154906,220000,0.29),(220000,None,0.33)]
_CA_BPA = 15_705  # Basic Personal Amount 2024

def _canada(gross: float, province: str = "ON") -> CountryTaxResult:
    notes = [f"Federal tax only shown. Province: {province} (add ~6-16% provincial)"]
    taxable = max(0.0, gross - _CA_BPA)
    # Apply BPA as a 15% credit (non-refundable)
    fed_tax, marg = _bracket_tax(taxable, _CA_BRACKETS)
    bpa_credit = _CA_BPA * 0.15
    fed_tax = max(0.0, fed_tax - bpa_credit)
    # CPP (Canada Pension Plan) employee: 5.95% on $3,500-$68,500
    cpp = min(max(0, gross - 3_500), 68_500 - 3_500) * 0.0595
    # EI (Employment Insurance): 1.66% up to $63,200 insurable earnings
    ei  = min(gross, 63_200) * 0.0166
    notes.append(f"CPP: ${cpp:,.2f} | EI: ${ei:,.2f}")
    total = round(fed_tax + cpp + ei, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Canada 2024 (Federal)", "CA$", gross,
        round(taxable,2), round(fed_tax,2), round(cpp+ei,2), total, round(eff,6), marg, notes)


# ─── GERMANY 2024 ────────────────────────────────────────────────────────────
# Source: EStG §32a, BMF 2024
# Germany uses a continuous formula — we approximate with fine brackets
_DE_GRUNDFREIBETRAG = 11_784
def _germany(gross: float) -> CountryTaxResult:
    notes = []
    z = max(0.0, gross)
    # German income tax formula (simplified linear approximation zones)
    if z <= 11_784: tax = 0.0; marg = 0.0
    elif z <= 17_005:
        y = (z - 11_784) / 10_000
        tax = (979.18 * y + 1_400) * y; marg = 0.14
    elif z <= 66_760:
        y = (z - 17_005) / 10_000
        tax = (192.59 * y + 2_397) * y + 966.53; marg = 0.24
    elif z <= 277_825:
        tax = 0.42 * z - 10_602.13; marg = 0.42
    else:
        tax = 0.45 * z - 18_936.88; marg = 0.45
    tax = max(0.0, round(tax, 2))
    # Solidarity surcharge: 5.5% of income tax, only if tax > €18,130 (2024)
    soli = round(tax * 0.055, 2) if tax > 18_130 else 0.0
    if soli: notes.append(f"Solidarity surcharge (Solidaritätszuschlag): €{soli:,.2f}")
    # Social security (employee half): ~20.5% total but capped
    # Pension 9.3%, Health 7.3%, Unemployment 1.3%, Care 1.8% = ~19.7% approx
    social_ceiling = min(gross, 90_600)  # BBG West 2024 (pension/unemployment)
    health_ceiling = min(gross, 62_100)  # Krankenversicherung ceiling
    pension = social_ceiling * 0.093
    health  = health_ceiling * 0.073
    unemp   = social_ceiling * 0.013
    care    = gross * 0.018  # no ceiling for care insurance
    social  = round(pension + health + unemp + care, 2)
    notes.append(f"Social: Pension €{pension:,.0f} + Health €{health:,.0f} + Unemp €{unemp:,.0f} + Care €{care:,.0f}")
    total = round(tax + soli + social, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Germany 2024", "€", gross, round(max(0,gross-_DE_GRUNDFREIBETRAG),2),
        round(tax+soli,2), social, total, round(eff,6), marg, notes)


# ─── AUSTRALIA 2024-25 (Stage 3 tax cuts effective 1 July 2024) ──────────────
# Source: ATO 2024-25 individual income tax rates
_AU_BRACKETS = [(0,18200,0),(18200,45000,0.19),(45000,135000,0.325),
                (135000,190000,0.37),(190000,None,0.45)]
_AU_LITO_MAX = 700  # Low Income Tax Offset (max $700, phases out $37,500-$45,000 and $45,000-$66,667)
_MEDICARE_LEVY = 0.02

def _australia(gross: float) -> CountryTaxResult:
    notes = []
    income_tax, marg = _bracket_tax(gross, _AU_BRACKETS)
    # LITO: reduces tax for lower incomes
    if gross <= 37_500: lito = _AU_LITO_MAX
    elif gross <= 45_000: lito = _AU_LITO_MAX - (gross - 37_500) * 0.05
    elif gross <= 66_667: lito = 325 - (gross - 45_000) * 0.015
    else: lito = 0.0
    lito = max(0.0, round(lito, 2))
    income_tax = max(0.0, round(income_tax - lito, 2))
    if lito: notes.append(f"Low Income Tax Offset (LITO): A${lito:,.2f}")
    # Medicare Levy: 2% (exemptions apply below $26,000 but simplified here)
    medicare = round(gross * _MEDICARE_LEVY, 2) if gross > 26_000 else 0.0
    notes.append(f"Medicare Levy (2%): A${medicare:,.2f}")
    # Superannuation: employer pays 11.5% — not employee deduction, shown for context
    notes.append("Super guarantee (11.5%): paid by employer, not deducted from gross")
    total = round(income_tax + medicare, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Australia 2024-25", "A$", gross, gross,
        income_tax, medicare, total, round(eff,6), marg, notes)


# ─── JAPAN 2024 ──────────────────────────────────────────────────────────────
# Source: NTA (National Tax Agency) 2024, No.2260
_JP_NATIONAL_BRACKETS = [(0,1950000,0.05),(1950000,3300000,0.10),(3300000,6950000,0.20),
                          (6950000,9000000,0.23),(9000000,18000000,0.33),
                          (18000000,40000000,0.40),(40000000,None,0.45)]
_JP_BASIC_EXEMPTION = 480_000

def _japan(gross: float) -> CountryTaxResult:
    notes = []
    taxable = max(0.0, gross - _JP_BASIC_EXEMPTION)
    national_tax, marg = _bracket_tax(taxable, _JP_NATIONAL_BRACKETS)
    # Special reconstruction tax: 2.1% of national income tax (until 2037)
    reconstruction = round(national_tax * 0.021, 2)
    notes.append(f"Reconstruction Special Tax (2.1%): ¥{reconstruction:,.0f}")
    # Residence tax: approximately 10% of taxable income (flat, collected next year)
    residence = round(taxable * 0.10, 2)
    notes.append(f"Residence Tax (~10% flat): ¥{residence:,.0f} (collected following year)")
    income_tax = round(national_tax + reconstruction, 2)
    # Social insurance: ~14.5% approx (health 5%, pension 9.15%, employment 0.6%)
    social_ceiling = min(gross, 7_590_000)  # approx pension ceiling
    social = round(social_ceiling * 0.1475, 2)
    notes.append(f"Social Insurance (~14.75%, capped): ¥{social:,.0f}")
    total = round(income_tax + residence + social, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Japan 2024", "¥", gross, round(taxable,2),
        income_tax, round(residence+social,2), total, round(eff,6), marg, notes)


# ─── SINGAPORE 2024 (Year of Assessment 2024) ────────────────────────────────
# Source: IRAS, Income Tax Act
_SG_BRACKETS = [(0,20000,0),(20000,30000,0.02),(30000,40000,0.035),(40000,80000,0.07),
                (80000,120000,0.115),(120000,160000,0.15),(160000,200000,0.18),
                (200000,240000,0.19),(240000,280000,0.195),(280000,320000,0.20),
                (320000,500000,0.22),(500000,1000000,0.23),(1000000,None,0.24)]
_SG_EARNED_INCOME_RELIEF = 1_000  # basic EIR for under 55

def _singapore(gross: float, age: int = 35) -> CountryTaxResult:
    notes = []
    taxable = max(0.0, gross - _SG_EARNED_INCOME_RELIEF)
    income_tax, marg = _bracket_tax(taxable, _SG_BRACKETS)
    # CPF (Central Provident Fund) employee contribution
    if age <= 55: cpf_rate = 0.20
    elif age <= 60: cpf_rate = 0.15
    elif age <= 65: cpf_rate = 0.105
    else: cpf_rate = 0.075
    # CPF applies to Ordinary Wages up to S$6,800/month = S$81,600/year
    cpf_ceiling = min(gross, 81_600)
    cpf = round(cpf_ceiling * cpf_rate, 2)
    notes.append(f"CPF employee ({cpf_rate*100:.1f}%, OW ceiling S$81,600): S${cpf:,.2f}")
    total = round(income_tax + cpf, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Singapore 2024", "S$", gross, round(taxable,2),
        round(income_tax,2), cpf, total, round(eff,6), marg, notes)


# ─── FRANCE 2024 ─────────────────────────────────────────────────────────────
# Source: CGI Art. 197, BOFIP 2024
_FR_BRACKETS = [(0,11294,0),(11294,28797,0.11),(28797,82341,0.30),
                (82341,177106,0.41),(177106,None,0.45)]
_FR_FAMILY_QUOTIENT_PARTS = {"single": 1, "married": 2, "one_child": 2.5}

def _france(gross: float, situation: str = "single") -> CountryTaxResult:
    notes = []
    parts = _FR_FAMILY_QUOTIENT_PARTS.get(situation, 1)
    # French tax applies to income / parts, then multiplied by parts
    income_per_part = gross / parts
    tax_per_part, marg = _bracket_tax(income_per_part, _FR_BRACKETS)
    income_tax = round(tax_per_part * parts, 2)
    if parts > 1: notes.append(f"Quotient familial: {parts} parts → tax reduced")
    # Social contributions (CSG/CRDS) on salary: 9.7% (7.5% CSG + 0.5% CRDS + 1.7% CSG)
    social_rate = 0.097
    social = round(gross * social_rate, 2)
    notes.append(f"CSG/CRDS social contributions ({social_rate*100:.1f}%): €{social:,.2f}")
    total = round(income_tax + social, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("France 2024", "€", gross, gross,
        income_tax, social, total, round(eff,6), marg, notes)


# ─── UAE (No personal income tax) ────────────────────────────────────────────
def _uae(gross: float) -> CountryTaxResult:
    return CountryTaxResult("UAE 2024", "AED", gross, gross, 0.0, 0.0, 0.0, 0.0, 0.0,
        ["UAE has NO personal income tax (Federal Decree-Law No. 47 of 2022 applies to corporate only)"])


# ─── BRAZIL 2024 ─────────────────────────────────────────────────────────────
# Source: Receita Federal, Tabela Progressiva Mensal 2024 (annualized)
_BR_BRACKETS = [(0,26963.20,0),(26963.20,33919.80,0.075),(33919.80,45012.60,0.15),
                (45012.60,55976.16,0.225),(55976.16,None,0.275)]
_BR_DED_PER_DEPENDENT = 2_275.08  # annual per dependent

def _brazil(gross: float, dependents: int = 0) -> CountryTaxResult:
    notes = []
    ded = dependents * _BR_DED_PER_DEPENDENT
    taxable = max(0.0, gross - ded)
    income_tax, marg = _bracket_tax(taxable, _BR_BRACKETS)
    # INSS (employee): tiered 7.5%-14% capped at R$7,786.02/month = R$93,432.24/year
    inss_brackets = [(0,21947.04,0.075),(21947.04,43214.88,0.09),
                     (43214.88,64919.52,0.12),(64919.52,93432.24,0.14)]
    inss, _ = _bracket_tax(gross, inss_brackets)
    notes.append(f"INSS (social security, employee): R${inss:,.2f}")
    if dependents: notes.append(f"{dependents} dependent(s) deduction: R${ded:,.2f}")
    total = round(income_tax + inss, 2)
    eff = total / gross if gross > 0 else 0.0
    return CountryTaxResult("Brazil 2024", "R$", gross, round(taxable,2),
        round(income_tax,2), round(inss,2), total, round(eff,6), marg, notes)


# ─── Dispatcher ──────────────────────────────────────────────────────────────

SUPPORTED_COUNTRIES = {
    "US": "Use tax_engine.compute_liability() for US federal",
    "IN": "India",  "GB": "United Kingdom", "CA": "Canada",
    "DE": "Germany","AU": "Australia",      "JP": "Japan",
    "SG": "Singapore","FR": "France",       "AE": "UAE (no tax)",
    "BR": "Brazil",
}

def compute_country_liability(
    gross_income: float,
    country_code: str,
    **kwargs
) -> CountryTaxResult:
    """
    Compute income tax for a given country.
    country_code: ISO 3166-1 alpha-2 (IN, GB, CA, DE, AU, JP, SG, FR, AE, BR)
    Additional kwargs are passed to country-specific functions.
    US: use tax_engine.compute_liability() directly.
    """
    c = country_code.upper()
    if c == "US":
        raise ValueError("Use tax_engine.compute_liability() for US. Full bracket engine there.")
    if c == "IN": return _india(gross_income, kwargs.get("regime", "new"))
    if c == "GB": return _uk(gross_income)
    if c == "CA": return _canada(gross_income, kwargs.get("province", "ON"))
    if c == "DE": return _germany(gross_income)
    if c == "AU": return _australia(gross_income)
    if c == "JP": return _japan(gross_income)
    if c == "SG": return _singapore(gross_income, kwargs.get("age", 35))
    if c == "FR": return _france(gross_income, kwargs.get("situation", "single"))
    if c == "AE": return _uae(gross_income)
    if c == "BR": return _brazil(gross_income, kwargs.get("dependents", 0))
    raise ValueError(f"Country '{country_code}' not supported. Supported: {list(SUPPORTED_COUNTRIES.keys())}")


if __name__ == "__main__":
    DEMO = [("IN",50_00_000),("GB",85_000),("CA",120_000),("DE",80_000),
            ("AU",100_000),("JP",8_000_000),("SG",150_000),("FR",75_000),
            ("AE",500_000),("BR",120_000)]
    for code, income in DEMO:
        r = compute_country_liability(income, code)
        print(f"\n{'='*50}")
        print(r.summary())
