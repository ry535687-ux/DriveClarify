"""原生入口在缺失闭环前置证据时拒绝加载模型，不自动生成候选或修补安全门。"""
import json
import os
from pathlib import Path


def get_entry_point():
    config = json.loads(Path(os.environ["DC_ABL_CONFIG"]).read_text())
    # 本轮尚无 producer 资格与阈值标定；禁止仅手填 ready=true 绕过。
    raise RuntimeError("ABLATION_LIVE_BLOCKED_TIMED_CANDIDATE_PRODUCER_UNIMPLEMENTED:"
                       + config["configuration_id"])
