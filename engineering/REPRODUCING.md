# 实验复现

## 可复现层级

| 层级 | 输入 | 操作 | 当前状态 |
| --- | --- | --- | --- |
| CPU 合同 | 手工 fixture、schema、测试 | `test` / `demo` | 本机已验证 |
| 论文离线计算 | 41 个输入文件与 14 个参考输出 | `paper` | 本机独立目录重算逐字节一致 |
| 历史原生统计 | 冻结 episode 表、配对结果 | 包含在 `paper` 中 | 统计重算；不重跑车辆 |
| 图表及 LaTeX | 图源、原生地图、相机归档、论文源 | 见下文 | 原有产物可查；本次未重新绘图/编译 |
| CARLA 新运行 | 环境、权重、地图、完整场景、原生接口 | 只提供依赖检查及历史入口导航 | 尚未达到可直接全量复现状态 |

## CPU 论文复现

按 README 安装 `requirements/paper.lock.txt`，然后执行：

```bash
python engineering/reproduce.py doctor --profile paper
python engineering/reproduce.py paper --output build/paper-001
```

此入口保持原文件的相对布局，在新目录依次执行：

```text
deliverables/paper_revision_20260915/analyze.py predict
deliverables/paper_revision_20260915/analyze.py analyze
deliverables/paper_revision_20260915/main_table_selectivity.py
deliverables/paper_revision_20260915/ablation_statistics.py
deliverables/paper_revision_20260915/analyze.py closed-loop
```

顺序保留“先锁定预测，再读取评分标签”的原协议。41 个输入中包含上游 benchmark manifest 的全部 30 个冻结来源；该 manifest 的校验仍由原脚本执行。14 个参考结果覆盖预测锁、逐记录结果、主表六指标、配对统计及历史闭环统计。`logs/01.log` 至 `05.log` 保存各步骤输出。

`REPRODUCTION_RECEIPT.json` 的 `PASS` 只表示这些冻结数据上的输出重现；不包含新训练、视觉模型前向、原始数据重新采集或新驾驶。参考结果不自动更新；若更换 NumPy / SciPy 导致数值序列或序列化变化，应检查差异，不能直接刷新摘要消除失败。

`requirements/paper.lock.txt` 锁定直接及传递依赖，并提供 PyPI 发布文件的 SHA-256；附 Python 3.10 和 Windows 的条件依赖。已在本机 Python 3.13.5 的新建虚拟环境执行干净安装，305 项测试与 14 个参考输出均通过。远程 Python 3.10 的 CPU 合同测试已通过，但论文参考输出的逐字节检查未通过，因此论文复现明确固定使用 Python 3.13；不通过修改参考摘要或放宽比较消除差异。CI 在两种 Python 上运行合同测试，在 Python 3.13 上运行完整论文重算。容器构建按实际结果报告。

## 原生环境与模型

SimLingo 原生环境和 CPU 分析环境分别管理，避免 Python / NumPy / CUDA 版本冲突。

| 项目 | 本机记录 | 来源 |
| --- | --- | --- |
| SimLingo 基础提交 | `743b243afd6cf5ff51b9fa1f8cac86f22d569684` | 本机 Git HEAD |
| CARLA 服务端 / Python API | `0.9.15` | 历史 BACKEND_IDENTITY 与已安装包元数据 |
| 原生 Python | `3.8`（上游环境文件为 `3.8.18`） | `env/simlingo-upstream.yaml` |
| PyTorch / torchvision | `2.2.0` / `0.17.0` | 本机分发元数据 |
| NumPy / Transformers | `1.23.0` / `4.46.3` | 本机分发元数据 |
| 视觉模型 | `OpenGVLab/InternVL2-1B` | 原生 BACKEND_IDENTITY |
| 其他包 | [原生环境快照](env/simlingo-observed.txt) | 读取元数据，未 import 模型库 |

