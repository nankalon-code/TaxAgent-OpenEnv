"""
run_evaluation.py — TaxAgent-OpenEnv | LLM Evaluation Runner
=============================================================

This is what was missing. An actual model, on actual tasks, producing
actual numbers you can put in a table.

Supports:
  - HuggingFace Serverless Inference API (free, no GPU needed)
  - OpenAI-compatible endpoints (local Ollama, Together AI, etc.)
  - Mock runner for testing the evaluation pipeline without an API key

Results are saved to results/<model_name>_<timestamp>.json
Load the JSON and call print_report() to get a formatted results table.

Usage:
    # With HuggingFace (requires HF_TOKEN env var):
    python run_evaluation.py --model meta-llama/Meta-Llama-3-8B-Instruct --n 50

    # With local Ollama:
    python run_evaluation.py --backend ollama --model llama3 --n 20

    # Dry run (mock LLM) to test the pipeline:
    python run_evaluation.py --backend mock --n 10
"""

from __future__ import annotations
import os
import sys
import json
import time
import argparse
import datetime
import traceback
from dataclasses import dataclass, asdict
from typing import Protocol

from tax_engine import compute_liability, score_agent_answer
from task_generator import generate_benchmark_suite, TaxTask
from environment import TaxEnvironment

# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class TaskResult:
    task_id:           str
    difficulty:        str
    gross_income:      float
    filing_status:     str
    model_raw_output:  str        # raw LLM response
    parsed_liability:  float | None
    ground_truth:      float
    score:             float
    steps:             int
    error:             str | None
    latency_ms:        float


@dataclass
class EvaluationReport:
    model_name:   str
    backend:      str
    timestamp:    str
    n_tasks:      int
    results:      list[TaskResult]

    def mean_score(self, difficulty: str | None = None) -> float:
        subset = [r for r in self.results
                  if difficulty is None or r.difficulty == difficulty]
        if not subset:
            return 0.0
        return sum(r.score for r in subset) / len(subset)

    def print_report(self):
        sep = "─" * 68
        print(f"\n{sep}")
        print(f"  Model   : {self.model_name}")
        print(f"  Backend : {self.backend}")
        print(f"  Time    : {self.timestamp}")
        print(f"  N tasks : {self.n_tasks}")
        print(sep)
        print(f"  {'Difficulty':<14}  {'N':>4}  {'Mean Score':>10}  {'Exact (1.0)':>11}  {'Failed':>6}")
        print(sep)
        for diff in ["easy", "medium", "hard", "adversarial"]:
            subset  = [r for r in self.results if r.difficulty == diff]
            if not subset:
                continue
            mean    = sum(r.score for r in subset) / len(subset)
            exact   = sum(1 for r in subset if r.score == 1.0)
            failed  = sum(1 for r in subset if r.error is not None)
            print(f"  {diff:<14}  {len(subset):>4}  {mean:>10.4f}  "
                  f"{exact:>11}  {failed:>6}")
        print(sep)
        overall = self.mean_score()
        print(f"  {'OVERALL':<14}  {self.n_tasks:>4}  {overall:>10.4f}")
        print(sep)

        # Print failure analysis
        failures = [r for r in self.results if r.score < 0.5]
        if failures:
            print(f"\n  Failure Analysis (score < 0.5) — top 5:")
            for r in sorted(failures, key=lambda x: x.score)[:5]:
                print(f"    [{r.difficulty}] ${r.gross_income:>10,.0f} | "
                      f"truth=${r.ground_truth:>10,.2f} | "
                      f"parsed=${r.parsed_liability:>10,.2f if r.parsed_liability else 'PARSE_FAIL':>12} | "
                      f"score={r.score:.2f}")
        print()


# ── LLM Backend protocols ─────────────────────────────────────────────────────

class LLMBackend(Protocol):
    def complete(self, prompt: str) -> str: ...


class HuggingFaceBackend:
    """
    HuggingFace Serverless Inference API.
    Free tier: rate-limited but sufficient for 50-task evaluation.
    """

    def __init__(self, model: str):
        try:
            from huggingface_hub import InferenceClient
        except ImportError:
            sys.exit(
                "[FATAL] huggingface_hub not installed.\n"
                "  pip install huggingface-hub"
            )
        token = os.environ.get("HF_TOKEN")
        if not token:
            sys.exit(
                "[FATAL] HF_TOKEN environment variable not set.\n"
                "  export HF_TOKEN=hf_..."
            )
        self.client = InferenceClient(model=model, token=token)
        self.model  = model

    def complete(self, prompt: str) -> str:
        response = self.client.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a tax calculation agent. You have access to "
                        "the tax_engine Python library. For every task, you "
                        "MUST call tax_engine.compute_liability() to compute "
                        "the answer. Never compute tax manually. "
                        "At the end of your response, output your final answer "
                        "on a line that starts with 'FINAL_LIABILITY: ' "
                        "followed by the numeric value (no $ sign, no commas)."
                    )
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=512,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""


