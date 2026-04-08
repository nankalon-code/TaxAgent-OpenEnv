import gradio as gr
import time

# Professional Task Metadata
task_data = {
    "Task 1: EASY": {
        "description": "Income classification and ITR form selection.",
        "details": "Salary and house property evaluation.",
        "requirements": "Target: ITR-1 (Sahaj)"
    },
    "Task 2: MEDIUM": {
        "description": "Identify valid deductions from messy expenses.",
        "details": "Section 80C and 80D compliance check.",
        "requirements": "Target: Maximize statutory limits"
    },
    "Task 3: HARD": {
        "description": "Multi-year audit risk and amended returns.",
        "details": "Flagging reporting errors and liability minimization.",
        "requirements": "Target: Risk mitigation / FY2024 Correction"
    }
}

task_options = list(task_data.keys())

def update_task_info(task_name):
    """Updates the UI display boxes when a new task is selected."""
    info = task_data.get(task_name, {})
    return info.get("description", ""), info.get("details", ""), info.get("requirements", "")

def run_evaluation(task_name):
    """
    Main evaluation logic simulating professional ITR workflows.
    Uses strict dictionary format for Gradio 5 compatibility.
    """
    history = []
    total_reward = 0.0
    
    # Step 1: System Initialization
    history.append({"role": "user", "content": f"Begin evaluation for {task_name}"})
    history.append({"role": "assistant", "content": "Environment initialized. Loading local tax statutes and ITR workflow standards."})
    yield history, total_reward, 0.0
    time.sleep(0.8)

    if "EASY" in task_name:
        history.append({"role": "assistant", "content": "Action: classify_income_sources()\nResult: Primary income identified as Salary. Criteria validated for ITR-1 selection."})
        total_reward += 0.5
        yield history, total_reward, 0.5
        time.sleep(1)
        history.append({"role": "assistant", "content": "Action: validate_form_selection()\nResult: Form ITR-1 (Sahaj) confirmed as optimal filing path.\n\nStatus: Task Complete. Grader Score: 1.0"})
        total_reward += 0.5
        yield history, total_reward, 0.5

    elif "MEDIUM" in task_name:
        history.append({"role": "assistant", "content": "Action: parse_expense_stream()\nResult: Processing unstructured financial data. 14 potential deductions identified."})
        yield history, total_reward, 0.0
        time.sleep(1)
        history.append({"role": "assistant", "content": "Action: apply_statutory_filters(Sec_80C, Sec_80D)\nResult: Validated LIC and Medical Insurance premiums. Applied Section 80C cap of 150,000 INR."})
        total_reward += 1.0
        yield history, total_reward, 1.