上游安装流程见 [SimLingo README](https://github.com/RenzKa/simlingo#setup) 与 [CARLA 0.9.15 安装说明](https://carla.readthedocs.io/en/0.9.15/start_quickstart/)。本项目保存了上游 `environment.yaml` 的副本，但它和本机已安装环境不是同一个东西：`simlingo-observed.txt` 用于比对，不宣称可直接 `pip install -r` 还原所有二进制依赖。CUDA、驱动、FlashAttention 和物理显示仍需在目标机器检查。

### 上游修改还原

本机 SimLingo 有 13 个 tracked 文件修改及 4 个未跟踪源文件。仅 checkout 基础提交不能还原 DriveClarify。已保存：

- `vendor/simlingo-local.patch`：相对基础提交的 tracked diff。
- `vendor/simlingo-extra/`：四个新增 `.py` / `.yaml` 文件。
- `native_assets.json`：还原后文件摘要、关键包版本、权重身份及协议入口。

优先使用下面的独立工作区准备命令（需要 Git；请把 CARLA 和 Python 路径换成目标机器的安装位置）：

```bash
python3 engineering/prepare_native.py --workspace build/native-workspace \
  --carla-root /path/to/CARLA_0.9.15 \
  --native-python /path/to/envs/simlingo/bin/python
python3 engineering/assets.py --profile native --output build/native-workspace
python engineering/reproduce.py doctor --profile native \
  --paths build/native-workspace/paths.local.json --hash-weights
```

第一条命令只克隆、checkout、应用补丁和核对 20 个文件摘要，已用本地上游仓库验证；不安装原生依赖，也不启动驾驶。先执行准备命令，再执行模型下载，避免克隆目录预先非空。最后的 doctor 仍返回 2，表示原生运行资格未完成。

也可手动在另一个**新克隆**中还原。以下假定两个仓库为同级目录：

```bash
git clone https://github.com/RenzKa/simlingo.git ../simlingo-reproduction
git -C ../simlingo-reproduction checkout 743b243afd6cf5ff51b9fa1f8cac86f22d569684
git -C ../simlingo-reproduction apply --check ../DriveClarify/engineering/vendor/simlingo-local.patch
git -C ../simlingo-reproduction apply ../DriveClarify/engineering/vendor/simlingo-local.patch
cp -a engineering/vendor/simlingo-extra/. ../simlingo-reproduction/
```

若当前源码目录不叫 `DriveClarify`，相应调整上面两个补丁路径。补丁保存的是当前本机状态；某一历史实验的冻结身份仍以该实验自己的 manifest 为准，不能把所有历史版本混用为一个配置。

### 权重和地图

两套权重具有不同实验角色，不能互换：

| 配置键 | 字节数 | SHA-256 | 用途 |
| --- | ---: | --- | --- |
| `base_checkpoint` | 2569679322 | `ec8943723d266ee9f5f56f45d153a163b22616960bfccb741965ea5daa700d28` | 原生公开任务初始化身份 |
| `b2d_checkpoint` | 2569681502 | `cc6873e2a7778140ff7af3fd7d3578114b26bd6a47974e955f0845ddd2178044` | 冻结 Bench2Drive V2 的 A0/A1 共同权重 |

适配权重已在本机流式读取并验证冻结摘要；基础权重已核对公开上游 LFS 的 SHA-256 与字节数。`--hash-weights` 可显式执行流式 SHA-256；不反序列化模型。对应 Hydra 配置已按冻结摘要保存为 `env/base-model-config.yaml` 与 `env/b2d-model-config.yaml`；其中历史宿主路径仍需在目标环境的新副本中调整。权重与模型缓存的完整下载清单在 `release_assets.json`；下载及分片合并使用 `assets.py --profile native`。基础权重固定到 Hugging Face 提交 `26c7c89e797d4e25bbf640013317af8da26a5454`，InternVL2-1B 固定到 `0d75ccd166b1d0b79446ae6c5d1a4a667f1e6187`，本机视觉大权重也已流式校验。适配权重、训练来源与历史记录已打包，GitHub Release 大文件上传等待网页授权。CARLA 服务端与额外地图仍按上游安装流程获取，不进入源码 Git 仓库。

原生依赖预检：

```bash
cp engineering/paths.example.json engineering/paths.local.json
# 编辑 paths.local.json；相对路径以 DriveClarify 仓库根目录为基准。
python engineering/reproduce.py doctor --profile native --paths engineering/paths.local.json
# 如需校验大权重字节：
python engineering/reproduce.py doctor --profile native --paths engineering/paths.local.json --hash-weights
```

本机已生成的 `paths.local.json` 不进入发布包。检查器不会运行指定的原生 Python，也不会 import `carla` / `torch`。存在性、版本与摘要各自报告；原生 profile 当前固定返回 **2** 表示整体复现仍待完成，不是仿真已通过。新配置仅接入检查器，尚未替换全部历史绝对路径。

## 原生实验入口与数据

| 实验角色 | 源入口 / 数据合同 | 复现前还需处理 |
| --- | --- | --- |
| 公开完整路线初始化 | `experiments/driveclarify_native_clear_backend_dev_20260913/` | 迁移 `BACKEND_IDENTITY.json` 的宿主路径；核对配置、路线和当前运行条件 |
| 历史局部停靠配对 | `tools/rq3_paired_v2/`、`driveclarify_rq3_paired_v2/` | 完整场景/路线/逐帧归档；遵守对应历史协议 |
| 新答案绑定开发 | `driveclarify_paper_runtime/agent_entry.py` | 真实分支场景、独立截止点、正式 RGB 及任务正确性资格 |
| 完整 Bench2Drive V2 | `reports/driveclarify_transparent_bypass_full_bench2drive_v2/tooling/` | 上游修改、适配权重、220 路线、配置迁移、新输出根目录 |

历史 `scripts/run_full_bench2drive_unattended.sh` 会启动宿主 systemd 服务，工具中也有固定输出路径与恢复行为，不能直接作为新机器的快速开始命令。此次工程入口没有隐式原生运行分支。

当前发布预览包含可复现离线输入与相关原生协议，**不是所有历史运行数据的备份**。大文件归档提供本机已有的五组历史实验记录（4226 文件）及适配训练配置、输入、选择记录（503 文件）；真实相机画面只包含本机已有文件，不能补出未采集的画面。尚缺的完整复现项是：论文采用的外部 220 路线最终 A0/A1 逐路线结果、新答案绑定运行资格，以及所有历史入口的可迁移运行环境。旧中途账本或名为 `FINAL_*` 的文件不能替代缺失的最终结果。

## 图表与论文

原论文分析脚本位于 `deliverables/paper_revision_20260915/`，归档 LaTeX 位于 `deliverables/paper_revision_20260915/source_archive/paper_ieeeconf_en/`。论文已存在的源码包是该 deliverables 下的 `DriveClarify_8page_LaTeX.zip`。编译命令为在解压后的 `paper_ieeeconf_en/` 执行 `bash build.sh`；所需编译器与历史验证见同目录 `FINAL_REPORT.md`。

源码仓库不包含整份论文和图片；`assets.py --profile paper-artifacts` 可下载 224 份已有论文源和附件。真实案例图的依赖记录位于 `deliverables/paper_revision_20260915/figure_sources.json`，其中所需 SimLingo `Town03.h5`、历史场景照片及逐帧轨迹已收录进现有记录归档。可选绘图依赖是 `requirements/figures.txt`；仅安装绘图库不能生成缺失的真实相机画面。

不要直接重跑 `revise_paper.py` 覆盖当前论文；已有用户编辑超出了旧模板。方法图与开头图仍由另一台机器维护。
