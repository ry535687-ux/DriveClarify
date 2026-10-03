# CARLA / Bench2Drive 安装与运行

## 1. 安装原生 Python 环境

需要 Linux x86_64、Git、Bash、Conda、可用于 CARLA 的 NVIDIA 驱动，以及独立的 Python 3.10+ 工程工具环境。原生模型使用 Python 3.8.18；不要将 CPU 分析依赖安装进原生环境。

```bash
# 从 DriveClarify 仓库根目录执行，创建全新环境
bash scripts/install_native.sh "$PWD/build/native-env"
```

脚本按本机 Conda 精确包 URL/MD5 建立环境，先安装 PyTorch 2.2.0 / torchvision 0.17.0，再安装 197 个观测包版本和 DeepSpeed 0.16.2，最后执行 `pip check`。197 个版本的公开 PyPI 记录与 Python 3.8 支持范围已核验；**本轮未在新 GPU 主机完成这份原生环境的干净安装或驾驶验证**。实际安装失败时保留日志并定位包或系统依赖，不应直接升级冻结的 NumPy / Torch / Transformers。

脚本拒绝覆盖已有环境。原环境依赖有一部分位于用户 site-packages，因此新环境把这些依赖显式装入自身；默认关闭隐式用户包。如果复用经过审计的旧环境，可在 `paths.local.json` 中显式增加 `native_extra_site_packages` 路径；不要复制他人的宿主路径。

本机只读硬件记录为 RTX 4070 Ti、12,282 MiB、驱动 575.57.08；这不是对其他机器显存容量的通过保证。模型与 CARLA 的合计显存压力由首次单路线运行检查。

## 2. 安装 CARLA 0.9.15 与 AdditionalMaps

需要基础服务端及额外地图，220 路线中包含 Town12 / Town13。下载入口来自冻结 SimLingo 的 `setup_carla.sh`；两条 URL 已检查可访问。两个压缩包合计约 15.8 GB，解压后的地图、原生环境、模型及运行数据还需要额外空间；请在运行主机为这些目录预留足够磁盘。

```bash
mkdir -p build/carla-install
cd build/carla-install
curl --fail -L -C - -o CARLA_0.9.15.tar.gz \
  https://carla-releases.s3.us-east-005.backblazeb2.com/Linux/CARLA_0.9.15.tar.gz
mkdir CARLA_0.9.15
tar -xf CARLA_0.9.15.tar.gz -C CARLA_0.9.15
curl --fail -L -C - -o CARLA_0.9.15/Import/AdditionalMaps_0.9.15.tar.gz \
  https://carla-releases.s3.us-east-005.backblazeb2.com/Linux/AdditionalMaps_0.9.15.tar.gz
cd CARLA_0.9.15
bash ImportAssets.sh
cd ../../..
```

