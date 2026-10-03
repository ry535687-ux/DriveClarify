# 第三方来源

运行入口负责固定版本下载、配置和校验。以下链接用于说明来源与许可，不是读者需要逐个执行的安装教程。

| 内容 | 来源与身份 |
| --- | --- |
| 原生模型与驾驶后端 | [SimLingo](https://github.com/RenzKa/simlingo)，提交 `743b243afd6cf5ff51b9fa1f8cac86f22d569684`，本项目补丁保存在安装包内 |
| 仿真、Python 客户端、地图 | [CARLA](https://github.com/carla-simulator/carla/releases/tag/0.9.15)，0.9.15；运行时下载对象记录在 `resources/runtime.json` |
| 基准评价与路线 | SimLingo 中的固定 Bench2Drive 文件；本项目保存 220 路线 XML 与种子 |
| 基础模型 | [RenzKa/simlingo](https://huggingface.co/RenzKa/simlingo) 与 [OpenGVLab/InternVL2-1B](https://huggingface.co/OpenGVLab/InternVL2-1B)，版本和每个文件的摘要保存在 `resources/assets.json` |
| 适配模型 | 本仓库 [模型 Release](https://github.com/ry535687-ux/DriveClarify/releases/tag/v0.1.0-assets)，分片及完整权重均按固定 SHA-256 校验 |
| 环境引导 | [Miniforge 24.3.0-0](https://github.com/conda-forge/miniforge/releases/tag/24.3.0-0)，安装器 SHA-256 与 Conda 显式清单随包保存 |

还原源代码与解压运行时保留原有许可文件；代码、地图和模型按各自许可使用。项目未新增覆盖第三方内容的统一许可证。
