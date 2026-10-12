
import os
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from app.main_window import MainWindow


_VLC_DLL_DIRECTORY = None


def configure_vlc():
    global _VLC_DLL_DIRECTORY

    candidates = [
        Path(r"C:\Program Files\VideoLAN\VLC"),
        Path(r"C:\Program Files (x86)\VideoLAN\VLC"),
    ]

    for folder in candidates:
        if (folder / "libvlc.dll").exists():
            _VLC_DLL_DIRECTORY = os.add_dll_directory(str(folder))
            os.environ["VLC_PLUGIN_PATH"] = str(folder / "plugins")
            return


def main():
    configure_vlc()

    app = QApplication(sys.argv)
    app.setApplicationName("Antony Video Editor")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
