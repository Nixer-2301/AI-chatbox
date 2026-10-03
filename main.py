"""AIChat 启动入口。

用法：
    python main.py
    python main.py --data-dir D:\\aichat-data
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.config import ConfigStore
from app.storage import Storage
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme


def parse_args(argv):
    parser = argparse.ArgumentParser(description="AIChat · API 对话工作台")
    parser.add_argument("--data-dir", default="",
                        help="配置与数据库存放目录（默认使用用户配置目录）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    app = QApplication(sys.argv[:1])
    app.setApplicationName("AIChat")
    app.setOrganizationName("AIChat")
    apply_theme(app)

    try:
        config = ConfigStore(Path(args.data_dir) if args.data_dir else None)
        storage = Storage(config.db_path)
    except Exception as exc:
        QMessageBox.critical(None, "启动失败", f"无法初始化数据目录：\n{exc}")
        return 1

    window = MainWindow(config, storage)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())