"""DriveClarify Counterfactual Fairness Contract v2（纯 CPU/标准库）。"""

from .contract import (
    CONTRACT_SCHEMA,
    evaluate_counterfactual_fairness,
)

__all__ = ["CONTRACT_SCHEMA", "evaluate_counterfactual_fairness"]
