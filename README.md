# PC Monitor – CPU/GPU-Auslastung des PCs auf dem Android-Handy

Zwei Teile:

| Teil | Ordner | Läuft auf |
|---|---|---|
| **PC Monitor Server** – misst CPU, GPU, RAM, VRAM, Temperaturen | `pc-server/` | Windows-PC (auch Linux/macOS) |
| **PC Monitor App** – zeigt die Werte live an | `android-app/` | Android 8.0+ |

Funktioniert nur im eigenen Netzwerk (WLAN/LAN), kein Fernzugriff, kein Konto.

## Installation

1. Unter **Releases** (bzw. *Actions → Build PC Monitor → Artifacts*) herunterladen:
   - `PCMonitorServer.exe` → auf den PC
   - `PCMonitor.apk` → aufs Handy (Installation aus unbekannten Quellen erlauben)
2. `PCMonitorServer.exe` auf dem PC starten. Beim ersten Start fragt die Windows-Firewall –
   **„Private Netzwerke“ zulassen**. Das Fenster zeigt die IP-Adresse des PCs an.
3. App öffnen → sie sucht den PC automatisch. Klappt das nicht: oben auf den Titel tippen
   und die IP aus dem Server-Fenster eintragen.

Der Server hat ein kleines Fenster mit IP-Adresse und Live-Werten. **Minimieren legt ihn in den
Systemtray** (Icon unten rechts neben der Uhr, Tooltip zeigt CPU/GPU). Doppelklick aufs Icon holt das
Fenster zurück. **Schließen (X)** oder Rechtsklick → „Beenden“ beendet den Server komplett –
inklusive aller Hintergrundprozesse; eine laufende Nest-Hub-Anzeige wird dabei geschlossen.

**Autostart:** Die EXE trägt sich beim ersten Start selbst in den Windows-Autostart ein
(`HKCU\...\Run`, mit `--tray`) – nach jedem Windows-Start läuft sie unsichtbar im Tray und
verbindet sich automatisch wieder mit dem zuletzt gewählten Nest Hub. Wird die EXE verschoben,
korrigiert sie den Eintrag beim nächsten Start. Abschalten: Rechtsklick aufs Tray-Icon →
„Mit Windows starten“.

## App-Funktionen

- Uhrzeit oben links
- Große Anzeigen für **CPU** und **GPU** (Auslastung in %), darunter untereinander:
  **Power** (Leistungsaufnahme), **Temp** (Temperatur) und **RAM** (CPU) bzw. **VRAM** (GPU)
- Zwei Verlaufsdiagramme: oben die **letzten 15 Minuten**, unten die **letzten 5 Minuten**.
  Der Server schreibt den Verlauf mit – die Diagramme sind also sofort gefüllt, auch wenn
  die App vorher geschlossen war
- Bildschirm bleibt an (z.B. als Zweitdisplay neben dem PC), abschaltbar im Menü ⋮
- Optional **dauerhafte Anzeige in der Benachrichtigungsleiste** (Menü ⋮ → „Anzeige in Benachrichtigung“),
  läuft auch weiter, wenn die App geschlossen ist
- Hoch- und Querformat

## Anzeige auf dem Google Nest Hub (oder Chromecast)

Der PC-Server kann die gleiche Ansicht wie die App direkt auf einen Nest Hub streamen:

- **Am PC:** im Server-Fenster unter „Auf Nest Hub / Chromecast anzeigen“ das Gerät wählen → **Anzeigen**
- **oder am Handy:** Menü ⋮ → **„Auf Nest Hub anzeigen…“** → Gerät antippen

Der Server merkt sich das Gerät und startet die Anzeige automatisch wieder – nach einem
Neustart des PCs, oder wenn der Hub sie nach einer Weile beendet. Nutzt jemand den Hub
gerade für etwas anderes (Musik, Wetter, Timer …), wird das nicht unterbrochen; die Anzeige
kommt zurück, sobald der Hub wieder frei ist. **Stoppen** beendet das dauerhaft.

Das Dashboard gibt es auch im Browser: `http://<pc-ip>:47811/`

Technik: Gestreamt wird über die Cast-App „DashCast“, die eine Webseite auf dem Gerät öffnet.
Der Nest Hub lädt die Seite direkt vom PC; das Handy muss dafür nicht an sein.

## CPU-Leistungsaufnahme und -Temperatur

| Quelle | Werte | Hinweis |
|---|---|---|
| [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (läuft im Hintergrund) | Leistung, Temperatur | beste Werte unter Windows, auch für AMD |
| Windows-Leistungsindikator „Energy Meter“ (RAPL) | Leistung | ohne Zusatzprogramm, nicht auf jedem PC vorhanden |
| Linux: `/sys/class/powercap`, Sensoren | Leistung, Temperatur | Leistung ggf. nur mit root |

Windows selbst stellt CPU-Temperatur und (bei den meisten CPUs) CPU-Leistung nicht bereit –
dafür braucht es einen Hardware-Treiber, wie ihn LibreHardwareMonitor mitbringt. Zeigt die
CPU „–“ an:

1. [LibreHardwareMonitor herunterladen](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest), entpacken, `LibreHardwareMonitor.exe` starten (Adminrechte bestätigen)
2. In LibreHardwareMonitor unter **Options** „Start Minimized“, „Minimize To Tray“ und
   „Run On Windows Startup“ anhaken
3. Fertig – der Server liest die Werte automatisch mit (WMI oder, falls unter Options →
   „Remote Web Server“ aktiviert, über Port 8085). Im Server-Fenster steht, welche Quelle genutzt wird.

## GPU-Unterstützung

| GPU | Quelle | Werte |
|---|---|---|
| NVIDIA | NVML / `nvidia-smi` | Auslastung, VRAM, Temperatur, Leistung |
| AMD / Intel unter Windows | Windows-Leistungsindikatoren (wie Task-Manager) | Auslastung, VRAM |
| AMD unter Linux | sysfs | Auslastung, VRAM, Temperatur |

## Server ohne EXE (mit Python)

```
cd pc-server
pip install -r requirements.txt
python pc_monitor_server.py
```

## Technik

- HTTP `GET http://<pc-ip>:47811/stats` liefert JSON
- HTTP `GET http://<pc-ip>:47811/history` liefert den CPU/GPU-Verlauf der letzten 15 Minuten
- UDP-Port `47810` für die automatische Suche
- App selbst bauen: `cd android-app && ./gradlew assembleRelease` (Android SDK nötig);
  GitHub Actions baut APK und EXE bei jedem Push automatisch.
