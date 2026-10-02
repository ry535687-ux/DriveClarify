# DriveClarify

**面向语言指令驾驶的选择性澄清研究：结合候选任务后果、证据完整性与剩余时间，判断何时执行、等待或询问。**

这个仓库包含离线决策器、论文受控实验、历史闭环分析，以及与 SimLingo / CARLA 对接的研究代码。当前已提供可独立运行的 CPU 测试和论文统计复现入口；完整原生驾驶复现仍有明确的资产与迁移缺口，见[复现说明](engineering/REPRODUCING.md)。

## 方法概览

语言存在歧义，不一定意味着必须询问。DriveClarify 比较候选解释对任务的影响，保留未知证据，并检查回答及后续执行是否仍有时间完成。

```mermaid
flowchart LR
    A[语言指令与场景证据] --> B[候选解释与任务后果]
    B --> C[任务关系与证据完整性]
    C --> D[执行与询问时间条件]
    D --> E[ACT / WAIT / ASK]
    E --> F[合法回答与后续观测绑定]
    F --> G[原生导航与模型接口]
```

不同实验版本的后果比较器与执行接口有各自的冻结合同；不能将这张概念图视为所有历史版本都已通过的闭环能力证明。当前答案绑定组件位于 `driveclarify_paper_runtime/`。

## 快速开始

一键完成环境安装、测试、演示和论文统计复现：

```bash
git clone https://github.com/ry535687-ux/DriveClarify.git
cd DriveClarify
bash reproduce.sh
```

脚本会创建独立 `.venv-reproduce/`，按版本与下载文件 SHA-256 安装完整 CPU 依赖，并将结果写入新建的 `build/reproduction-<时间>-<进程号>/`。本机已在新建虚拟环境中验证 305 项测试及 14 个参考输出一致。原生 GPU 资产与驾驶环境另见复现说明；这条命令复现论文离线计算。

容器方式：

```bash
docker build -f Dockerfile.cpu -t driveclarify-cpu .
docker run --rm -v "$PWD/build:/workspace/DriveClarify/build" driveclarify-cpu
```

以下命令在仓库根目录运行。CPU 依赖使用 Python **3.10+**，本机已验证版本为 **3.13.5**。CARLA / SimLingo 使用独立的 Python 3.8 环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/paper.lock.txt

python engineering/reproduce.py doctor --profile paper
python engineering/reproduce.py test
python engineering/reproduce.py demo --output build/demo-001
python engineering/reproduce.py paper --output build/paper-001
```

`demo` 运行早期人工合成 fixture；`paper` 重新执行当前论文的离线预测、评分、选择性指标、配对消融及历史闭环统计。两者的样本与结论不同。所有输出目录必须不存在；再次运行请改为 `demo-002` / `paper-002`。

`paper` 会校验 [55 份输入及参考文件](engineering/paper_assets.json)，将所需文件复制到新目录后运行原始脚本，最后逐字节比较 14 个参考输出并生成 `REPRODUCTION_RECEIPT.json`。原始冻结代码、标签和历史结果保持原样。

本仓库暂按源码目录运行：冻结 schema、fixture 和论文输入位于 Python 包外。`pyproject.toml` 管理默认测试范围，依赖在 `requirements/`；暂不提供 `pip install -e .` 或独立 wheel。

## 模型、原始记录和论文附件

下载清单位于 [release_assets.json](engineering/release_assets.json)，包含固定上游版本、文件大小与 SHA-256。上游 SimLingo 基础权重和 InternVL2-1B 从 Hugging Face 下载；本项目的适配权重与实验归档使用 [GitHub Releases](https://github.com/ry535687-ux/DriveClarify/releases)。**本项目大文件已在本地打包，Release 上传正在等待 GitHub CLI 网页授权；完成前以下 Release 下载命令会报资产不存在。**

发布完成后的下载入口：

```bash
# 4226 份历史场景/轨迹/记录，以及适配训练的配置、输入和选择记录
python3 engineering/assets.py --profile evidence --output build/assets

# 论文 LaTeX、已有图表与交付附件
python3 engineering/assets.py --profile paper-artifacts --output build/assets

