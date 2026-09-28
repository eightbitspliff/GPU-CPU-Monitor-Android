"""Erzeugt icon.ico für die Windows-EXE (wird beim Build aufgerufen)."""
import sys
import types

# Nur die Zeichenfunktion wird gebraucht – GUI-Module nicht laden.
for name in ("tkinter", "tkinter.messagebox", "pystray"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["tkinter"].messagebox = None

import tray_gui  # noqa: E402

tray_gui.make_icon(256).save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
print("icon.ico erstellt")