class OllamaBackend:
    """
    Local Ollama instance. Install from https://ollama.ai.
    No API key needed. Run: ollama pull llama3
    """

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        try:
            import requests
        except ImportError:
            sys.exit("[FATAL] requests not installed. pip install requests")
        self.requests  = requests
        self.model     = model
        self.base_url  = base_url

    def complete(self, prompt: str) -> str:
        system = (
            "You are a tax calculation agent. You have access to the "
            "tax_engine Python library. For every task, call "
            "tax_engine.compute_liability() to compute the answer. "
            "At the end, output 'FINAL_LIABILITY: <number>'."
        )
        response = self.requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


class MockBackend:
    """
    Deterministic mock LLM for testing the evaluation pipeline.
    Simulates three failure modes seen in real models:
      - Always correct (oracle)
      - Forgets FICA (common)
      - Uses flat 20% rate (the old wrong behavior)
      - Random wrong answers
    """

    def __init__(self, mode: str = "mixed"):
        """mode: 'oracle', 'no_fica', 'flat_rate', 'mixed'"""
        self.mode = mode
        self._counter = 0

    def complete(self, prompt: str) -> str:
        import re
        # Extract income from prompt
        match = re.search(r"Gross W-2 Income: \$([\d,]+\.\d+)", prompt)
        income = float(match.group(1).replace(",", "")) if match else 50_000.0

        match2 = re.search(r"Filing Status: (\w[\w ]+)", prompt)
        status_raw = match2.group(1).strip().lower().replace(" ", "_") if match2 else "single"
        if "jointly" in status_raw:
            status = "married_filing_jointly"
        elif "household" in status_raw:
            status = "head_of_household"
        else:
            status = "single"

        from tax_engine import compute_liability

        self._counter += 1
        mode = self.mode

        if mode == "mixed":
            # Cycle through failure modes
            modes = ["oracle", "no_fica", "flat_rate", "oracle", "oracle"]
            mode  = modes[self._counter % len(modes)]

        if mode == "oracle":
            result   = compute_liability(income, 2024, status, fica=True)
            liability = result.total_tax_liability
        elif mode == "no_fica":
            # Model computes only federal income tax, forgets FICA
            result   = compute_liability(income, 2024, status, fica=False)
            liability = result.federal_income_tax
        elif mode == "flat_rate":
            # Model uses flat 20% on taxable income (old wrong behavior)
            from tax_engine import _STANDARD_DEDUCTIONS_2024
            taxable  = max(0.0, income - _STANDARD_DEDUCTIONS_2024.get(status, 14_600))
            liability = taxable * 0.20
        else:
            liability = income * 0.15   # completely wrong

        return (
            f"I have computed the tax liability for this taxpayer.\n"
            f"Using tax_engine.compute_liability(), the result is ${liability:,.2f}.\n"
            f"FINAL_LIABILITY: {liability:.2f}"
        )


# ── Response parser ───────────────────────────────────────────────────────────

def parse_liability(response: str) -> float | None:
    """
    Extract the numeric liability from an LLM response.
    Strategy: look for 'FINAL_LIABILITY: <number>' first,
    then fall back to scanning for any plausible dollar amount.
    """
    import re

    # Primary: structured marker
    m = re.search(r"FINAL_LIABILITY:\s*([\d,]+\.?\d*)", response)
    if m:
        return float(m.group(1).replace(",", ""))

    # Fallback: last dollar amount in the response
    amounts = re.findall(r"\$?([\d,]{3,}\.?\d*)", response)
    if amounts:
        try:
            return float(amounts[-1].replace(",", ""))
        except ValueError:
            pass

    return None


# ── Main evaluation loop ──────────────────────────────────────────────────────