额外地图导入及系统图形依赖以 [CARLA 0.9.15 安装说明](https://carla.readthedocs.io/en/0.9.15/start_quickstart/) 为准。CARLA 启动器与原生 Python API 都固定为 0.9.15。

## 3. 还原源码、下载模型和路线

```bash
# workspace 必须尚无 simlingo 子目录；之后可以重复执行资产下载续传
driveclarify prepare --workspace build/native-workspace \
  --carla-root "$PWD/build/carla-install/CARLA_0.9.15" \
  --native-python "$PWD/build/native-env/bin/python"
driveclarify assets --output build/native-workspace
driveclarify benchmark preflight \
  --paths build/native-workspace/paths.local.json
```

还原步骤 checkout 固定 SimLingo 提交、应用已保存的本地补丁，并放入 220 个原始路线 XML。预检核对固定上游源文件、最终包文件及所有路线、B2D 权重、视觉缓存及关键分发元数据，不 import Torch / CARLA，也不启动 GPU。

模型下载固定上游提交，本项目适配权重从 [v0.1.0-assets](https://github.com/ry535687-ux/DriveClarify/releases/tag/v0.1.0-assets) 获取；分片自动校验、合并，再检查完整 SHA-256。这个基准使用 `b2d_checkpoint`，不是 `base_checkpoint`。

## 4. 先试跑第一条配对路线

```bash
# 只生成新计划及运行副本；不启动模型
driveclarify benchmark plan \
  --paths build/native-workspace/paths.local.json \
  --output build/b2d-smoke-001 --offscreen

# 查看实际命令，不执行
driveclarify benchmark run --plan build/b2d-smoke-001 --dry-run

# 依次运行这条路线的 A0、A1；此命令会加载模型并自动启动 CARLA
driveclarify benchmark run --plan build/b2d-smoke-001
```

默认路线 ID 为 `1711`，种子为冻结的 `902609062`。也可以在 `plan` 时指定 `--route-id`，或用 `--gpu` 指定 CARLA 图形设备编号。Torch 使用调用终端的 `CUDA_VISIBLE_DEVICES`，多 GPU 主机请保证两者对应同一目标设备。`--offscreen` 生成带 `-RenderOffScreen` 的 CARLA 启动副本；有显示的机器可去掉它，并在终端设置有效的 `DISPLAY` / `XAUTHORITY`。

新目录的 `PLAN.json` 记录原始冻结身份、运行副本摘要与迁移范围。只修改运行副本中的宿主路径、模型缓存路径和 GPU/显示启动设置；原模型参数、决策代码、路线和冻结配置快照不被覆盖。权重通过符号链接引用，避免每个计划重复占用 2.57 GB；计划生成后不要移动目录或改动输入。

结果位于 `build/b2d-smoke-001/results/1711/A0/attempt_01/` 和对应 A1 目录。检查 `evaluator.log`、`official_checkpoint.json`、`agent_setup.json`、`agent_terminal.json`、`PROCESS_RECEIPT.json`；两臂共有原生 tick / run_step / PID，澄清 context 为空。正式资格仍以目标机器的真实输出为准，本轮只验证了预检、计划与命令生成。

## 5. 运行完整 220 路线配对

首次配对确认环境能正常运行后，在另一个新目录生成完整计划：

```bash
driveclarify benchmark plan \
  --paths build/native-workspace/paths.local.json \
  --output build/b2d-full-001 --all --offscreen
driveclarify benchmark run --plan build/b2d-full-001

# 中断后保留第一份合法最终结果，继续尚未完成的路线
driveclarify benchmark run --plan build/b2d-full-001 --resume
```

路线和交替 A0/A1 次序沿用冻结清单；只设置原协议的交通管理器种子，没有额外强制 Torch 的确定性。一次只运行一个 evaluator；原有进程 owner 代码清理本次实际派生的 CARLA 进程，不启动旧的 systemd 队列。

碰撞、阻塞、合法策略超时和低分均属于应保留的驾驶结果，不会因分数低重试。基础设施失败会停止队列；先排查日志再做明确技术重试，例如：

```bash
driveclarify benchmark run --plan build/b2d-full-001 \
  --route-id 1711 --arm A0 --attempt 2 \
  --repair-note "已定位并修复地图导入失败；科学配置未改变"
driveclarify benchmark run --plan build/b2d-full-001 --resume
```

重试最多三个技术尝试，必须记录修复原因；已有合法最终结果时拒绝重跑。输出目录不覆盖，所有原始 JSON 保留。

## 6. 收集新结果并调用官方合并

```bash
driveclarify benchmark collect --plan build/b2d-full-001 \
  --output build/b2d-summary-001

build/native-env/bin/python \
  build/native-workspace/simlingo/Bench2Drive/tools/merge_route_json.py \
  --folder build/b2d-summary-001/official_merge/A0
build/native-env/bin/python \
  build/native-workspace/simlingo/Bench2Drive/tools/merge_route_json.py \
  --folder build/b2d-summary-001/official_merge/A1
```

`RESULT_SOURCES.json` 绑定每条路线首次合法结果的路径与摘要；`PAIRED_ROUTE_RESULTS.csv` 保留 DS / RC / penalty 原始字段。缺失一臂时写 `MISSING`，收集命令返回 2，不补零。只有 `complete=true` 且两臂各 220 条时，才能使用官方 merged 的全量 DS / Success；单路线试跑只是资格检查。

官方能力脚本为 `Bench2Drive/tools/ability_benchmark.py --file <完整 XML> --result_file <merged.json> --host localhost --port <CARLA RPC>`，需要可连接的 CARLA 地图服务；舒适度 / 效率脚本为 `efficiency_smoothness_benchmark.py --file <merged.json> --metric_dir <原生 metric_info 目录>`。保留每次运行的 `official_data/` 和原生 metric 文件，不用合成数据填补缺失帧。
