"""RQ1 条件化补充分析（离线、CPU-only、不新增 VLA 前向）。

本包只读取既有记录，不采集、不训练、不驾驶。三项分析：
1. 轨迹差异 × 任务差异四格（含事后阈值敏感性）；
2. M3 / M4 / M5 在同一份保存输入上的机制对照；
3. 输入缺失压力测试（离线 mask 变体）。

来源 A/B/C/D 分别报告，不合并为一个总体正确率。
"""

CPU_ONLY_CONTRACT = {
    "creates_cuda_context": False,
    "loads_gpu_model": False,
    "new_vla_forward_count": 0,
    "starts_carla": False,
    "training_performed": False,
    "modifies_pid_or_adapter_or_execution_stack": False,
    "simulated_trajectories_generated": False,
}
