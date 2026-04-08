class TaxTask:
    def __init__(self, difficulty_level: str, income: float, standard_deduction: float, target_liability: float):
        self.difficulty_level = difficulty_level
        self.income = income
        self.standard_deduction = standard_deduction
        self.target_liability = target_liability
        self.description = (
            f"Difficulty: {difficulty_level.upper()}. Review the client's income of {income}, "
            f"apply the standard deduction of {standard_deduction}, calculate the tax liability "
            "(assume a flat 20% tax rate on taxable income), and submit the filing."
        )

def get_eval_tasks():
    """Returns the required minimum of 3 tasks of increasing difficulty."""
    return [
        TaxTask(difficulty_level="easy", income=50000.0, standard_deduction=10000.0, target_liability=8000.0),
        TaxTask(difficulty_level="medium", income=120000.0, standard_deduction=15000.0, target_liability=21000.0),
        TaxTask(difficulty_level="hard", income=350000.0, standard_deduction=25000.0, target_liability=65000.0)
    ]

def grade_submission(submitted_liability: float, target_liability: float) -> float:
    """Deterministic programmatic grader returning a score between 0.0 and 1.0"""
    if submitted_liability == target_liability:
        return 1.0
    # Provide partial credit if they are close (e.g., math rounding error)
    elif abs(submitted_liability - target_liability) <= 1000:
        return 0.5
    else:
        return 0.0
