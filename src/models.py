from pydantic import BaseModel, Field
from typing import Dict, Any

class TaxAction(BaseModel):
    action_type: str = Field(..., description="Action to perform: 'apply_deduction', 'calculate_tax', or 'submit_filing'")
    parameters: Dict[str, Any] = Field(default_factory=dict)

class TaxObservation(BaseModel):
    current_state: str
    feedback: str
