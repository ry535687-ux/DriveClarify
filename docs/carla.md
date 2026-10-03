# 安装与运行

在具备 NVIDIA 驱动的 Linux x86_64 普通用户下，从仓库根目录运行 `bash reproduce.sh --native`，完成准备并启动一条路线的 A0/A1 配对。基础主机要求及完整 220 路线命令见 [README](../README.md)。无需打开上游安装教程。

## 安装顺序与位置

| 步骤 | 自动准备的内容 | 默认位置 |
| --- | --- | --- |
| 工具入口 | 独立 Python 3.10+ 工具环境、当前项目 | `.venv/` |
| 原生环境 | Python 3.8.18、Torch 2.2.0、固定原生包 | `build/native/env/` |
| Conda 引导 | 无 Conda 时下载并校验固定 Miniforge 安装器 | `build/native/miniforge/` |
| 仿真与地图 | CARLA 0.9.15、额外地图；检查所有 12 个 Town | `build/native/carla/` |
| 后端与路线 | 固定提交、补丁、237 份冻结源文件、220 份 XML | `build/native/workspace/simlingo/` |
| 模型 | 原始权重、适配权重、InternVL2-1B 与配置 | `build/native/workspace/` |
| 新运行 | 独立代码副本、模型链接、运行日志与原始结果 | `build/native/run-*/` |
| 收集与合并 | 首次合法结果、逐路线表、两臂合并指标 | 计划目录的 `summary-*/` |

安装清单包含在 wheel 中，也可安装包后直接运行 `driveclarify setup`。`driveclarify setup --check` 只显示平台、所需地图、下载对象和空间预算，不下载。没有 NVIDIA 驱动或缺少系统运行库时，入口会先报出具体条件。主机驱动由运行者安装；脚本不会修改系统驱动或全局 Python 环境。

首次须联网访问 GitHub、Hugging Face、PyPI、Conda 源与 CARLA 对象存储。仿真与地图归档约 15.8 GB，模型和原生 CUDA 环境另需空间；建议使用 SSD，至少预留 100 GiB。完成后重复运行不需要重新下载完整资产，但仍会检查摘要。

## 自定义与复用

```bash
# 改变安装根目录及 GPU
bash reproduce.sh --native --root /data/driveclarify --gpu 1

# 复用已安装的仿真和独立原生环境；模型与源代码仍由入口准备
bash reproduce.sh --native --carla-root /data/CARLA_0.9.15 \
  --native-python /data/native-env/bin/python --setup-only

# 复用已经准备好的完整路径清单；只校验、创建新计划和运行
bash reproduce.sh --native --paths /data/driveclarify/workspace/paths.local.json \
  --output build/reused-smoke
```

`--carla-root` 必须是 0.9.15 且包含所有路线地图；`--native-python` 必须是 3.8.18 且已安装原生清单中的包。路径清单由准备工具生成，不需要手写。旧环境仅在明确配置 `native_extra_site_packages` 时才增加该路径；工具环境的依赖不会混入原生环境。

## 失败后继续

下载保存 `.partial`，验证大小、模型 SHA-256 或固定运行时对象身份后才变成正式文件。已完成的 CARLA 解压步骤、原生包安装和后端还原均有凭据；重新执行原命令复用这些步骤。已有不同内容的环境或后端不会被覆盖，应使用新的 `--root` 或 `--paths` 指定已准备工作区。

运行计划不可覆盖。在路线之间中断后使用 `--resume --output 原计划目录`。若当前路线的尝试目录已建立但没有最终结果，按下面的技术重试流程创建新尝试。合法的碰撞、阻塞、超时和低分结果会保留；基础设施崩溃不能当成完成结果，也不能通过重跑挑最高分。发生技术故障时，查看该路线尝试目录的 `evaluator.log`，记录修复原因后最多进行三次技术尝试：

```bash
bash reproduce.sh --native --resume --output build/native/full \
  --attempt 2 --repair-note '说明修复的环境故障'
```

技术重试完成后通过本项目入口收集并合并：

```bash
.venv/bin/driveclarify benchmark collect --plan build/native/full \
  --output build/native/full/summary-repaired --merge
```

不完整的结果收集返回状态码 2，并在 `RESULT_SOURCES.json` 中列出缺失路线；不会生成完整基准成绩。合并调用经摘要验证的固定官方脚本，无需手工进入后端目录。单路线检查会保留官方少于 220 路线的提示。

## 复现范围

所有路线、种子、权重身份和 A0/A1 次序固定，运行时副本另有摘要。A0/A1 使用同一适配权重，A1 的澄清上下文为空；这是原生透明旁路基准，不是真实回答绑定的任务收益验证。标准收集提供 Driving Score、Success Rate 与逐路线结果；包含真实回答的任务实验及其他专用能力指标需要相应场景输入。

CPU 测试与受控评测已在干净安装、wheel 和 CI 中验证。安装器下载验证、工作区恢复、地图存在性和 440 次计划命令另行检查；空白 GPU 主机上的全量安装及真实驾驶尚未完成验收。仓库不附带不存在的最终 220 路线结果。
