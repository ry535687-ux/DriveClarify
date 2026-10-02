# STAGE3D-A 最终报告

最终状态：**`STAGE3D_A_DIAGNOSED_WATCHDOG_FIXED_B_GO_REVIEW`**。

A主类为 `A_PLAN_COMMANDS_LOW_SPEED`；次类 `A_RETURNED_CONTROL_NOT_SUFFICIENT_TO_ESTABLISH_APPLICATION` 仅指独立应用证据缺口。制动段原生公式重建目标速度范围0.002013–0.163962（中位0.029398），均低于0.4阈值，返回控制与该条件一致。pred_route持续非退化，原始第一轴递增。末段30.30–31.50秒目标升至7.379924、连续正油门，ego升至4.447166m/s并移动约1.543m。没有发现必须改PID/模型/owner/guard的确定性故障。

灯125的Red→Green与持续油门起点同帧，支持等灯解释；ego适用灯ID、场景实际触发和blocked状态未记录，不作确定因果归因。`ACTUAL_APPLY_CONTROL_INDEPENDENTLY_VERIFIED` 仍UNKNOWN；原生终点、独立任务门和安全记录均不升级。完整证据及不同时间原点说明见 [A_STALL_ANALYSIS.md](A_STALL_ANALYSIS.md) 和 [A_FRAME_ALIGNED_SUMMARY.csv](A_FRAME_ALIGNED_SUMMARY.csv)。没有重新统计论文或历史实验。

watchdog的实际修订是3.9秒有测量来源的清理预留：416.1秒TERM，0.5秒后必要时KILL。9次CPU校准和3种真实420秒预算验证均通过。最坏receipt总时长416.701418s，含整个wrapper返回的外部总时长416.735102s；子孙进程、临时端口清理通过，无关哨兵存活。结论仅 `WATCHDOG_CPU_CONTRACT_FIXED`。详见 [WATCHDOG_FIX_REPORT.md](WATCHDOG_FIX_REPORT.md) / [WATCHDOG_DUMMY_RESULTS.json](WATCHDOG_DUMMY_RESULTS.json)。

B决定：**`GO_B_NATIVE_PROBE_REVIEW`**。A末段已有运动，B的另一公开任务可提供独立后端信息，不需要先改生产控制。B未授权、未运行；旧Stage3C运行入口未自动接新wrapper，未来必须明确绑定修订版并重新满足物理显示/资源/输出等前置条件。见 [B_GO_NO_GO.md](B_GO_NO_GO.md)。本结论不是原生运行成功、在线切换、隐藏意图公平性或方法收益。

交付包含实际新源码/测试（scripts）、源码新增diff和相对旧wrapper diff、5/5解析单测日志、CPU dummy逐次回执、CSV/JSON一致性检查、限定输入保护校验及入口/出口Git记录。原A数据、Stage3B配置、5个引用源和旧Stage3C归档保持不变；只新增本报告开发版本并更新增量交接。两仓库HEAD/branch及预存tracked/staged diff保护检查见 `evidence/EXIT_IDENTITY.json`。没有commit/push/reset/clean/restore/checkout。

本轮启动CARLA、A重跑、B、模型forward、历史测试均为0；未修改生产/live、SimLingo/PID/UKF/owner/evaluator/scenario。未换路线/seed/checkpoint，未新增observer、调度器或通用框架。总工作在用户45分钟限额内，时间与命令记录见 `COMMAND_LOG.md`。核心清单 `ARTIFACT_MANIFEST.json`；未制作大复核包。

唯一下一步：用户审阅本轮证据与修订wrapper，决定是否另行授权B一次。完成后停止。
