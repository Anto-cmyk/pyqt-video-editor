
APP_STYLE = """
QMainWindow, QWidget {
    background-color: #202127;
    color: #eeeeF3;
    font-family: "Segoe UI";
    font-size: 10pt;
}
QMenuBar {
    background: #292b32;
    color: #eeeeF3;
    padding: 4px;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #444957;
}
QMenu {
    background: #292b32;
    color: #eeeeF3;
    border: 1px solid #454956;
}
QMenu::item {
    padding: 6px 24px;
}
QPushButton {
    background: #353843;
    color: #f5f5f7;
    border: 1px solid #4b5060;
    border-radius: 5px;
    padding: 7px 10px;
}
QPushButton:hover {
    background: #464c5d;
}
QPushButton:pressed {
    background: #535b70;
}
QSlider::groove:horizontal {
    background: #444854;
    height: 5px;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #ff5252;
    width: 13px;
    margin: -5px 0;
    border-radius: 6px;
}
QLabel {
    background: transparent;
}
QStatusBar {
    background: #292b32;
    color: #d5d7de;
}
QFileDialog, QMessageBox {
    background: #202127;
}
"""
