"""Autostart mit Windows, startet direkt im Systemtray.

Die EXE fordert Adminrechte an (Sensoren/FPS). Windows überspringt solche
Programme im normalen Autostart (Run-Schlüssel) stillschweigend. Deshalb
wird eine Aufgabe in der Aufgabenplanung angelegt: bei Anmeldung, mit
höchsten Rechten, ohne UAC-Abfrage, ohne Laufzeitlimit.

Der Eintrag zeigt immer auf den aktuellen Speicherort der EXE und wird bei
jedem Start korrigiert, falls sie verschoben wurde. Abschaltbar über das
Tray-Menü; die Wahl wird in der Konfigurationsdatei gespeichert.
"""

import os
import subprocess
import sys
import tempfile

TASK_NAME = "PCMonitorServer"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LOGON_DELAY = "PT15S"  # WLAN/Netz Zeit geben, bevor der Nest Hub gesucht wird

try:
    import winreg
    AVAILABLE = bool(getattr(sys, "frozen", False))
except ImportError:  # kein Windows
    winreg = None
    AVAILABLE = False

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True,
                          creationflags=_NO_WINDOW if os.name == "nt" else 0)


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _user():
    dom = os.environ.get("USERDOMAIN", "")
    name = os.environ.get("USERNAME", "")
    return f"{dom}\\{name}" if dom else name


def _task_xml():
    exe = _xml_escape(sys.executable)
    user = _xml_escape(_user())
    workdir = _xml_escape(os.path.dirname(sys.executable))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>PC Monitor Server im Systemtray starten</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>{LOGON_DELAY}</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>"{exe}"</Command>
      <Arguments>--tray</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _remove_run_key():
    """Alter Run-Eintrag (greift bei Admin-EXEs nicht) wird entfernt."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, TASK_NAME)
    except OSError:
        pass


def is_enabled():
    if not AVAILABLE:
        return False
    r = _schtasks("/Query", "/TN", TASK_NAME, "/XML")
    if r.returncode != 0:
        return False
    xml = r.stdout.decode("utf-16", "ignore") if r.stdout[:2] in (b"\xff\xfe", b"\xfe\xff") \
        else r.stdout.decode("utf-8", "ignore")
    return sys.executable.lower() in xml.lower() and "--tray" in xml


def set_enabled(on):
    if not AVAILABLE:
        return False
    _remove_run_key()
    if not on:
        _schtasks("/Delete", "/TN", TASK_NAME, "/F")
        return True
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(_task_xml())
        return _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F").returncode == 0
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def apply_from_config(cfg):
    """Standard: an. Nur wenn der Nutzer es im Tray abgeschaltet hat, aus."""
    if cfg.get("autostart", True):
        if not is_enabled():
            set_enabled(True)
    elif is_enabled():
        set_enabled(False)
