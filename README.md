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

## 原生驾驶

按 [CARLA 安装与运行指南](docs/carla.md)准备 Linux x86_64、NVIDIA GPU、独立 Python 3.8 环境、CARLA 0.9.15 与地图，然后运行：

```bash
driveclarify prepare --workspace build/native-workspace \
  --carla-root "$PWD/build/carla-install/CARLA_0.9.15" \
  --native-python "$PWD/build/native-env/bin/python"
driveclarify assets --output build/native-workspace
driveclarify benchmark plan --paths build/native-workspace/paths.local.json \
  --output build/smoke --offscreen
driveclarify benchmark run --plan build/smoke --dry-run
driveclarify benchmark run --plan build/smoke
```

最后一条命令加载模型并启动 CARLA。确认单路线环境后，用 `plan --all` 生成 220 路线 / A0、A1 共 440 次运行；用 `run --resume` 续跑、`collect` 收集首次合法结果。模型固定到公开上游版本和[适配权重 Release](https://github.com/ry535687-ux/DriveClarify/releases/tag/v0.1.0-assets)，下载支持续传、分片合并和完整 SHA-256 校验。

基准 A0/A1 使用同一适配权重，A1 在澄清上下文为空时透明旁路到原生 SimLingo。它不证明真实答案绑定场景的任务收益。新 GPU 主机的原生环境安装及真实驾驶尚未在本轮验证；最终 220 路线结果由运行者生成。

## 目录

```text
src/driveclarify/  核心算法、执行接口、命令工具与必要运行资源
configs/          本机路径配置示例
scripts/          原生环境安装
requirements/     CPU 工具锁、原生依赖和 Conda 清单
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

## 第三方

原生后端依赖 [SimLingo](https://github.com/RenzKa/simlingo)、[CARLA](https://carla.org/) 和 Bench2Drive；代码、地图、模型分别遵循其原许可。本项目自身尚未选定开源许可证。

