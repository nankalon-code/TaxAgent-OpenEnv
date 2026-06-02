"""
task_generator.py — TaxAgent-OpenEnv | Parameterized Task Generator
=====================================================================

The original environment had 3 hardcoded tasks. Three. You can overfit
to 3 tasks with a lookup table. This module generates an arbitrarily
large suite of reproducible, difficulty-stratified tax scenarios.

Design:
- Seed-controlled randomness (reproducible benchmarks, no data leakage)
- 4 difficulty tiers mapped to realistic demographic profiles
- Each task exposes exactly the information a real tax professional sees
- Tasks include deliberate edge cases (near-bracket boundaries, FICA cap
  crossings, zero tax scenarios) that expose model reasoning failures

Publication note: generate 500 tasks with seed=42, evaluate your model,
report mean score ± std. That's a publishable benchmark. 3 tasks is not.
"""

from __future__ import annotations
import random
import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Iterator
from tax_engine import (
    FilingStatus,
    TaxComputation,
    compute_liability,
    _STANDARD_DEDUCTIONS_2024,
    _SOCIAL_SECURITY_WAGE_BASE,
)

# ── Task difficulty definitions ───────────────────────────────────────────────

@dataclass
class TaxTask:
    """
    A single evaluation task for a tax agent.

    The agent receives all fields in the `prompt` string.
    It is expected to call tax_engine.compute_liability() and submit
    the total_tax_liability from the resulting TaxComputation.

    ground_truth is NOT visible to the agent — it is used by the grader.
    """
    task_id:        str
    difficulty:     str          # "easy" | "medium" | "hard" | "adversarial"
    gross_income:   float
    filing_status:  FilingStatus
    tax_year:       int
    description:    str          # demographic context for the prompt
    prompt:         str          # what the agent sees
    ground_truth:   TaxComputation   # what the grader checks against

    def to_dict(self) -> dict:
        d = asdict(self)
        # TaxComputation is not JSON-serializable directly
        d["ground_truth"] = {
            "total_tax_liability":  self.ground_truth.total_tax_liability,
            "federal_income_tax":   self.ground_truth.federal_income_tax,
            "taxable_income":       self.ground_truth.taxable_income,
            "effective_rate":       self.ground_truth.effective_rate,
        }
        return d


# ── Difficulty income ranges ──────────────────────────────────────────────────
#
# These ranges correspond to real US income distribution percentiles.
# Choosing income values near bracket boundaries is intentional — that is
# exactly where marginal-vs-effective confusion surfaces in LLMs.

_DIFFICULTY_CONFIG = {
    "easy": {
        "income_range":  (30_000, 80_000),
        "status_weights": {"single": 0.6, "married_filing_jointly": 0.3, "head_of_household": 0.1},
        "description": "Standard W-2 employee, single income source.",
        "edge_cases": False,
    },
    "medium": {
        "income_range":  (80_000, 200_000),
        "status_weights": {"single": 0.4, "married_filing_jointly": 0.4, "head_of_household": 0.2},
        "description": "Mid-career professional, may cross multiple brackets.",
        "edge_cases": False,
    },
    "hard": {
        "income_range":  (200_000, 600_000),
        "status_weights": {"single": 0.35, "married_filing_jointly": 0.5, "head_of_household": 0.15},
        "description": "High-net-worth individual. Crosses FICA wage base cap.",
        "edge_cases": True,
    },
    "adversarial": {
        # Specifically targets known LLM failure modes:
        # - Income exactly at bracket boundaries (triggers off-by-one errors)
        # - Married filing jointly (LLMs often apply single brackets)
        # - Head of household (most LLMs don't know HOH brackets exist)
        # - Income below standard deduction (zero tax — LLMs output positive)
        "income_range":  (0, 800_000),
        "status_weights": {"single": 0.3, "married_filing_jointly": 0.3, "head_of_household": 0.4},
        "description": "Edge case designed to expose reasoning failures.",
        "edge_cases": True,
    },
}

_ADVERSARIAL_INCOME_PINS = [
    # Exactly at 2024 bracket boundaries (single) — ±$1 changes marginal rate
    11_600, 11_601, 47_149, 47_150, 47_151,
    100_524, 100_525, 100_526,
    191_949, 191_950,
    # At FICA wage base
    168_599, 168_600, 168_601,
    # Below standard deduction (zero income tax)
    10_000, 14_599, 14_600,
    # Very high (top bracket)
    610_000, 750_000,
]


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys   = list(weights.keys())
    vals   = list(weights.values())
    return rng.choices(keys, weights=vals, k=1)[0]


def _make_task_id(seed: int, index: int, difficulty: str) -> str:
    raw = f"{seed}-{index}-{difficulty}"
    return "T" + hashlib.sha1(raw.encode()).hexdigest()[:8].upper()


