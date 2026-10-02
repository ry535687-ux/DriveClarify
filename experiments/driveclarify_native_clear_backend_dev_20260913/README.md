# 公开任务原生初始化诊断 V1（默认关闭）

本目录只准备 `DC_NATIVE_CLEAR_INIT_DIAG_V1` 的 A/B 两个输入。`configs/case_A.json`、`case_B.json` 均为 `enabled=false`，无隐藏意图、无 ASK。没有执行入口或自动调度器。路线 XML 是已有完整原生文件的逐字节副本；不是 STEP3A 连接段，也不使用 CPURequestBackend。

在仓库根目录运行以下命令只解析文件、检查摘要/引用并打印未来原生命令：

```bash
python -B experiments/driveclarify_native_clear_backend_dev_20260913/dry_run.py --case A
python -B experiments/driveclarify_native_clear_backend_dev_20260913/dry_run.py --case B
python -B -m unittest discover -s experiments/driveclarify_native_clear_backend_dev_20260913/tests -v
```

`--execute` 不存在。脚本不 import 原生模块，不调用 subprocess，不建模型、不占端口/GPU，也不创建运行输出目录。默认检查 checkpoint 存在性/大小及已取得的摘要身份；`--verify-checkpoint-bytes` 可只读重新计算文件摘要，不反序列化权重。其他冻结源每次检查摘要。实际运行须独立授权，见报告 `RUN_CARD.md`。

正常原生 evaluator 从完整 XML 加载本次宿主路线，再向同一 agent 设置 global plan。每次公开任务使用自身路线/导航；不是在线安装或途中回答切换。XML 中 z 坐标、场景、天气全部保留。实际地图 trace/道路命令由原加载器在未来运行时产生；不手填 RoadOption。旧 py3.8 egg 路径不存在，命令使用所选 Conda Python 已安装的 CARLA 0.9.15（仅文件检查，未 import）。

`extract_observer.py` 读取已有 CP1 世界状态 JSONL。评价只使用 `snapshot_frame`、`snapshot_elapsed_seconds`、`snapshot_delta_seconds`、`ego.location_xyz` 和预声明门；方法选择、真值、RC 不进入事件函数。三个端点是 **有向闭边界到达**：`da<0<=db` 且在门宽内，包括接触后退；不认证严格穿入正侧或物理不可恢复。`local_end` 位于原生终点之前，不改变官方终止。门中心/法向来自原 XML 出口路点，1.5m 半宽是草案参数，不是实测车道宽。

cadence 来自当前原生 evaluator 的 20 Hz 设置（每帧 0.05s）；容差 1e-5s 是预声明数值容差。检查帧差、时间差和快照 delta；真实一致性仍待运行。缺帧打断顺序，已观察的连续三门正事件可保留；不由文件存在推出全程覆盖、安全或任务成功。原生 RC/宿主原因单列。模型证据需要观测身份、调用前后时间及真实输出，控制证据需要同观测的返回控制值；计数不能替代这些字段。现有日志没有逐次 CARLA apply_control 回执，输出保持 null。

未来授权后离线整理示例（本轮未执行该真实输入）：

```bash
python -B experiments/driveclarify_native_clear_backend_dev_20260913/extract_observer.py --case A --world-state <A输出目录>/world_state.jsonl --route-receipt <A输出目录>/ROUTE_BINDING_RUNTIME.json --native-statistics <A输出目录>/leaderboard_results.json
```

整理器只输出 JSON；保存时应写入对应运行的新整理文件，保留原日志。缺文件/格式错误退出 2，输出结构化原因；不补答案、成功或过程结束原因。单测轨迹全部为合成数学夹具，不能加入论文样本。
