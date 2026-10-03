"""一键构建 Windows 免安装版。

流程：
    1. 清理旧的 build/、dist/
    2. 调用 PyInstaller 生成 dist/AIChat/（目录版，启动更快）
    3. 打包成 dist/AIChat-windows-x64.zip，可直接上传 GitHub Release

用法：
    python build.py            # 完整构建 + 打包
    python build.py --no-zip   # 只构建，不压缩
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "AIChat"

# 体积大、本项目用不到的可选模块，排除后可显著减小发布包
EXCLUDES = (
    "tkinter",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQml",
    "PyQt6.QtQuick",
    "PyQt6.QtQuick3D",
    "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets",
    "PyQt6.Qt3DCore",
    "PyQt6.QtBluetooth",
    "PyQt6.QtDesigner",
    "PyQt6.QtPdf",
    "PyQt6.QtPdfWidgets",
    "PyQt6.QtNetworkAuth",
    "PyQt6.QtSensors",
    "PyQt6.QtSerialPort",
    "PyQt6.QtPositioning",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
)


def clean() -> None:
    for path in (BUILD, DIST):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def build() -> Path:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
    ]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(ROOT / "main.py"))

    print("运行:", " ".join(args))
    subprocess.check_call(args, cwd=str(ROOT))

    target = DIST / APP_NAME
    if not (target / f"{APP_NAME}.exe").exists():
        raise SystemExit("构建失败：未生成可执行文件")
    return target


def make_zip(target: Path) -> Path:
    archive = DIST / f"{APP_NAME}-windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target.rglob("*")):
            zf.write(path, path.relative_to(target.parent))
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 AIChat 发布包")
    parser.add_argument("--no-zip", action="store_true", help="只构建，不压缩成 zip")
    args = parser.parse_args()

    clean()
    target = build()
    print(f"\n可执行文件：{target / (APP_NAME + '.exe')}")

    if not args.no_zip:
        archive = make_zip(target)
        size = archive.stat().st_size / 1024 / 1024
        print(f"发布压缩包：{archive}（{size:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())