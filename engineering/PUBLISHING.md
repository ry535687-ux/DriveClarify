# GitHub 发布与维护

公开仓库为 [ry535687-ux/DriveClarify](https://github.com/ry535687-ux/DriveClarify)，源码通过已连接的 GitHub 插件提交到 `main`。模型和归档的独立资产版本为 `v0.1.0-assets`；7 个大文件/清单附件已公开发布，GitHub 返回的大小与 SHA-256 全部核验通过。完整发布状态同时见 README。

## 可审阅的源码清单

原研究工作区有大量历史文件；发布以明确清单为准。不要直接在原目录执行 `git add .`。

```bash
python engineering/reproduce.py export --output build/github-preview-002
python engineering/reproduce.py --root build/github-preview-002 test
python engineering/reproduce.py --root build/github-preview-002 paper --output build/release-check-002
```

`export` 收录版本化 Python 源码、工具、配置、CPU 测试、复现文档、上游修改及冻结论文输入，不复制原工作区 `.git`、本机配置、账户资料或大权重。`RELEASE_MANIFEST.json` 记录每个发布文件的字节数和 SHA-256。

提交工具必须用原始字节构造 Git blob。CSV 中的 CRLF 是冻结身份的一部分；用 Python `read_text()` 默认读入后再上传会改变它。仓库 `.gitattributes` 禁止 Git 换行转换。发布后从 GitHub 重新下载，再核对整个清单并执行 `reproduce.sh`，不能仅在原工作区验证。

## 大文件版本

`engineering/release_assets.json` 记录所有下载 URL、大小、摘要和分片关系。基础模型使用固定 Hugging Face 提交；本项目适配模型、现有记录和论文附件使用 Releases。

首版资产已通过 GitHub CLI 发布。维护新版本时可参考下面命令；已存在的公开标签不要重复创建或覆盖。这里的 `build/release-assets/` 是本机打包目录，不进入 Git。

```bash
gh release create v0.1.0-assets --repo ry535687-ux/DriveClarify \
  --target main --title "DriveClarify reproducibility assets v0.1.0" \
  --notes-file build/release-notes.md

gh release upload v0.1.0-assets build/release-assets/* \
  --repo ry535687-ux/DriveClarify
```

适配权重拆为两个小于 2 GiB 的资产，下载器会检查各片后合并并验证完整权重。现有实验记录、训练来源和论文附件分别归档；SHA256SUMS 便于手工核对。失败后可重试尚未上传的资产；不要替换已经发布的同名字节，新版本应使用新标签并更新清单。

## 检查实际结果

[CPU Actions](https://github.com/ry535687-ux/DriveClarify/actions) 执行 Python 3.10 / 3.13 的合同测试，并在 Python 3.13 进行论文重算。只有具体运行通过后才报告远程 CI 成功。Release 上传后还需实际访问公开资产并验证下载，不能将本地打包成功写成远程发布成功。

许可证、作者与论文信息由维护者确定；当前未自动添加 MIT / Apache 或推定第三方资产的再分发许可。原生闭环资格和外部最终逐路线记录的缺口在复现文档保留，不能通过改动历史证据消除。
