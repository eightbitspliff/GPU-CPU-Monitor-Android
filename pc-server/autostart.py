"""Autostart mit Windows (HKCU\\...\\Run), startet direkt im Systemtray.

Nur für die gebaute EXE aktiv: der Eintrag zeigt immer auf den aktuellen
Speicherort der EXE, wird also bei jedem Start korrigiert, falls sie
verschoben wurde. Abschaltbar über das Tray-Menü; die Wahl wird in der
Konfigurationsdatei gespeichert und dann respektiert.
"""

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "PCMonitorServer"

try:
    import winreg
    AVAILABLE = getattr(sys, "frozen", False)
except ImportError:  # kein Windows
    winreg = None
    AVAILABLE = False


def command():
    return f'"{sys.executable}" --tray'


def is_enabled():
    if not AVAILABLE:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, VALUE_NAME)
        return value == command()
    except OSError:
        return False


def set_enabled(on):
    if not AVAILABLE:
        return False
    try:
        # CreateKeyEx statt OpenKey: legt den Run-Schlüssel an, falls er (z.B. auf einem
        # frisch eingerichteten Windows) noch nicht existiert
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(k, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def apply_from_config(cfg):
    """Standard: an. Nur wenn der Nutzer es im Tray abgeschaltet hat, aus."""
    if cfg.get("autostart", True):
        if not is_enabled():
            set_enabled(True)
    elif is_enabled():
        set_enabled(False)
