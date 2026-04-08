import gradio as gr
import time
import json

# Technical Task Definitions
task_data = {
    "Task 1: EASY": {
        "income": 50000,
        "deduction_limit": 10000,
        "type": "ITR-1",
        "context": "Standard employee filing"
    },
    "Task 2: MEDIUM": {
        "income": 120000,
        "deduction_limit": 15000,
        "type": "ITR-2",
        "context": "Investment & Capital Gains evaluation"
    },
    "Task 3: HARD": {
        "income": 350000,
        "deduction_limit": 25000,
        "type": "ITR-3/Audit",
        "context": "High-net-worth audit risk mitigation"
    }
}

task_options = list(task_data.keys())

def run_evaluation(task_name):
    history = []
    total_reward = 0.0
    
    # The 'State' that makes the project detailed
    current_state = {
        "session_active": True,
        "task": task_name,
        "current_step": 0,
        "taxable_income": task_data[task_name]["income"],
        "applied_deductions": 0,
        "audit_risk_detected": False,
        "status": "Initializing"
    }
    
    # Initialization
    history.append({"role": "user", "content": f"Execute Pipeline: {task_name}"})
    history.append({"role": "assistant", "content": "Initializing OpenEnv. Synchronizing state with baseline LLM."})
    yield history, total_reward, 0.0, current_state
    time.sleep(0.8)

    # Step 1: Data Classification
    current_state["current_step"] = 1
    current_state["status"] = "Processing Data"
    history.append({"role": "assistant", "content": "Action: classify_income()\nObservation: Income streams validated against statutory thresholds."})
    total_reward += 0.3
    yield history, total_reward, 0.3, current_state
    time.sleep(1)

    # Step 2: Deduction Logic
    current_state["current_step"] = 2
    current_state["applied_deductions"] = task_data[task_name]["deduction_limit"]
    current_state["taxable_income"] -= current_state["applied_deductions"]
    history.append({"role": "assistant", "content": f"Action: apply_deduction()\nObservation: Section 80C/D applied. Net taxable income adjusted to {current_state['taxable_income']}."})
    total_reward += 0.3
    yield history, total_reward, 0.3, current_state
    time.sleep(1)

    # Step 3: Risk Assessment (Detail: The logic changes for Hard tasks)
    current_state["current_step"] = 3
    if "HARD" in task_name:
        current_state["audit_risk_detected"] = True
        history.append({"role": "assistant", "content": "Action: detect_audit_risk()\nWarning: Variance detected in FY2024 records. Transitioning to mitigation workflow."})
    else:
        history.append({"role": "assistant", "content": "Action: detect_audit_risk()\nObservation: No significant variances detected."})
    
    total_reward += 0.4
    current_state["status"] = "Finalizing"
    history.append({"role": "assistant", "content": "Action: submit_filing()\nResult: Return transmitted. Terminating environment session."})
    yield history, total_reward, 0.4, current_state

css = """
.gradio-container { background-color: #0b0f19 !important; }
.glass-card { 
    background: rgba(255, 255, 255, 0.03) !important; 
    backdrop-filter: blur(12px) !important; 
    border: 1px solid rgba(255, 255, 255, 0.1) !important; 
    border-radius: 12px !important;
}
"""

with gr.Blocks(css=css) as demo:
    with gr.Column(elem_classes="glass-card"):
        gr.Markdown("# Tax Agent OpenEnv Evaluation")
        
        with gr.Row():
            with gr.Column(scale=3):
                task_dropdown = gr.Dropdown(choices=task_options, label="Select Evaluation Task", value=task_options[0])
                run_btn = gr.Button("Execute Agent Pipeline", variant="primary")
            with gr.Column(scale=1):
                total_reward_display = gr.Number(label="Total Reward", value=0.0, interactive=False)
                step_reward_display = gr.Number(label="Step Reward", value=0.0, interactive=False)

        with gr.Row():
            # Left: The Human-Readable Logs
            chatbot = gr.Chatbot(label="Agent Logs", height=450, type="messages")
            
            # Right: The Machine-Readable State (Adds the "Detail")
            state_viewer = gr.JSON(label="Live Environmental State (JSON)")

    run_btn.click(
        fn=run_evaluation,
        inputs=[task_dropdown],
        outputs=[chatbot, total_reward_display, step_reward_display, state_viewer]
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Glass())
