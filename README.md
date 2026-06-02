# TaxAgent-OpenEnv: eBPF-Hardened Autonomous LLM Financial Evaluation Suite

[![Kernel Security](https://img.shields.io/badge/Security-eBPF%20Enforcement-blueviolet.svg)](#)
[![Tax Engines](https://img.shields.io/badge/Math-10%20Countries%20%2B%20US%20Federal-brightgreen.svg)](#)
[![Evaluations](https://img.shields.io/badge/LLM-Benchmark%20Suite-blue.svg)](#)
[![License](https://img.shields.io/badge/License-MIT-orange.svg)](#)

A production-grade, kernel-hardened evaluation environment for financial and tax-calculating LLM agents. 

Traditional sandbox models (like Docker containers) are resource-heavy and slow for high-throughput evaluation, and userspace interceptors (like Python import mocks) are easily bypassed by agents writing raw `ctypes` or executing shell code. **TaxAgent-OpenEnv** solves this by enforcing security policies directly inside the Linux kernel using **eBPF (Extended Berkeley Packet Filter)**, running alongside a deterministic mathematical framework to score financial precision without hallucinations.

---

## 🏗️ System Architecture

```
                                ┌────────────────────────────────────────┐
                                │          Userspace Evaluator           │
                                │        (run_evaluation.py)             │
                                └────────────┬──────────────┬────────────┘
                                             │              │
                     Injects scenario data   │              │ Invokes Python execution
                                             ▼              ▼
                ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
                │      International Engine       │   │        Execution Sandbox        │
                │     (international_taxes.py)    │   │          (harness.py)           │
                │  Multi-country tax rule database│   │  Loads & monitors agent script  │
                └─────────────────────────────────┘   └────────────────┬────────────────┘
                                                                       │
                                                  Kernel-level hooks   │ registers monitored TGIDs
                                                  & syscall blocks     ▼
                                                      ┌─────────────────────────────────┐
                                                      │          eBPF Shield            │
                                                      │        (ebpf_shield.c)          │
                                                      │  Active blocking via kprobes    │
                                                      └─────────────────────────────────┘
```

---

## 🌟 Resume Highlights (Why This Project Stands Out)

If you are presenting this project to FAANG, top quantitative trading firms, or cutting-edge AI startups, highlight these points:
* **Kernel-Space Security Systems:** Demonstrates deep experience writing safe, verifier-compliant C code inside the Linux Kernel using kprobes, tracepoints, and `bpf_override_return`.
* **Sub-Microsecond Latency Enforcement:** Shows understanding of system performance metrics by choosing eBPF over containerization, achieving containment with negligible syscall overhead.
* **Deterministic Financial Verification:** Solves LLM arithmetic limitations by enforcing a strict FSM and grading agents against progressive marginal tax codes (such as FICA caps and multi-country tax regulations).
* **Defensive Threat Modeling:** Includes a formal, production-grade security analysis (`THREAT_MODEL.md`) showing knowledge of modern escape vectors, including PID recycling, symlink TOCTOU, and DNS exfiltration.

---

## 📂 Repository Structure

* [harness.py](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/harness.py) — Userspace sandbox orchestrator. Manages child process lifecycle, handles dynamic capability validation, and feeds monitored PIDs to the kernel.
* [ebpf_shield.c](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/ebpf_shield.c) — The C kernel module. Hooks `openat()` and `connect()` syscalls to block illegal file access and outbound network connections.
* [run_evaluation.py](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/run_evaluation.py) — High-throughput LLM benchmark runner. Supports HuggingFace Serverless API, local Ollama endpoints, and simulation backends.
* [tax_engine.py](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/tax_engine.py) — Deterministic US federal progressive tax calculator (FICA, Social Security ceilings, Medicare surtaxes).
* [state_taxes.py](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/state_taxes.py) — CA, NY (including NYC local resident surtax), and TX income calculators.
* [international_taxes.py](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/international_taxes.py) — Native tax calculations for 10 major global economies (US, India, UK, Canada, Germany, Australia, Japan, Singapore, France, UAE, Brazil).
* [THREAT_MODEL.md](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/THREAT_MODEL.md) — Comprehensive security audit detailing sandbox boundaries, mitigation paths, and architectural trade-offs.
* [dashboard/](file:///c:/Users/APOORVA%20JHA/OneDrive/Desktop/OPEN%20TAX%20AGENT/dashboard/) — A sleek, glassmorphic monitoring dashboard with real-time risk scores and live event timelines.

---

## 🚀 Getting Started

### 1. Prerequisites (Linux / WSL2)
Install the BPF compiler collection (BCC) and kernel headers:
```bash
sudo apt-get update
sudo apt-get install -y python3-bpfcc bpfcc-tools linux-headers-$(uname -r)
```

### 2. Run Sandbox Validation
Run the eBPF harness. It will automatically check your kernel configurations and capabilities:
```bash
sudo python3 harness.py
```
*If you wish to run without full root privileges in production, configure capabilities:*
```bash
sudo setcap cap_bpf,cap_perfmon+eip $(which python3)
python3 harness.py
```

### 3. Run LLM Evaluation
Run the model evaluation suite using the mock pipeline to verify task scoring:
```bash
python run_evaluation.py --backend mock --n 20
```
Evaluate an open-source model using the HuggingFace API:
```bash
export HF_TOKEN="your_hf_token_here"
python run_evaluation.py --backend hf --model meta-llama/Meta-Llama-3-8B-Instruct --n 50
```

### 4. Run Mathematical Engine Tests
Ensure the progressive bracket math aligns with actual IRS regulations:
```bash
pip install pytest
pytest test_tax_engine.py -v
```
