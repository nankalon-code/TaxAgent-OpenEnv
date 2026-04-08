import gradio as gr
import time
import json
import os
from openai import OpenAI
from dotenv import load_dotenv

from src.environment import TaxEnvironment
from src.tasks import get_eval_tasks
from src.models import TaxAction

# Load environment variables
load_dotenv()

# Initialize tasks and drop-down choices
tasks = get_eval_tasks()
# Create detailed task descriptions for the dropdown, strictly text-only
task_options = []
for i, task in enumerate(tasks):
    label = f"Task {i+1}: {task.difficulty_level.upper()} - {task.description[:60]}..."
    task_options.append(label)

def create_client():
    """Initializes the OpenAI client using HF_TOKEN for Serverless Inference."""
    # For security, read HF_TOKEN from environment. Explain this process in the README.
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        # For local testing, prompt user to set, but deployment requires Space secret
        raise ValueError("HF_TOKEN environment variable is missing. It must be set as a Secret in Hugging Face Spaces deployment.")
    return OpenAI(base_url="https://api-inference.huggingface.co/v1/", api_key=hf_token)

def reset_ui():
    """Returns initial empty/default values for all UI components."""
    # Outputs must match outputs in reset_btn.click (Chatbot, Number, Number, Textbox, Textbox, Textbox)
    # Using None for Chatbot history to clear it, 0.0 for rewards, empty strings for feedback/status, and default action prompt.
    return None, 0.0, 0.0, "", "Not Started", "Choose a task and click 'Run Agent on Selected Task'."

def run_agent_generator(task_index_str,chatbot_history, total_reward):
    """
    Generator that executes the agent-environment loop step-by-step, 
    yielding incremental updates to all UI components after each logical phase.
    """
    # Initialize client (will raise error if token is missing, handled by Gradio)
    client = create_client()
    
    # Determine which task was selected from the dropdown string
    task_index = task_options.index(task_index_str)
    task = tasks[task_index]
    
    # Detailed system prompt for model, strictly enforcing JSON outputs
    system_prompt = (
        "You are an autonomous tax agent navigating a simulated environment. "
        "Your goal is to complete the tax processing task efficiently and accurately. "
        "All communications must be valid JSON ONLY, strictly adhering to this schema:\n"
        "{\"action_type\": \"<action>\", \"parameters\": {\"<key>\": <value>}}\n\n"
        "Do not include markdown, explanations, or any extra text.\n"
        "Valid actions and their requirements:\n"
        "1. 'apply_deduction' (float param 'amount' > 0 and <= standard deduction)\n"
        "2. 'calculate_tax' (no parameters)\n"
        "3. 'submit_filing' (float param 'liability')"
    )
    
    # Initialize state
    env = TaxEnvironment(task=task)
    obs = env.reset()
    done = False
    messages = [{"role": "system", "content": system_prompt}]
    chatbot_history = []
    total_reward = 0.0

    # Yield initial task-started update: clear history, initial rewards, basic info
    # (Chatbot, Inc. Reward, Total Reward, Feedback, Status, Agent Action/Thinking)
    init_msg = f"Task {task_index+1} initialized. Objective: {task.description}\nInitial environment observation: {obs.feedback}"
    yield chatbot_history, 0.0, total_reward, "", "started", init_msg

    while not done:
        # A: Show Environment updates (feedback and internal state status/json) within chatbot for transparency
        status_text = f"Turn {env.turn_count} State Status: {env.state()['status']}"
        chatbot_history.append((None, f"{status_text}\nEnvironment Feedback: {obs.feedback}\nCurrent environment internal state (JSON): {json.dumps(env.state())}"))
        
        # Yield update before calling agent, including visual delay
        yield chatbot_history, 0.0, total_reward, obs.feedback, env.state()["status"], "Waiting for agent reasoning and output..."
        time.sleep(1) # Visual delay for demonstration

        # B: LLM Interaction
        # User message: JSON object containing environmental feedback and state details for context
        user_msg = json.dumps({"feedback": obs.feedback, "environment_state": env.state()})
        messages.append({"role": "user", "content": user_msg})
        agent_action_text = ""
        agent_reply_display = ""
        
        try:
            # Call Serverless Inference using OpenAI client and Meta-Llama-3-8B model
            response = client.chat.completions.create(
                model="meta-llama/Meta-Llama-3-8B-Instruct",
                messages=messages,
                temperature=0.1, # Keep low for deterministic tool use
                response_format={"type": "json_object"}
            )
            
            agent_reply_display = response.choices[0].message.content
            # Log the full response within the visual chatbot history
            messages.append({"role": "assistant", "content": agent_reply_display})
            
            # Parse response into strict Pydantic model
            action_data = json.loads(agent_reply_display)
            action = TaxAction(**action_data)
            agent_action_text = f"Parsed Agent Action: {action.action_type} with parameters {action.parameters}"
            
            # Append interaction to chatbot history (left: user context json, right: agent reply json)
            chatbot_history.append((user_msg, agent_reply_display)) 
            
        except Exception as e:
             # Handle errors or JSON parsing failures seamlessly using fallback action
             action = TaxAction(action_type="error", parameters={})
             chatbot_history.append((user_msg, f"Error/Hallucination encountered: {e}"))
             agent_action_text = f"Error during reasoning or parsing: {e}"

        # Yield update with agent response and parsed action
        yield chatbot_history, 0.0, total_reward, obs.feedback, env.state()["status"], agent_action_text
        time.sleep(1) # Visual delay

        # C: Environment Step
        # Execute agent's action and receive standard OpenEnv tuple return
        obs, reward, done, info = env.step(action)
        total_reward += reward
        
        # Log reward and incremental state change details in chatbot and dedicated boxes
        chatbot_history.append((None, f"Environment processed agent action.\nIncremental reward: {reward:.2f}\nUpdated Feedback: {obs.feedback}"))
        
        if done:
            # Handle episode completion within chatbot visual logs
            final_score_msg = f"Task {task_index+1} complete.\nFinal Grader Feedback: {obs.feedback}\nTotal Cumulative Reward: {total_reward:.2f}"
            chatbot_history.append((None, final_score_msg))
            
        # Yield comprehensive update with rewards, state, feedback, etc.
        yield chatbot_history, reward, total_reward, obs.feedback, env.state()["status"], "Environment updated. Processing next turn or task completion..."
        time.sleep(1) # Visual delay for demonstration

    # D: Final Terminal State Update (loop exited)
    final_agent_status_msg = f"Completed evaluation with Total Cumulative Reward: {total_reward:.2f}\nDetailed interaction history above."
    yield chatbot_history, reward, total_reward, obs.feedback, env.state()["status"], final_agent_status_msg

