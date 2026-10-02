# DriveClarify Git 跟踪范围

状态：包含 2026-10-02 本地工程化增补；下面保留 2026-07-22 历史边界。

## 当前工程化范围（2026-10-02）

用户当前要求按完整实验复现工程化，并上传到公开 GitHub 仓库。新增根 README、贡献说明、依赖文件、
测试配置、CPU CI 与 `engineering/`。`engineering/reproduce.py export` 通过
显式模式与资产清单生成本地发布预览，收录当前实验源码、必要离线数据及
原生协议；不默认复制全部报告、原始运行、模型、私人文档或本机配置。

外部 SimLingo 仓库仍只读。当前本地修改的补丁和四个附加源文件保存于
`engineering/vendor/`，供新的独立克隆还原；没有修改外部原工作区。

源码已通过 GitHub 插件发布到 [ry535687-ux/DriveClarify](https://github.com/ry535687-ux/DriveClarify)。原研究目录的 Git 历史未改写；大模型和归档已公开发布到 `v0.1.0-assets`，并验证服务器摘要与归档下载。完整 CARLA 新运行仍有已披露的资格与数据缺口。
具体清单见 `engineering/paper_assets.json`、`engineering/native_assets.json`
及导出时生成的 `RELEASE_MANIFEST.json`。发布和维护操作见
`engineering/PUBLISHING.md`；仍禁止在研究工作区直接 `git add .`。

## 1. 仓库边界

本 Git 仓库只管理 `/home/buaa/wrh/DriveClarify` 自身的设计合同、配置和后续 CPU-only offline evaluator。外部同级仓库 `/home/buaa/wrh/simlingo` 不属于本仓库，不得嵌套、复制或修改。

## 2. 修订前基线提交范围

- `.gitignore`
- `GIT_TRACKING_MANIFEST.md`
- `design/v0/**`

## 3. 本次设计修订新增跟踪范围

- `configs/offline_v0_synthetic.yaml`
- `design/v0/DESIGN_DELTA_DUAL_RATE.md`
- `design/v0/CANDIDATE_CACHE_SCHEMA.json`
- `design/v0/DESIGN_REPAIR_REPORT.md`
- 任务书允许修订的现有 `design/v0` 合同文件

## 4. 明确不跟踪

- `runtime/`：历史 CARLA/SimLingo/OpenArm 运行证据、日志、缓存和产物；
- `workspace/`：包含独立嵌套仓库和大型 USD/视频；
- `DriveClarify_host_upload_pack/`：只读上传研究材料；
- `docker/`、`docs/`、`patches/`、`scripts/` 及顶层旧 OpenArm 打包文件；
- checkpoint、模型权重、数据数组、归档、视频和生成测试产物。

禁止使用无范围限制的 `git add .`。提交前必须用 `git status --short` 和 `git diff --cached --stat` 核对实际暂存范围。