def _build_prompt(income: float, status: FilingStatus, year: int, desc: str) -> str:
    return (
        f"Tax Year: {year}\n"
        f"Filing Status: {status.replace('_', ' ').title()}\n"
        f"Gross W-2 Income: ${income:,.2f}\n"
        f"Profile: {desc}\n"
        f"\n"
        f"Task: Use the tax_engine library to compute the total federal tax\n"
        f"liability (including FICA) for this taxpayer. Call:\n"
        f"  result = tax_engine.compute_liability(\n"
        f"      gross_income={income},\n"
        f"      tax_year={year},\n"
        f"      filing_status='{status}',\n"
        f"  )\n"
        f"Then submit result.total_tax_liability as your final answer.\n"
    )


def generate_tasks(
    n: int,
    seed: int = 42,
    difficulty: str | None = None,
    tax_year: int = 2024,
) -> list[TaxTask]:
    """
    Generate `n` tax evaluation tasks.

    Parameters
    ----------
    n
        Number of tasks to generate.
    seed
        Random seed for full reproducibility. Use the same seed across
        model evaluations to ensure a fair comparison.
    difficulty
        If given, generate only tasks of that difficulty tier. If None,
        generate a stratified mix: 30% easy, 30% medium, 25% hard,
        15% adversarial.
    tax_year
        Tax year to use for all generated tasks.

    Returns
    -------
    list[TaxTask]
        List of fully-specified tasks with ground truth computed.
    """
    rng    = random.Random(seed)
    tasks: list[TaxTask] = []

    # Difficulty distribution
    if difficulty:
        dist = {difficulty: 1.0}
    else:
        dist = {"easy": 0.30, "medium": 0.30, "hard": 0.25, "adversarial": 0.15}

    difficulties = rng.choices(
        list(dist.keys()),
        weights=list(dist.values()),
        k=n,
    )

    adv_pins = _ADVERSARIAL_INCOME_PINS.copy()
    rng.shuffle(adv_pins)
    adv_idx = 0

    for i, diff in enumerate(difficulties):
        cfg    = _DIFFICULTY_CONFIG[diff]
        status = _weighted_choice(rng, cfg["status_weights"])

        if diff == "adversarial" and adv_idx < len(adv_pins):
            income = float(adv_pins[adv_idx])
            adv_idx += 1
        else:
            lo, hi = cfg["income_range"]
            income = round(rng.uniform(lo, hi), 2)

        gt = compute_liability(income, tax_year, status, fica=True)

        task_id = _make_task_id(seed, i, diff)
        prompt  = _build_prompt(income, status, tax_year, cfg["description"])

        tasks.append(TaxTask(
            task_id      = task_id,
            difficulty   = diff,
            gross_income = income,
            filing_status = status,
            tax_year     = tax_year,
            description  = cfg["description"],
            prompt       = prompt,
            ground_truth = gt,
        ))

    return tasks


def generate_benchmark_suite(seed: int = 42, tax_year: int = 2024) -> dict:
    """
    Generate the standard benchmark suite used for published evaluations.
    Returns a dict with all four difficulty splits.

    Usage:
        suite = generate_benchmark_suite()
        # Evaluate your model on suite['easy'], suite['medium'], etc.
        # Report mean ± std per split. This is a proper benchmark.
    """
    return {
        "easy":        generate_tasks(100, seed=seed, difficulty="easy",        tax_year=tax_year),
        "medium":      generate_tasks(100, seed=seed, difficulty="medium",      tax_year=tax_year),
        "hard":        generate_tasks(100, seed=seed, difficulty="hard",        tax_year=tax_year),
        "adversarial": generate_tasks(50,  seed=seed, difficulty="adversarial", tax_year=tax_year),
    }


if __name__ == "__main__":
    # Quick sanity check
    suite = generate_benchmark_suite()
    for diff, tasks in suite.items():
        liabilities = [t.ground_truth.total_tax_liability for t in tasks]
        avg = sum(liabilities) / len(liabilities)
        print(f"{diff:>12}  n={len(tasks):3d}  "
              f"avg_liability=${avg:>10,.2f}  "
              f"income_range=${min(t.gross_income for t in tasks):>8,.0f}"
              f"–${max(t.gross_income for t in tasks):>8,.0f}")

    # Print one adversarial example to show what the agent sees
    print("\n── Sample Adversarial Task ──")
    t = suite["adversarial"][0]
    print(f"ID: {t.task_id}  Income: ${t.gross_income:,.2f}  "
          f"Status: {t.filing_status}")
    print(f"Ground truth: ${t.ground_truth.total_tax_liability:,.2f}")
    print(f"\nAgent prompt:\n{t.prompt}")