# --- Gradio Blocks Structure (Detailed UI Definition, Strictly Text-Only and Interactive) ---
# Using queue and launch with share=False for generator updates within one process.
with gr.Blocks(title="Tax Agent OpenEnv Interactive Evaluation") as demo:
    gr.Markdown("# Tax Agent OpenEnv Interactive Evaluation")
    gr.Markdown(
        "Select a tax task from the dropdown below and click 'Run Agent on Selected Task' to observe the autonomous agent interact with the environment. "
        "The agent will execute steps sequentially, navigating state changes and receiving incremental rewards based on its actions. "
        "Detailed logs of conversation and environment state are presented in the Chatbot interface, with rewards and state status updated continuously in the dedicated boxes."
    )
    gr.Markdown(
        "**Note:** Ensure that your `HF_TOKEN` environment variable is set securely (or as a Secret in Hugging Face Spaces deployment) for the OpenAI compatible model endpoint to function without errors."
    )

    with gr.Row():
        # Task Selection and Primary Metrics
        task_dropdown = gr.Dropdown(choices=task_options, label="Select Tax Task", index=0)
        total_reward_display = gr.Number(label="Total Cumulative Reward", value=0.0, interactive=False)
        current_reward_display = gr.Number(label="Incremental Step Reward", value=0.0, interactive=False)

    # Main Visual Interaction Log (Chatbot component is ideal for conversation + environment history)
    chatbot = gr.Chatbot(label="Agent Conversation & Environment Step-by-Step State Logs")
    
    with gr.Row():
        # Detailed feedback and status monitoring
        current_feedback = gr.Textbox(label="Last Environmental Feedback", interactive=False)
        current_state_status = gr.Textbox(label="Environment State Status", value="Not Started", interactive=False)
        agent_action_details = gr.Textbox(label="Parsed Agent Action / Reasoning Status", value="Choose a task and click 'Run Agent on Selected Task' to begin.", interactive=False)

    with gr.Row():
        # User controls
        run_btn = gr.Button("Run Agent on Selected Task", variant="primary")
        reset_btn = gr.Button("Reset Everything")

    # --- Interactions Definition ---
    # generator function run_agent_generator will update Chatbot, Numbers, and all Textboxes iteratively
    # queuing (demo.queue()) is crucial for generator updates to work properly
    run_btn.click(fn=run_agent_generator, inputs=[task_dropdown, chatbot, total_reward_display], outputs=[chatbot, current_reward_display, total_reward_display, current_feedback, current_state_status, agent_action_details])
    reset_btn.click(fn=reset_ui, outputs=[chatbot, current_reward_display, total_reward_display, current_feedback, current_state_status, agent_action_details])

if __name__ == "__main__":
    # Launch without public share link, assuming deployment on Hugging Face Spaces Docker template.
    # The default port 7860 should be used or exposed. Queue is needed for generators.
    demo.queue().launch(server_name="0.0.0.0", server_port=7860, share=False)