# 全部原生模型资产、历史记录；至少预留 20 GB 下载及解压空间
python3 engineering/assets.py --profile native --output build/native-workspace
```

重复运行可续传；每个文件先验证摘要再使用。适配权重自动合并分片，归档解压到独立目录，已有内容不同则拒绝覆盖。这些命令准备已有资产；原生代码还原、CARLA 地图和硬件条件见[复现说明](engineering/REPRODUCING.md#原生环境与模型)。

## 实验与入口

| 目标 | 入口 | 所需环境与状态 |
| --- | --- | --- |
| 合成决策与状态机验证 | `reproduce.py demo` | CPU；人工合成测试，不是驾驶结果 |
| 核心回归与开发原型 | `reproduce.py test` | CPU；明确选择测试集，不扫描全部历史实验 |
| 论文离线主表与消融 | `reproduce.py paper` | CPU；使用冻结输入、标签和历史记录 |
| 论文图表与排版 | [复现说明](engineering/REPRODUCING.md#图表与论文) | 部分图依赖原生地图、相机归档及外部论文素材 |
| SimLingo / CARLA 依赖检查 | `reproduce.py doctor --profile native --paths engineering/paths.local.json` | 只读检查；不加载模型、不启动仿真 |
| 新答案绑定接口 | `driveclarify_paper_runtime/agent_entry.py` | 开发入口；CPU 检查通过，真实分支资格尚未完成 |
| 历史 Bench2Drive 协议 | `reports/driveclarify_transparent_bypass_full_bench2drive_v2/` | 冻结协议及工具；本机缺论文采用的最终逐路线结果 |

表中的 `reproduce.py` 均指 `engineering/reproduce.py`。原生环境安装、两种不同权重的身份、上游补丁与数据关系，统一见[复现说明](engineering/REPRODUCING.md)。

## 目录导航

```text
engineering/                复现 CLI、资产摘要、环境记录与发布指南
requirements/               CPU / 论文分析 / 绘图依赖
driveclarify_offline/        早期 CPU 决策器与状态机
driveclarify_rq1_*/          任务后果关系与条件化分析
driveclarify_rq2_*/          历史证据、时间与可行动窗口实验
driveclarify_rq3*/           历史原生闭环与配对评价
driveclarify_paper_runtime/  新答案绑定开发接口
driveclarify_*v*/           保留版本身份的研究实现
experiments/                隔离开发原型与原生输入准备
tests/                      单元测试及各实验合同测试
tools/                      各阶段工具；不是一个统一执行队列
reports/                    实验协议、部分源脚本、冻结输入与历史证据
deliverables/               论文分析脚本与交付材料
build/                      新复现结果与本地发布预览，不进入 Git
```

`reports/` 中也有真实源代码和必要输入，不能把整个目录当作可删除的日志。完整工作区与轻量发布预览的内容有所不同，后者按明确清单收录源码和已验证的离线复现资产。

## 结果边界

论文离线主实验使用 176 条冻结输入、22 个布局，其中 132 条标签有定义、44 条未定义；六个策略行共产生 1,056 条结果。受控候选、结构化证据、理想回答和等权反事实意图是这些结果的前提。`Random-Query` 是解析期望，不是一次随机驾驶实测；未定义样本不能当作正确或错误。

历史闭环统计的复现只是重新计算已有记录。当前新增答案绑定接口未完成真实分支资格；历史局部停靠两臂都没有 task-correct safe completion。论文使用的另一台机器上的 220 路线 Bench2Drive 汇总缺少本机可追溯的最终逐路线文件，不能靠现有中途账本补成完整结果。详细边界见[论文交付说明](deliverables/paper_revision_20260915/FINAL_REPORT.md)。

## 开发与发布

```bash
# 静态统计源代码依赖和本机绝对路径，不导入实验模块
python engineering/reproduce.py inventory --output build/inventory.json

# 在新的目录生成源码和离线数据发布预览；不创建远程仓库
python engineering/reproduce.py export --output build/github-preview-001
```

开发约定见 [CONTRIBUTING.md](CONTRIBUTING.md)，GitHub 上传流程见[发布指南](engineering/PUBLISHING.md)。[GitHub Actions](https://github.com/ry535687-ux/DriveClarify/actions) 执行 Python 3.10 / 3.13 的 CPU 测试与离线复现；请以具体运行记录为准。

## 第三方与许可

本项目的原生接口依赖 [SimLingo](https://github.com/RenzKa/simlingo)、[CARLA 0.9.15](https://carla.readthedocs.io/en/0.9.15/start_quickstart/) 及其 Bench2Drive 组件。第三方代码、地图、模型和数据分别遵循原许可。仓库尚未选定自身开源许可证，也未填写论文作者、发表信息或 DOI；许可与作者信息由维护者另行确定；不能据此 README 推定第三方资产的再分发授权。
