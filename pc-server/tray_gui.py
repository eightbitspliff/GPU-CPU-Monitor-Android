"""Fenster + Systemtray für den PC Monitor Server (Windows).

Minimieren -> Fenster verschwindet, Icon bleibt im Systemtray.
Doppelklick aufs Tray-Icon oder "Anzeigen" holt das Fenster zurück.
"""

import json
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pystray

from app_icon import make_icon

BG = "#0E1116"
CARD = "#171B22"
TEXT = "#E8ECF2"
MUTED = "#8A94A6"
CPU = "#3FA9F5"
GPU = "#7BD85A"


def show_error(msg):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("PC Monitor Server", msg)
    root.destroy()


SETTINGS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "PCMonitor", "settings.json")


def _load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _rate(bps):
    if bps >= 1e6:
        return f"{bps / 1e6:.1f} MBit/s".replace(".", ",")
    return f"{round(bps / 1e3)} kBit/s"


def _net_text(n):
    lines = []
    w = n.get("wifi")
    if w:
        if w.get("connected"):
            parts = [w.get("ssid") or "verbunden"]
            if w.get("signal") is not None:
                parts.append(f"{round(w['signal'])} %")
            if w.get("band"):
                parts.append(w["band"])
            lines.append("WLAN: " + " · ".join(parts))
        else:
            lines.append("WLAN: nicht verbunden")
    bt = n.get("bluetooth")
    if bt:
        devs = bt.get("devices") or []
        lines.append("Bluetooth: " + (", ".join(
            d["name"] + (f" ({round(d['battery'])} %)" if d.get("battery") is not None else "") for d in devs)
            if devs else "kein Gerät verbunden"))
    t = n.get("traffic")
    if t:
        lines.append(f"{'WLAN' if t.get('wifi') else t.get('iface')}: ↓ {_rate(t['down_bps'])}  ↑ {_rate(t['up_bps'])}")
    return "\n".join(lines)


def _pct(v):
    return "–" if v is None else f"{round(v)} %"


