from typing import Tuple, Dict, Any
from src.models import TaxAction, TaxObservation
from src.tasks import TaxTask, grade_submission

class TaxEnvironment:
    def __init__(self, task: TaxTask):
        self.task = task
        self.turn_count = 0
        self.max_turns = 8
        self._state = {
            "income": self.task.income,
            "applied_deductions": 0.0,
            "calculated_liability": 0.0,
            "status": "started"
        }

    def state(self) -> Dict[str, Any]:
        """Returns the current state of the environment."""
        return self._state

    def reset(self) -> TaxObservation:
        """Initializes and returns the first observation."""
        self.turn_count = 0
        self._state["applied_deductions"] = 0.0
        self._state["calculated_liability"] = 0.0
        self._state["status"] = "in_progress"
        
        return TaxObservation(
            current_state="started",
            feedback=f"Task initialized. {self.task.description}"
        )

    def step(self, action: TaxAction) -> Tuple[TaxObservation, float, bool, Dict[str, Any]]:
        """Executes action, returns (observation, reward, done, info) with incremental rewards."""
        self.turn_count += 1
        feedback = ""
        done = False
        reward = 0.0 # Meaningful incremental reward

        if action.action_type == "apply_deduction":
            amount = action.parameters.get("amount", 0.0)
            if amount <= self.task.standard_deduction and amount > 0:
                self._state["applied_deductions"] += amount
                reward += 0.2 # Incremental reward for correct step
                feedback = f"Successfully applied deduction of {amount}."
            else:
                reward -= 0.1 # Penalty for invalid amount
                feedback = "Failed. Deduction amount invalid or exceeds allowed standard deduction."

        elif action.action_type == "calculate_tax":
            taxable_income = max(0, self._state["income"] - self._state["applied_deductions"])
            calculated = taxable_income * 0.20
            self._state["calculated_liability"] = calculated
            reward += 0.2 # Incremental reward for calculating
            feedback = f"Calculated preliminary liability: {calculated}."

        elif action.action_type == "submit_filing":
            submitted_liability = action.parameters.get("liability", 0.0)
            done = True
            self._state["status"] = "finished"
            
            # Final grading mechanism (0.0 to 1.0)
            final_score = grade_submission(submitted_liability, self.task.target_liability)
            reward += final_score 
            
            feedback = f"Filing submitted. Grader score: {final_score}/1.0."

        else:
            reward -= 0.2 # Penalty for hallucinated actions
            feedback = f"Error: '{action.action_type}' is an invalid action."

        # Penalty for infinite loops/getting stuck
        if self.turn_count >= self.max_turns and not done:
            done = True
            self._state["status"] = "terminated"
            reward -= 0.5
            feedback = "Task terminated: Maximum turns reached."

        obs = TaxObservation(current_state=self._state["status"], feedback=feedback)
        info = {"turn_count": self.turn_count, "state": self.state()}
        
        return obs, float(reward), done, info
