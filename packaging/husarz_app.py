"""Skrypt startowy dla PyInstaller (cel binarki launchera).

PyInstaller pakuje ten plik jako punkt wejścia; deleguje do launchera desktopowego.
"""

from __future__ import annotations

import sys

from husarz.launcher.app import main

if __name__ == "__main__":
    sys.exit(main())
