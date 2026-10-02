"""固定观测任务关系与询问选择性评价。"""

from .contracts import AskDecision, MethodResult, TaskRelation
from .methods import evaluate_method

__all__ = ["AskDecision", "MethodResult", "TaskRelation", "evaluate_method"]
