# DriveClarify

根据候选任务后果、证据完整性和剩余时间，决定执行、等待或询问，并将回答绑定到后续观测与原生导航。

仓库只维护一套实现，统一安装为 `driveclarify` 包。包含核心算法、答案绑定、CARLA / SimLingo 接口、受控评测数据和运行工具。

## 快速开始

需要 Python **3.10+**、Git 和 Bash。离线评测使用标准库，不加载模型或启动 CARLA。

```bash
git clone https://github.com/ry535687-ux/DriveClarify.git
cd DriveClarify
bash reproduce.sh
```

脚本创建独立虚拟环境，安装带 SHA-256 的测试与构建依赖，安装项目，运行测试并重算 176 条输入、六种策略共 1,056 条预测。预测文件和逐条评分与固定参考文件逐字节比较；输出写入新的 `build/` 子目录。重复运行会创建新目录。

手动安装与运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements/cpu.lock.txt
python -m pip install --no-build-isolation --no-deps -e .
python -m pytest
driveclarify evaluate --output build/evaluation-001
```

输出包含 `predictions.jsonl`、`prediction-lock.json`、`scores.csv`、`summary.csv` 和 `receipt.json`。先锁定预测，再读取标签；44 条任务标签未定义的输入不计正确率。`Random-Query` 是概率为 1/3 的解析期望。

## 完整驾驶复现

主机要求：Linux x86_64（建议 Ubuntu 22.04）、Python 3.10+、Git、Bash、已安装的 NVIDIA 535 或更新驱动。建议至少 16 GB 显存、32 GB 内存；首次准备须有 **100 GiB 空闲空间**。Ubuntu 的基础工具可安装为：

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv bzip2 libvulkan1 libx11-6 libglib2.0-0
```

克隆本仓库后，只需运行：

```bash
bash reproduce.sh --native
```

入口自动安装独立 Python 3.8.18 环境与固定依赖；没有 Conda 时自动安装项目内的 Miniforge；还原固定版本后端并应用本仓库补丁；下载、解压 CARLA 0.9.15 与全部路线所需的地图；下载模型、合并权重分片并校验 SHA-256；恢复 220 条路线及种子；最后启动一条路线的 A0/A1 配对，自动收集首次合法结果并生成两份 `merged.json`。不需要另行克隆 SimLingo 或 Bench2Drive，也不需要按它们的教程配置环境。

首次需要联网下载大文件，耗时取决于带宽；下载中断后重新执行同一命令即可续传。安装文件、模型与结果统一放在 `build/native/`，不进入 Git。CARLA 归档固定 HTTPS 对象、ETag 和大小，解压检查 gzip 完整性并记录本地 SHA-256；模型使用预先固定的 SHA-256。

```bash
# 只准备环境、地图和模型，不启动驾驶
bash reproduce.sh --native --setup-only

# 准备后检查完整 440 次运行命令，不启动驾驶
bash reproduce.sh --native --all --dry-run --output build/native/full-check

# 正式运行 220 路线 × A0/A1，并自动收集、合并结果
bash reproduce.sh --native --all --output build/native/full

# 中断后续跑同一计划；已获得的合法结果保留
bash reproduce.sh --native --resume --output build/native/full
```

结果位于计划目录的 `summary-*/`，包含逐路线 CSV、结果来源记录和 `official_merge/A0/merged.json`、`official_merge/A1/merged.json`。少于 220 路线的合并仅用于启动检查，不作为完整基准成绩。已有模型或 CARLA 的复用方法、输出说明及故障处理见 [运行指南](docs/carla.md)，所有操作均通过本仓库入口完成。

基准 A0/A1 使用同一适配权重，A1 在澄清上下文为空时透明旁路到原生后端。它不证明真实答案绑定场景的任务收益。已验证自动准备工具、固定源代码/地图清单/模型校验和完整运行命令；本次未在空白 GPU 主机安装全部大文件并完成真实驾驶。最终 220 路线结果由运行者生成。

## 目录

```text
src/driveclarify/  核心算法、执行接口、命令工具与必要运行资源
configs/          本机路径配置示例
scripts/          安装兼容入口
requirements/     CPU 工具依赖锁；原生清单随安装包发布
tests/            当前实现的行为和回归测试
docs/             运行与接口说明
build/            本机环境、模型和新输出（不进入 Git）
```

`src/driveclarify/resources/` 收录必要输入、参考输出、模型清单、原生补丁与一份含 220 路线的配置；启动时还原实际 XML。接口说明见 [architecture.md](docs/architecture.md)。

## 容器与开发

```bash
docker build --network=host -f Dockerfile -t driveclarify .
mkdir -p build
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/build:/workspace/DriveClarify/build" driveclarify
```

容器运行 CPU 测试和受控评测。需要网络代理的主机可为构建添加 `--build-arg HTTP_PROXY --build-arg HTTPS_PROXY`，继承终端的代理配置。

项目支持构建 wheel，安装包包含运行资源。修改决策逻辑时检查输入合同、未知证据、预测与标签隔离、答案生效帧及原生控制所有权；行为改变应使用新的参考数据说明原因。GitHub Actions 检查 Python 3.10 / 3.13 下的安装、测试及离线评测。

## 致谢

本项目集成 SimLingo、CARLA 和 Bench2Drive。安装入口自动准备其固定版本；下载内容保留原始许可与出处，相关说明见 [第三方来源](docs/third-party.md)。
