---
title: Tax Agent OpenEnv
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
app_port: 7860
---

# Tax Agent Environment (OpenEnv Interactive Evaluation)

[![OpenEnv Compliant](https://img.shields.io/badge/OpenEnv-Compliant-brightgreen.svg)]()
[![Hugging Face Spaces - Gradio Demo](https://img.shields.io/badge/Hugging%20Face-Spaces%20Demo-blue.svg)]()

## Environment Overview and Motivation
The Tax Agent Environment simulates the complex, high-stakes task of financial document processing and regulatory compliance (tax filing). Unlike traditional toy problems, this environment tests an LLM's capability to act as an autonomous agent that must accurately read financial data, interpret strict guidelines (deductions and flat tax rules), execute precise arithmetic, and submit an irreversible final filing.

Motivation: AI agents are rapidly being integrated into fintech and accounting workflows. This environment provides a deterministic, OpenEnv-compliant sandbox to meticulously evaluate an agent's ability to navigate financial processes sequentially without hallucinating numerical values or making destructive, irreversible errors.

---

## Action and Observation Spaces

The environment strictly enforces structured communication using Typed Observations and Actions validated via Pydantic models.

### Action Space
The agent can perform one of three discrete actions per turn:
1. apply_deduction: Submits a deduction claim based on standard guidelines.
   * Required Parameter: amount (float > 0).
   * Constraint: The cumulative amount must not exceed the allowed standard deduction.
2. calculate_tax: Triggers the internal system to calculate preliminary liability based on a fixed 20% tax rate on taxable income.
   * Parameters: None.
3. submit_filing: The irreversible terminal action that concludes the episode and triggers the deterministic automated grader.
   * Required Parameter: liability (float).

### Observation Space
After every step, the environment returns a standardized OpenEnv tuple: (observation, reward, done, info).
* observation: A Pydantic object containing current_state status and descriptive natural language feedback.
* reward: A float value providing incremental positive reinforcement (+0.2 for valid steps) and penalties (-0.2 for hallucinated actions), plus a final grader score (0.0 to 1.0).
* done: A boolean flag indicating task completion or termination.
* info: A dictionary containing turn_count and the fully exposed internal state() of the environment.

---

## Task Descriptions

The environment evaluates the agent across three distinct tasks of increasing difficulty. All tasks are evaluated by a programmatic, deterministic grader that assigns a score between 0.0 and 1.0.

* Task 1: Easy
  * Profile: Standard W-2 employee with simple parameters.
  * Description: Income: $50,000 | Standard Deduction: $10,000.
  * Objective: Flawlessly apply the deduction and correctly calculate the $8,000 liability.
* Task 2: Medium
  * Profile: Mid-level earner.
  * Description: Income: $120,000 | Standard Deduction: $15,000.
  * Objective: Navigate larger numerical values, calculate higher liability of $21,000.
* Task 3: Hard
  * Profile: High-net-worth individual with maximum complexity.
  * Description: Income: $350,000 | Standard Deduction: $25,000.
  * Objective: Flawlessly execute maximum deduction usage, calculate significant liability of $65,000.

---

## Baseline Performance Scores

The standalone baseline evaluation was performed using meta-llama/Meta-Llama-3-8B-Instruct via Hugging Face Serverless Inference (Temperature: 0.1).

| Task Difficulty | Target Liability | Grader Score / Total Reward | Turns | Final State Status |
| :--- | :--- | :--- | :--- | :--- |
| Easy | $8,000 | [POPULATE FROM LOGS] / [TOTAL REWARD] | [TURNS] | [FINAL STATE] |
| Medium | $21,000 | [POPULATE FROM LOGS] / [TOTAL REWARD] | [TURNS] | [FINAL STATE] |
| Hard | $65,000 | [POPULATE FROM LOGS] / [TOTAL REWARD] | [TURNS] | [FINAL STATE] |
| Total Baseline | - | [TOTAL BASELINE REWARD] | - | - |

---
## System Architecture

The TaxAgent environment is built on the **OpenEnv Finite State Machine (FSM)** architecture. 

1. **State Management**: Unlike a simple chatbot, this environment maintains a persistent JSON state (`TaxState`). Every action from the agent transitions the state, which is then reflected in the Observation Space.
2. **Reward Function**: The environment utilizes a shaped reward function.
   - **Step Reward ($r_t$):** Provided for valid transitions (e.g., successful income classification).
   - **Termination Reward ($R$):** The final Grader Score (0.0–1.0) based on the accuracy of the final tax liability calculation.
3. **Deterministic Grader**: The final submission is evaluated by a programmatic grader that compares the agent's output against a target value calculated using a 20% flat tax rate after statutory deductions.

## Setup and Usage Instructions

This project is pre-configured for Hugging Face Spaces deployment using the OpenEnv specifications.

### Prerequisites
1. A free Hugging Face account and an Access Token (HF_TOKEN).
2. Python 3.10+ (for local CLI evaluation only).

### Hugging Face Spaces Deployment (Interactive UI)
This repository is fully compatible with Hugging Face Spaces Docker SDK.
1. Deploy this repository to a Hugging Face Space (via direct clone or GitHub Actions).
2. Navigate to the Space Settings -> Variables and Secrets.
3. Add a new Secret named HF_TOKEN and paste your access token.
4. The Space will automatically build the Docker container and serve the interactive UI on port 7860.

### Local CLI Baseline Evaluation
To run the reproducible baseline evaluation script without the UI:
```bash
git clone [https://github.com/nankalon-code/TaxAgent-OpenEnv.git](https://github.com/nankalon-code/TaxAgent-OpenEnv.git)
cd TaxAgent-OpenEnv
pip install -r requirements.txt
export HF_TOKEN="your_hf_token_here"
python src/inference.py




