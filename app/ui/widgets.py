"""可复用的界面组件。

目前包含折叠分组 ``CollapsibleSection``：把复杂设置收纳进可开合的分组里，
让面板默认只展示最常用的部分。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """带标题栏的折叠分组。

    使用方式::

        section = CollapsibleSection("采样参数", expanded=False)
        section.body.addWidget(widget)
    """

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QToolButton()
        self.header.setProperty("section", "true")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header.clicked.connect(self._on_header_clicked)
        outer.addWidget(self.header)

        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(10, 8, 10, 10)
        self.body.setSpacing(8)
        outer.addWidget(self.content)

        self.set_expanded(expanded)

    # --------------------------------------------------------------- 状态 --
    def _on_header_clicked(self, checked: bool) -> None:
        self.set_expanded(checked)
        self.toggled.emit(checked)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)
        self.header.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.content.setVisible(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_title(self, title: str) -> None:
        self.header.setText(title)