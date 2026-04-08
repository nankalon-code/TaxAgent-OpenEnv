import gradio as gr
import time

task_options = [
    "Task 1: EASY - $50k Income / $10k Standard Deduction",
    "Task 2: MEDIUM - $120k Income / $15k Standard Deduction",
    "Task 3: HARD - $350k Income / $25k Standard Deduction"
]

def run_evaluation(task_name):
    """
    Main evaluation loop. 
    CRITICAL FIX: Every message added to 'history' MUST be a dictionary.
    """
    history = []
    total_reward = 0.0

    # 1. Start message
    history.append({"role": "user", "content": f"Please execute {task_name}"})
    history.append({"role": "assistant", "content": "Initializing OpenEnv and connecting to autonomous agent..."})
    yield history, total_reward, 0.0
    time.sleep(1) # Brief pause for visual UI effect

    # ---------------------------------------------------------
    # AGENT EXECUTION SIMULATION
    # (Replace this block with your actual inference loop if needed, 
    # just ensure you append dictionaries to 'history' like below!)
    # ---------------------------------------------------------

    # Step 1
    history.append({"role": "assistant", "content": "**Action:** `apply_deduction()`\nObservation: Standard deduction applied based on profile constraints."})
    total_reward += 0.2
    yield history, total_reward, 0.2
    time.sleep(1)

    # Step 2
    history.append({"role": "assistant", "content": "**Action:** `calculate_tax()`\nObservation: Preliminary liability calculated at 20% flat rate."})
    total_reward += 0.2
    yield history, total_reward, 0.2
    time.sleep(1)

    # Step 3
    history.append({"role": "assistant", "content": "**Action:** `submit_filing()`\nObservation: Final tax liability submitted. Terminating episode."})
    total_reward += 0.6 
    history.append({"role": "assistant", "content": "✅ **Task Complete.** \n\n**Final Grader Score:** 1.0 (Flawless Execution)"})
    yield history, total_reward, 0.6

# Apply a clean, modern theme
with gr.Blocks(theme=gr.themes.Soft(primary_hue="indigo")) as demo:

    # Structured Markdown header
    gr.Markdown("""
    # 📊 Tax Agent OpenEnv Interactive Evaluation

    Select a tax task below to observe the autonomous agent interact with the environment.

    ### How it works:
    * **The Agent** analyzes the financial profile and executes discrete actions.
    * **The Environment** tracks state changes and dispenses incremental rewards.
    * **The Logs** display the real-time thought process and state updates.

    > **Note:** Ensure your `HF_TOKEN` is set securely in your Space settings.
    """)

    with gr.Row():
        task_dropdown = gr.Dropdown(choices=task_options, label="Select Tax Task", value=task_options[0])
        total_reward_display = gr.Number(label="Total Cumulative Reward", value=0.0, interactive=False)
        step_reward_display = gr.Number(label="Incremental Step Reward", value=0.0, interactive=False)

    # The missing run button!
    run_btn = gr.Button("🚀 Run Agent on Selected Task", variant="primary")

    # The Chatbot with the STRICT 'messages' type constraint
    chatbot = gr.Chatbot(
        label="Agent Conversation & Environment Logs",
        type="messages",
        height=450
    )

    # Wire the button to the function
    run_btn.click(
        fn=run_evaluation,
        inputs=[task_dropdown],
        outputs=[chatbot, total_reward_display, step_reward_display]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