def run_evaluation(
    backend: LLMBackend,
    model_name: str,
    backend_name: str,
    tasks: list[TaxTask],
    delay_between_requests: float = 1.0,
) -> EvaluationReport:
    """
    Run the model through all tasks and collect results.

    Parameters
    ----------
    backend
        LLM backend instance.
    model_name
        Human-readable model name for the report.
    backend_name
        'hf', 'ollama', or 'mock'.
    tasks
        List of TaxTask objects from task_generator.
    delay_between_requests
        Seconds to wait between API calls (rate limiting).
    """
    results: list[TaskResult] = []
    total = len(tasks)

    for i, task in enumerate(tasks, 1):
        print(f"  [{i:>3}/{total}] {task.task_id} {task.difficulty:<12} "
              f"${task.gross_income:>10,.0f} {task.filing_status} ...", end=" ", flush=True)

        error   = None
        parsed  = None
        score   = 0.0
        raw_out = ""
        start   = time.monotonic()

        try:
            raw_out = backend.complete(task.prompt)
            parsed  = parse_liability(raw_out)

            if parsed is None:
                error = "PARSE_FAIL: no numeric liability found in response"
                score = 0.0
            else:
                score = score_agent_answer(parsed, task.ground_truth)

        except KeyboardInterrupt:
            print("\n  Interrupted by user.")
            break
        except Exception as e:
            error   = f"{type(e).__name__}: {e}"
            raw_out = traceback.format_exc()

        latency = (time.monotonic() - start) * 1000

        result = TaskResult(
            task_id          = task.task_id,
            difficulty       = task.difficulty,
            gross_income     = task.gross_income,
            filing_status    = task.filing_status,
            model_raw_output = raw_out[:500],   # truncate for storage
            parsed_liability = parsed,
            ground_truth     = task.ground_truth.total_tax_liability,
            score            = score,
            steps            = 3,   # in tool-call mode, always 3 steps
            error            = error,
            latency_ms       = round(latency, 1),
        )
        results.append(result)

        status_str = f"score={score:.2f}" if not error else f"ERR"
        print(status_str)

        if delay_between_requests > 0 and i < total:
            time.sleep(delay_between_requests)

    return EvaluationReport(
        model_name  = model_name,
        backend     = backend_name,
        timestamp   = datetime.datetime.now().isoformat(),
        n_tasks     = len(results),
        results     = results,
    )


def save_report(report: EvaluationReport, output_dir: str = "results") -> str:
    os.makedirs(output_dir, exist_ok=True)
    safe_model = report.model_name.replace("/", "_").replace(":", "_")
    ts          = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path        = os.path.join(output_dir, f"{safe_model}_{ts}.json")

    data = {
        "model_name":  report.model_name,
        "backend":     report.backend,
        "timestamp":   report.timestamp,
        "n_tasks":     report.n_tasks,
        "mean_score":  report.mean_score(),
        "by_difficulty": {
            d: report.mean_score(d)
            for d in ["easy", "medium", "hard", "adversarial"]
        },
        "results": [asdict(r) for r in report.results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  Results saved to {path}")
    return path


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run LLM evaluation on TaxAgent-OpenEnv benchmark.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--backend", choices=["hf", "ollama", "mock"],
                        default="mock", help="LLM backend (default: mock)")
    parser.add_argument("--model",   default="meta-llama/Meta-Llama-3-8B-Instruct",
                        help="Model name/ID")
    parser.add_argument("--n",       type=int, default=20,
                        help="Number of tasks per difficulty (default: 20)")
    parser.add_argument("--seed",    type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--difficulty", default=None,
                        choices=["easy", "medium", "hard", "adversarial"],
                        help="Run only one difficulty tier")
    parser.add_argument("--delay",   type=float, default=1.0,
                        help="Seconds between API calls (default: 1.0)")
    parser.add_argument("--output",  default="results",
                        help="Output directory for results JSON")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama server URL")
    args = parser.parse_args()

    # Build backend
    if args.backend == "hf":
        backend      = HuggingFaceBackend(args.model)
        backend_name = "hf"
    elif args.backend == "ollama":
        backend      = OllamaBackend(args.model, args.ollama_url)
        backend_name = "ollama"
    else:
        backend      = MockBackend(mode="mixed")
        backend_name = "mock"
        args.model   = f"mock/{args.model}"

    # Generate tasks
    print(f"\n  Generating benchmark tasks (seed={args.seed}, n={args.n} per tier)...")
    if args.difficulty:
        from task_generator import generate_tasks
        tasks = generate_tasks(args.n, seed=args.seed, difficulty=args.difficulty)
    else:
        suite = generate_benchmark_suite(seed=args.seed)
        tasks = []
        for diff_tasks in suite.values():
            tasks.extend(diff_tasks[:args.n])

    print(f"  Total tasks: {len(tasks)}")
    print(f"  Model      : {args.model}")
    print(f"  Backend    : {backend_name}")
    print(f"  Delay      : {args.delay}s\n")

    # Run evaluation
    report = run_evaluation(
        backend      = backend,
        model_name   = args.model,
        backend_name = backend_name,
        tasks        = tasks,
        delay_between_requests = args.delay,
    )

    # Print report
    report.print_report()

    # Save
    save_report(report, args.output)


if __name__ == "__main__":
    main()
