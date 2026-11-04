from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QLabel

_COLORS = {True: "#2ecc71", False: "#e74c3c", None: "#95a5a6"}


class Led(QLabel):
    """on=True 绿 / False 红 / None 灰；color 可覆盖。"""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self.set_state(None)

    def set_state(self, on: Optional[bool], text: Optional[str] = None, color: Optional[str] = None) -> None:
        if text is not None:
            self._text = text
        self.setText(f"● {self._text}")
        self.setStyleSheet(f"color:{color or _COLORS[on]}; font-weight:bold; padding:2px 8px;")
