"""Erzeugt icon.ico für die Windows-EXE (wird beim Build aufgerufen)."""

from app_icon import make_icon

make_icon(256).save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
print("icon.ico erstellt")