def run(sampler, caster, ips, port, start_hidden=False):
    root = tk.Tk()
    root.title("PC Monitor Server")
    root.configure(bg=BG)
    root.resizable(False, False)
    icon_img = make_icon(64)
    try:
        from PIL import ImageTk
        root.iconphoto(True, ImageTk.PhotoImage(icon_img))
    except Exception:
        pass

    pad = {"padx": 16}
    tk.Label(root, text="PC Monitor Server läuft", bg=BG, fg=TEXT,
             font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(14, 2), **pad)
    tk.Label(root, text="In der App 'Suchen' tippen oder diese Adresse eintragen:",
             bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", **pad)
    for ip in ips or ["(keine Netzwerkadresse gefunden)"]:
        tk.Label(root, text=f"{ip}:{port}" if ips else ip, bg=BG, fg=TEXT,
                 font=("Consolas", 12)).pack(anchor="w", **pad)

    card = tk.Frame(root, bg=CARD)
    card.pack(fill="x", pady=12, **pad)
    cpu_var = tk.StringVar(value="CPU –")
    gpu_var = tk.StringVar(value="GPU –")
    tk.Label(card, textvariable=cpu_var, bg=CARD, fg=CPU,
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
    tk.Label(card, text=sampler.cpu_name, bg=CARD, fg=MUTED,
             font=("Segoe UI", 8)).pack(anchor="w", padx=12)
    tk.Label(card, textvariable=gpu_var, bg=CARD, fg=GPU,
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    tk.Label(card, text="GPU-Quelle: " + (sampler.gpu.source or "keine GPU-Daten gefunden"),
             bg=CARD, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=12)

    gpu_power_var = tk.StringVar(value="")
    tk.Label(card, textvariable=gpu_power_var, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
             wraplength=300, justify="left").pack(anchor="w", padx=12)

    bw_var = tk.StringVar(value="")
    tk.Label(card, textvariable=bw_var, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
             wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(4, 0))

    net_var = tk.StringVar(value="")
    tk.Label(card, textvariable=net_var, bg=CARD, fg=TEXT, font=("Segoe UI", 9),
             wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(6, 0))

    # ------------------------------------------------ CPU-Temperatur (Treiber PawnIO)
    lhm = sampler.lhm
    temp_var = tk.StringVar(value="CPU-Temperatur: –")
    tk.Label(card, textvariable=temp_var, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
             wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(4, 0))
    pawn_btn = ttk.Button(card, text="CPU-Temperatur aktivieren (Treiber PawnIO installieren)")
    pawn_row = {"shown": False}

    def install_pawnio():
        pawn_btn.configure(state="disabled", text="Installiere Treiber…")

        def work():
            ok, msg = lhm.install_pawnio()

            def done():
                pawn_btn.configure(state="normal", text="CPU-Temperatur aktivieren (Treiber PawnIO installieren)")
                (messagebox.showinfo if ok else messagebox.showerror)("PC Monitor Server", msg)
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    pawn_btn.configure(command=install_pawnio)
    tk.Frame(card, bg=CARD, height=10).pack(side="bottom")

    def ask_pawnio_once():
        settings = _load_settings()
        if not lhm.needs_pawnio() or not lhm.admin or settings.get("pawnio_declined"):
            return
        if messagebox.askyesno(
                "PC Monitor Server",
                "Windows gibt die CPU-Temperatur nur über einen Treiber heraus.\n\n"
                "Soll der Treiber PawnIO (signiert, Open Source, auch von LibreHardwareMonitor "
                "genutzt) jetzt installiert werden?"):
            install_pawnio()
        else:
            settings["pawnio_declined"] = True
            _save_settings(settings)

    root.after(5000, ask_pawnio_once)

    # ------------------------------------------------ Nest Hub / Chromecast
    cast_card = tk.Frame(root, bg=CARD)
    cast_card.pack(fill="x", pady=(0, 12), **pad)
    tk.Label(cast_card, text="Auf Nest Hub / Chromecast anzeigen", bg=CARD, fg=TEXT,
             font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
    row = tk.Frame(cast_card, bg=CARD)
    row.pack(fill="x", padx=12)
    device_var = tk.StringVar(value=caster.target or "")
    combo = ttk.Combobox(row, textvariable=device_var, width=26,
                         values=[caster.target] if caster.target else [])
    combo.pack(side="left")
    search_btn = ttk.Button(row, text="Suchen")
    search_btn.pack(side="left", padx=(6, 0))
    row2 = tk.Frame(cast_card, bg=CARD)
    row2.pack(fill="x", padx=12, pady=(6, 0))
    start_btn = ttk.Button(row2, text="Anzeigen")
    start_btn.pack(side="left")
    stop_btn = ttk.Button(row2, text="Stoppen")
    stop_btn.pack(side="left", padx=(6, 0))
    cast_status = tk.StringVar(value=caster.status)
    tk.Label(cast_card, textvariable=cast_status, bg=CARD, fg=MUTED, font=("Segoe UI", 8),
             wraplength=300, justify="left").pack(anchor="w", padx=12, pady=(6, 10))

    caster.listeners.append(lambda text: root.after(0, cast_status.set, text))

    def do_search():
        search_btn.configure(state="disabled", text="Suche…")

        def work():
            try:
                names = [d["name"] for d in caster.discover()]
            except Exception:
                names = []

            def done():
                search_btn.configure(state="normal", text="Suchen")
                combo.configure(values=names)
                if names and device_var.get() not in names:
                    device_var.set(names[0])
                if not names:
                    cast_status.set("Kein Gerät gefunden – gleiches WLAN? Firewall?")
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def do_start():
        name = device_var.get().strip()
        if name:
            caster.start(name)
        else:
            cast_status.set("Erst 'Suchen' und ein Gerät wählen")

    search_btn.configure(command=do_search)
    start_btn.configure(command=do_start)
    stop_btn.configure(command=caster.stop)
    if not caster.state()["available"]:
        for b in (search_btn, start_btn, stop_btn):
            b.configure(state="disabled")
    elif not caster.target:
        do_search()

    tk.Label(root, text="Minimieren legt das Fenster in den Systemtray.",
             bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 12), **pad)

    # ---------------------------------------------------------------- Tray
    def show_window(icon=None, item=None):
        root.after(0, _show)

    def _show():
        root.deiconify()
        root.state("normal")
        root.lift()
        root.focus_force()

    def quit_app(icon=None, item=None):
        root.after(0, _quit)

    def _quit():
        lhm.stop()
        tray.stop()
        root.destroy()

    tray = pystray.Icon(
        "pcmonitor", icon_img, "PC Monitor Server",
        menu=pystray.Menu(
            pystray.MenuItem("Anzeigen", show_window, default=True),
            pystray.MenuItem("Beenden", quit_app),
        ),
    )
    threading.Thread(target=tray.run, daemon=True).start()

    def on_unmap(event):
        # Minimiert -> ganz ausblenden, nur das Tray-Icon bleibt.
        if event.widget is root and root.state() == "iconic":
            root.withdraw()

    root.bind("<Unmap>", on_unmap)
    root.protocol("WM_DELETE_WINDOW", _quit)

    # ------------------------------------------------------- Live-Werte
    def refresh():
        d = sampler.snapshot()
        if d:
            cpu = d["cpu"]["usage"]
            gpus = d.get("gpus") or []
            gpu = gpus[0]["usage"] if gpus else None
            temp = d["cpu"].get("temp_c")
            g0 = gpus[0] if gpus else {}
            cpu_w = d["cpu"].get("power_w")
            cpu_var.set(f"CPU  {_pct(cpu)}" + (f"  ·  {round(temp)} °C" if temp is not None else "")
                        + (f"  ·  {round(cpu_w)} W" if cpu_w is not None else ""))
            ram = d.get("ram") or {}
            bw_parts = []
            if ram.get("bandwidth_max_gbs") is not None or ram.get("bandwidth_gbs") is not None:
                cur = ram.get("bandwidth_gbs")
                bw_parts.append("RAM-Bandbreite: " + (f"{cur:.1f} / " if cur is not None else "aktuell – (HWiNFO mit Shared Memory starten) / max. ")
                                + f"{ram.get('bandwidth_max_gbs') or '–'} GB/s" + (f" ({ram['bandwidth_desc']})" if ram.get("bandwidth_desc") else ""))
            if g0.get("vram_bw_gbs") is not None:
                bw_parts.append(f"VRAM-Bandbreite: {g0['vram_bw_gbs']:.0f} / {g0.get('vram_bw_max_gbs') or '–'} GB/s")
            elif g0.get("vram_ctrl_pct") is not None:
                bw_parts.append(f"VRAM-Controller: {round(g0['vram_ctrl_pct'])} %")
            bw_var.set("\n".join(bw_parts))
            gpu_var.set(f"GPU  {_pct(gpu)}" + (f"  ·  {round(g0['power_w'])} W" if g0.get("power_w") is not None else ""))
            src = g0.get("power_sources") or {}
            gpu_power_var.set("GPU-Leistungssensoren: " + ", ".join(
                f"{k} {round(v)} W" for k, v in sorted(src.items(), key=lambda kv: -kv[1])) if src else "")
            net_var.set(_net_text(d.get("net") or {}))
            note = d["cpu"].get("temp_note")
            temp_var.set("CPU-Temperatur: " + (f"{round(temp)} °C" if temp is not None else (note or "–")))
            want = lhm.needs_pawnio() and lhm.admin
            if want != pawn_row["shown"]:
                if want:
                    pawn_btn.pack(anchor="w", padx=12, pady=(4, 0))
                else:
                    pawn_btn.pack_forget()
                pawn_row["shown"] = want
            try:
                tray.title = f"PC Monitor – CPU {_pct(cpu)} · GPU {_pct(gpu)}"
            except Exception:
                pass
        root.after(1000, refresh)

    refresh()
    if start_hidden:
        root.withdraw()
    root.mainloop()
