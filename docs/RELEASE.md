# 发行版打包说明

本文只说明如何在本地生成 ChemSSH 发行包。

## 前提条件

- Git
- Python 3.10+
- Node.js（仅打包者需要，用于构建前端）
- Windows 用户推荐使用 Git Bash（随 Git for Windows 安装）

## 更新版本号

打包脚本会自动读取 `backend/app/__init__.py` 中的版本号。打包前如需变更版本，只修改这里：

```python
__version__ = "0.3.5"
```

## 执行打包

在仓库根目录运行：

```bash
chmod +x create-release-archive.sh
./create-release-archive.sh
```

Windows 用户也推荐在 Git Bash 中运行同一个脚本。

## 打包产物

脚本会在 `release/` 目录下生成以当前版本号命名的发行目录和压缩包，例如：

```text
release/
├── chemssh-<version>/
│   ├── backend/
│   ├── frontend/
│   │   └── dist/
│   ├── plugins/
│   ├── docs/
│   ├── README.md
│   ├── README.zh-CN.md
│   ├── config.yaml
│   └── pyproject.toml
├── chemssh-<version>.tar.gz
├── chemssh-<version>.tar.gz.sha256
├── chemssh-<version>.zip
└── chemssh-<version>.zip.sha256
```

发行包要点：

- 包含已构建的 `frontend/dist/`
- 不包含 `frontend/src/`
- 不包含 `frontend/node_modules/`
- 使用发行包的用户不需要安装 Node.js

## 打包前检查

- [ ] `backend/app/__init__.py` 中的版本号正确
- [ ] 前端可以正常构建
- [ ] 后端测试通过
- [ ] README 或其他随包文档已更新
- [ ] `create-release-archive.sh` 执行成功
- [ ] `.tar.gz`、`.zip` 及对应 `.sha256` 文件已生成

## 本地验证发行包

打包后可在 `release/chemssh-<version>/` 中做一次最小验证：

```bash
cd release/chemssh-<version>
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
chemssh --config config.yaml
```

Windows PowerShell 激活虚拟环境时使用：

```powershell
.\.venv\Scripts\Activate.ps1
```
