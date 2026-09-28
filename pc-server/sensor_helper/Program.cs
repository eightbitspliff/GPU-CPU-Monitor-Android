using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using LibreHardwareMonitor.Hardware;
using Microsoft.Win32;

// Gibt jede Sekunde eine JSON-Zeile aus, z.B.
// {"admin":true,"pawnio":true,"cpu_temp":54.5,"cpu_power":88.2,"gpus":[{"name":"AMD Radeon RX 7800 XT","vendor":"amd",
//  "power_w":212.3,"clock_mhz":2430,"temp_c":61,"usage":97}]}
// Beendet sich, sobald der PC-Server die Pipe schließt.
static class Program
{
    static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    static int Main()
    {
        var computer = new Computer { IsCpuEnabled = true, IsGpuEnabled = true };
        try
        {
            computer.Open();
        }
        catch (Exception e)
        {
            Console.Error.WriteLine("Open fehlgeschlagen: " + e.Message);
        }

        bool admin = IsAdmin();
        bool pawnIo = PawnIoInstalled();
        try
        {
            while (true)
            {
                string line;
                try
                {
                    line = Sample(computer, admin, pawnIo);
                }
                catch (Exception e)
                {
                    line = "{\"error\":" + Str(e.Message) + "}";
                }
                Console.Out.WriteLine(line);
                Console.Out.Flush();
                Thread.Sleep(1000);
            }
        }
        catch (Exception)
        {
            // Pipe geschlossen -> PC-Server beendet
        }
        finally
        {
            try { computer.Close(); } catch { }
        }
        return 0;
    }

    static string Sample(Computer computer, bool admin, bool pawnIo)
    {
        double? cpuTemp = null;
        double? cpuPower = null;
        var gpus = new List<string>();
        foreach (IHardware hw in computer.Hardware)
        {
            Update(hw);
            var sensors = AllSensors(hw).ToList();
            switch (hw.HardwareType)
            {
                case HardwareType.Cpu:
                    if (cpuTemp == null) cpuTemp = CpuTemperature(sensors);
                    if (cpuPower == null) cpuPower = CpuPower(sensors);
                    break;
                case HardwareType.GpuNvidia:
                case HardwareType.GpuAmd:
                case HardwareType.GpuIntel:
                    string vendor = hw.HardwareType == HardwareType.GpuNvidia ? "nvidia"
                        : hw.HardwareType == HardwareType.GpuAmd ? "amd" : "intel";
                    gpus.Add("{\"name\":" + Str(hw.Name) + ",\"vendor\":\"" + vendor + "\"" +
                             ",\"power_w\":" + Num(GpuPower(sensors)) +
                             ",\"clock_mhz\":" + Num(Pick(sensors, SensorType.Clock, "^GPU Core$", "Core", "Shader")) +
                             ",\"temp_c\":" + Num(Pick(sensors, SensorType.Temperature, "^GPU Core$", "Core", "Edge")) +
                             ",\"usage\":" + Num(Pick(sensors, SensorType.Load, "^GPU Core$", "^D3D 3D$", "Core")) +
                             "}");
                    break;
            }
        }
        return "{\"admin\":" + (admin ? "true" : "false") +
               ",\"pawnio\":" + (pawnIo ? "true" : "false") +
               ",\"cpu_temp\":" + Num(cpuTemp) +
               ",\"cpu_power\":" + Num(cpuPower) +
               ",\"gpus\":[" + string.Join(",", gpus) + "]}";
    }

    static void Update(IHardware hw)
    {
        hw.Update();
        foreach (IHardware sub in hw.SubHardware) Update(sub);
    }

    static IEnumerable<ISensor> AllSensors(IHardware hw)
    {
        foreach (ISensor s in hw.Sensors) yield return s;
        foreach (IHardware sub in hw.SubHardware)
            foreach (ISensor s in AllSensors(sub)) yield return s;
    }

    static bool Valid(ISensor s) => s.Value.HasValue && !float.IsNaN(s.Value.Value) && !float.IsInfinity(s.Value.Value);

    /// Erster Sensor, dessen Name auf eines der Muster passt (in dieser Reihenfolge).
    static double? Pick(List<ISensor> sensors, SensorType type, params string[] patterns)
    {
        var candidates = sensors.Where(s => s.SensorType == type && Valid(s)).ToList();
        foreach (string p in patterns)
        {
            var hit = candidates.FirstOrDefault(s => Regex.IsMatch(s.Name, p, RegexOptions.IgnoreCase));
            if (hit != null) return hit.Value.Value;
        }
        return null;
    }

    static double? CpuTemperature(List<ISensor> sensors)
    {
        double? t = Pick(sensors, SensorType.Temperature,
            "Package", "Tctl", "Tdie", "Core Max", "Core Average", "CCD");
        if (t == null)
        {
            var all = sensors.Where(s => s.SensorType == SensorType.Temperature && Valid(s)).ToList();
            if (all.Count > 0) t = all.Max(s => s.Value.Value);
        }
        return t != null && t > 5 && t < 125 ? t : null;
    }

    /// Leistungsaufnahme des ganzen Prozessors (Package), sonst Summe der Kerne.
    static double? CpuPower(List<ISensor> sensors)
    {
        double? p = Pick(sensors, SensorType.Power, "Package", "^CPU Total$", "Total");
        if (p == null)
        {
            var cores = sensors.Where(s => s.SensorType == SensorType.Power && Valid(s) &&
                                           Regex.IsMatch(s.Name, "Core", RegexOptions.IgnoreCase)).ToList();
            if (cores.Count > 0) p = cores.Sum(s => s.Value.Value);
        }
        return p != null && p >= 0 && p < 1500 ? p : null;
    }

    /// Gesamtleistungsaufnahme der Grafikkarte (Board/Package), sonst größter Leistungswert.
    static double? GpuPower(List<ISensor> sensors)
    {
        double? p = Pick(sensors, SensorType.Power, "Board", "Total", "^GPU Package$", "Package", "^GPU Power$");
        if (p == null)
        {
            var all = sensors.Where(s => s.SensorType == SensorType.Power && Valid(s)).ToList();
            if (all.Count > 0) p = all.Max(s => s.Value.Value);
        }
        return p != null && p >= 0 && p < 2000 ? p : null;
    }

    static bool IsAdmin()
    {
        try
        {
            return new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator);
        }
        catch
        {
            return false;
        }
    }

    static bool PawnIoInstalled()
    {
        try
        {
            using (var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64)
                       .OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"))
                return k != null;
        }
        catch
        {
            return false;
        }
    }

    static string Num(double? v) => v.HasValue ? Math.Round(v.Value, 1).ToString(Inv) : "null";

    static string Str(string s)
    {
        var sb = new StringBuilder("\"");
        foreach (char c in s ?? "")
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < 0x20) sb.Append(' ');
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }
}
