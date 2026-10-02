"""仅由本轮 runner 显式选用；标准 Bench2Drive 不导入此入口。"""
from driveclarify_clear_task_diagnostic.runtime import initialize
initialize()
from driveclarify_clear_task_diagnostic.agent import ClearTaskAgent, get_entry_point
