import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QFont

from app.ui.main_window import MainWindow

_DEFAULT_UI_FONT_PT = 13
_MIN_UI_FONT_PT = 10
_MAX_UI_FONT_PT = 22


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    saved = int(QSettings("PriceAction", "Trainer").value("ui/font_pt", _DEFAULT_UI_FONT_PT))
    pt = max(_MIN_UI_FONT_PT, min(_MAX_UI_FONT_PT, saved))
    font = app.font()
    font.setPointSize(pt)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
