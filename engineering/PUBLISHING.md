# GitHub 发布流程

## 本地准备

当前研究工作区有大量未跟踪实验和本机资料。建议从明确清单生成一个新的发布目录，并在那里创建 Git 仓库；这样可以审阅完整首版内容，且不会把早期 Git 历史误认为已包含全部当前代码。

```bash
python engineering/reproduce.py export --output build/github-preview-001
python engineering/reproduce.py --root build/github-preview-001 test
python engineering/reproduce.py --root build/github-preview-001 paper --output build/release-check-001
```

`export` 收录版本化 Python 源码、工具、配置、选定 CPU 测试、复现文档、上游修改及冻结论文分析输入。它不复制 `.git`、本机配置、个人材料、大权重或所有历史运行数据。`RELEASE_MANIFEST.json` 记录实际路径、字节数与 SHA-256；它是提交预览，不等同于完整 CARLA 数据集已发布。

GitHub 首版建议把以下内容作为完成条件：README 的命令在新目录通过；资产下载位置和缺失状态可查；上游修改可还原；选择本项目许可证并核对第三方再分发范围；维护者决定仓库归属和公开范围。无需为了排版重新运行已冻结实验。

## 建立本地提交

先检查发布目录与 `RELEASE_MANIFEST.json`，然后在该目录执行。这里的命令尚未由本轮自动执行。

```bash
cd build/github-preview-001
git init -b main
python - <<'PY'
import json
import subprocess
from pathlib import Path
manifest = json.loads(Path('RELEASE_MANIFEST.json').read_text())
paths = [r['path'] for r in manifest['files']] + ['RELEASE_MANIFEST.json']
# 只提交清单中的文件。-f 用于收录被旧工作区忽略规则覆盖的历史入口。
for start in range(0, len(paths), 100):
    subprocess.run(['git', 'add', '-f', '--', *paths[start:start + 100]], check=True)
PY
git diff --cached --stat
git status --short
git commit -m "工程化：加入实验源码、复现入口与文档"
```

作者、邮箱使用自己的 Git 配置，不要抄用其他人的身份。本机尚未选定自身许可证，故没有自动添加 MIT / Apache 或伪造作者与论文引用。

## 上传

在 GitHub 创建属于自己的空仓库，选择需要的公开范围。复制 GitHub 显示的远程地址，在上面的发布目录中执行：

```bash
git remote add origin https://github.com/<OWNER>/DriveClarify.git
git push -u origin main
```

将 `<OWNER>` 替换为真实用户或组织。使用已配置的 GitHub 登录方式；不要将 token 写入远程 URL 或 README。上传后检查 CPU Actions 的实际运行结果，不提前把远程检查标为通过。

权重、原始数据、视频和地图应使用适当的独立数据存储，并补充下载地址、版本、许可与摘要。README 里将“下载已有实验资产”和“重新运行原生实验”分别说明；新用户必须能知道拿到的是哪一层数据。

CI 写法参考 [GitHub 的 Python 测试文档](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)。本轮只整理本地，没有新建 GitHub 仓库、提交或推送。
