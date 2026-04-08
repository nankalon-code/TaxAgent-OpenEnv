import os
import json
from openai import OpenAI
from dotenv import load_dotenv

from src.environment import TaxEnvironment
from src.tasks import get_eval_tasks
from src.models import TaxAction

load_dotenv()

def run_evaluation():
    # Uses OpenAI client, but auths via HF_TOKEN as required
    # Pointing to HF Serverless Inference using an OpenAI compatible model
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        raise ValueError("HF_TOKEN environment variable is missing.")

    client = OpenAI(
        base_url="https://api-inference.huggingface.co/v1/",
        api_key=hf_token
    )
    
    tasks = get_eval_tasks()
    total_score = 0.0
    
    for i, task in enumerate(tasks):
        print(f"\n--- Starting Task {i+1}: {task.difficulty_level.upper()} ---")
        env = TaxEnvironment(task=task)
        
        system_prompt = (
            "You are an autonomous tax agent. Output ONLY valid JSON matching this schema:\n"
            "{\"action_type\": \"<action>\", \"parameters\": {\"<key>\": <value>}}\n"
            "Valid actions: apply_deduction (needs 'amount'), calculate_tax (no params), submit_filing (needs 'liability')."
        )

        messages = [{"role": "system", "content": system_prompt}]
        obs = env.reset()
        done = False
        task_reward = 0.0
        
        while not done:
            user_msg = json.dumps({"feedback": obs.feedback, "environment_state": env.state()})
            messages.append({"role": "user", "content": user_msg})
            
            try:
                # Using a standard open model hosted on HF
                response = client.chat.completions.create(
                    model="meta-llama/Meta-Llama-3-8B-Instruct",
                    messages=messages,
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                
                agent_reply = response.choices[0].message.content
                messages.append({"role": "assistant", "content": agent_reply})
                
                action_data = json.loads(agent_reply)
                action = TaxAction(**action_data)
                print(f"Agent took action: {action.action_type} with params {action.parameters}")
                
            except Exception as e:
                print(f"Error/Hallucination: {e}")
                action = TaxAction(action_type="error", parameters={})
            
            # Step returns the OpenEnv tuple
            obs, reward, done, info = env.step(action)
            task_reward += reward
            print(f"Reward: {reward:.2f} | Feedback: {obs.feedback}")
        
        print(f"Task {i+1} completed. Cumulative Reward: {task_reward:.2f}")
        total_score += task_reward

    print(f"\n=== EVALUATION COMPLETE ===")
    print(f"Total Baseline Score across {len(tasks)} tasks: {total_score:.2f}")

if __name__ == "__main__":
    run_evaluation()
