# Linux 离线 Runtime 打包步骤

本文档用于制作一个离线可运行目录：`chemssh` 可执行文件由 PyInstaller 生成，业务代码、前端资源、插件和配置仍按当前源码树的明文目录放在旁边。后续普通版本更新时，用 `create-release-archive.sh` 生成的内容覆盖安装目录中的明文文件即可。

## 1. 准备 Linux 构建机

准备一台和离线服务器一致或更老的 Linux 构建机，例如同架构 `x86_64`、同发行版优先。不要在 Windows 上交叉打 Linux 包。

## 2. 创建打包工作目录

所有临时文件和产物统一放在 `packup` 下，避免干扰项目：

```bash
mkdir -p packup
```

## 3. 确认前端资源已构建

确认已有构建好的前端资源：

```text
frontend/dist/index.html
```

如果不存在，先在仓库根目录构建前端：

```bash
(
  cd frontend
  npm install
  npm run build
)
```

## 4. 激活 Python 环境并安装 PyInstaller

复用项目 `.venv`，仅补装 PyInstaller：

```bash
source .venv/bin/activate
python -m pip install pyinstaller
```

## 5. 打包 Runtime

使用 `packaging/chemssh_external_backend_launcher.py` 作为 PyInstaller 入口：

```bash
pyinstaller \
  --name chemssh \
  --onedir \
  --console \
  --workpath packup/build \
  --distpath packup/dist \
  --add-data "frontend/dist:frontend/dist" \
  --add-data "plugins:plugins" \
  --collect-all uvicorn \
  --collect-all fastapi \
  --collect-all pydantic \
  --collect-all ptyprocess \
  --collect-all ase \
  packaging/chemssh_external_backend_launcher.py
```

## 6. 放入明文运行文件

把当前源码树里的明文文件放到 PyInstaller 产物目录旁边：

```bash
cp -r backend packup/dist/chemssh/
mkdir -p packup/dist/chemssh/frontend
cp -r frontend/dist packup/dist/chemssh/frontend/
cp -r plugins packup/dist/chemssh/
cp config.yaml packup/dist/chemssh/
cp README.md README.zh-CN.md packup/dist/chemssh/
```

目录结构类似：

```text
packup/dist/chemssh/
  chemssh
  _internal/
  backend/
  frontend/dist/
  plugins/
  config.yaml
  README.md
  README.zh-CN.md
```

## 7. 本地测试

```bash
./packup/dist/chemssh/chemssh \
  --config packup/dist/chemssh/config.yaml \
  --host 0.0.0.0 \
  --port 5678 \
  --workspace-root .
```

## 8. 打包成离线安装包

```bash
cd packup/dist
tar -czf chemssh-linux.tar.gz chemssh
sha256sum chemssh-linux.tar.gz > chemssh-linux.tar.gz.sha256
```

## 9. 离线服务器安装

```bash
tar -xzf chemssh-linux.tar.gz
cd chemssh
./chemssh --config config.yaml --host 0.0.0.0 --port 5678 --workspace-root .
```

## 10. 普通版本更新

如果只是业务代码、前端资源、插件、README 或配置模板变更，在新版本源码目录运行：

```bash
./create-release-archive.sh
```

把生成的明文包解压后覆盖到已安装的 `chemssh/` 目录即可：

```bash
tar -xzf chemssh-<version>.tar.gz -C /tmp
cp -r /tmp/chemssh-<version>/. /path/to/chemssh/
```

然后重启服务：

```bash
cd /path/to/chemssh
./chemssh --config config.yaml --host 0.0.0.0 --port 5678 --workspace-root .
```

如果 Python 依赖、PyInstaller 参数或 `packaging/chemssh_external_backend_launcher.py` 发生变化，再重新执行第 5 步之后的完整打包流程。

## 11. onefile 说明

如果必须做单文件版，把第 5 步改成：

```bash
pyinstaller \
  --name chemssh \
  --onefile \
  --console \
  --workpath packup/build \
  --distpath packup/dist \
  --add-data "frontend/dist:frontend/dist" \
  --add-data "plugins:plugins" \
  --collect-all uvicorn \
  --collect-all fastapi \
  --collect-all pydantic \
  --collect-all ptyprocess \
  --collect-all ase \
  packaging/chemssh_external_backend_launcher.py
```

离线服务器部署更推荐 `onedir`，排查依赖、插件和静态资源问题会更直接。
