package com.pcmonitor.app

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.PopupMenu
import android.widget.TextView
import android.widget.Toast
import java.util.Locale

class MainActivity : Activity() {

    private lateinit var hostText: TextView
    private lateinit var statusText: TextView
    private lateinit var cpuGauge: GaugeView
    private lateinit var gpuGauge: GaugeView
    private lateinit var cpuName: TextView
    private lateinit var cpuInfo: TextView
    private lateinit var cpuTempNote: TextView
    private lateinit var gpuName: TextView
    private lateinit var gpuInfo: TextView
    private lateinit var graph: GraphView
    private lateinit var coresTitle: TextView
    private lateinit var coreGrid: CoreGridView
    private lateinit var ramText: TextView
    private lateinit var ramBar: BarView
    private lateinit var vramText: TextView
    private lateinit var vramBar: BarView
    private lateinit var wifiText: TextView
    private lateinit var wifiBar: BarView
    private lateinit var trafficText: TextView
    private lateinit var searchButton: Button

    private var cpuSeries = 0
    private var gpuSeries = 0
    private var failures = 0
    private var searching = false

    private val poller = Poller({ Prefs.address(this) }, 1000L) { result ->
        runOnUiThread { onStats(result) }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        hostText = findViewById(R.id.hostText)
        statusText = findViewById(R.id.statusText)
        cpuGauge = findViewById(R.id.cpuGauge)
        gpuGauge = findViewById(R.id.gpuGauge)
        cpuName = findViewById(R.id.cpuName)
        cpuInfo = findViewById(R.id.cpuInfo)
        cpuTempNote = findViewById(R.id.cpuTempNote)
        gpuName = findViewById(R.id.gpuName)
        gpuInfo = findViewById(R.id.gpuInfo)
        graph = findViewById(R.id.graph)
        coresTitle = findViewById(R.id.coresTitle)
        coreGrid = findViewById(R.id.coreGrid)
        ramText = findViewById(R.id.ramText)
        ramBar = findViewById(R.id.ramBar)
        vramText = findViewById(R.id.vramText)
        vramBar = findViewById(R.id.vramBar)
        wifiText = findViewById(R.id.wifiText)
        wifiBar = findViewById(R.id.wifiBar)
        trafficText = findViewById(R.id.trafficText)
        searchButton = findViewById(R.id.searchButton)

        val cpuColor = getColor(R.color.cpu)
        val gpuColor = getColor(R.color.gpu)
        cpuGauge.label = "CPU"; cpuGauge.color = cpuColor
        gpuGauge.label = "GPU"; gpuGauge.color = gpuColor
        ramBar.color = getColor(R.color.ram)
        vramBar.color = gpuColor
        wifiBar.color = getColor(R.color.net)
        coreGrid.color = cpuColor
        cpuSeries = graph.addSeries(cpuColor)
        gpuSeries = graph.addSeries(gpuColor)

        searchButton.setOnClickListener { search() }
        findViewById<View>(R.id.statusBox).setOnClickListener { askAddress() }
        findViewById<View>(R.id.menuButton).setOnClickListener { showMenu(it) }

        applyOrientation(resources.configuration)
        applyKeepScreenOn()

        if (Prefs.address(this) == null) search()
        if (Prefs.notify(this)) MonitorService.start(this)
    }

    override fun onResume() {
        super.onResume()
        updateHostLabel()
        poller.start()
    }

    override fun onPause() {
        super.onPause()
        poller.stop()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applyOrientation(newConfig)
    }

    /** Im Querformat stehen Anzeigen und Verlauf nebeneinander. */
    private fun applyOrientation(config: Configuration) {
        val land = config.orientation == Configuration.ORIENTATION_LANDSCAPE
        val content = findViewById<LinearLayout>(R.id.content)
        val gaugeRow = findViewById<View>(R.id.gaugeRow)
        val bottom = findViewById<View>(R.id.bottomArea)
        content.orientation = if (land) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
        for ((v, weight) in listOf(gaugeRow to 1.2f, bottom to 1f)) {
            val lp = v.layoutParams as LinearLayout.LayoutParams
            if (land) {
                lp.width = 0; lp.height = LinearLayout.LayoutParams.MATCH_PARENT
            } else {
                lp.width = LinearLayout.LayoutParams.MATCH_PARENT; lp.height = 0
            }
            lp.weight = weight
            v.layoutParams = lp
        }
        val bottomLp = bottom.layoutParams as LinearLayout.LayoutParams
        bottomLp.marginStart = if (land) (12 * resources.displayMetrics.density).toInt() else 0
        bottom.layoutParams = bottomLp
    }

    private fun applyKeepScreenOn() {
        if (Prefs.keepScreenOn(this)) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun updateHostLabel() {
        val addr = Prefs.address(this)
        if (addr == null) {
            hostText.text = "PC Monitor"
            statusText.text = "Kein PC gewählt – 'Suchen' tippen oder hier IP eingeben"
        }
    }

    private fun onStats(result: Result<PcStats>) {
        val addr = Prefs.address(this) ?: return
        result.onSuccess { s ->
            failures = 0
            hostText.text = s.host
            statusText.setTextColor(getColor(R.color.muted))
            statusText.text = "Verbunden · $addr"

            cpuGauge.setValue(s.cpuUsage)
            cpuName.text = s.cpuName
            cpuInfo.text = listOfNotNull(
                s.cpuFreqMhz?.let { String.format(Locale.GERMANY, "%.2f GHz", it / 1000.0) },
                s.cpuPowerW?.let { "${Math.round(it)} W" },
                s.cpuTempC?.let { "${Math.round(it)} °C" } ?: "– °C",
            ).joinToString("\n")

            val note = if (s.cpuTempC == null) s.cpuTempNote else null
            cpuTempNote.text = note ?: ""
            cpuTempNote.visibility = if (note != null) View.VISIBLE else View.GONE

            coreGrid.setCores(s.coreUsage, s.coreFreqMhz)
            val n = maxOf(s.coreUsage.size, s.coreFreqMhz.size)
            coresTitle.text = buildString {
                append(if (n > 0) "CPU-Kerne ($n)" else "CPU-Kerne")
                append(if (coreGrid.showsFrequency) "  ·  Takt in GHz" else "  ·  Auslastung")
            }

            val g = s.gpu
            gpuGauge.setValue(g?.usage)
            gpuName.text = g?.name ?: "Keine GPU-Daten"
            gpuInfo.text = if (g == null) "" else listOfNotNull(
                g.clockMhz?.let { "${Math.round(it)} MHz" },
                g.powerW?.let { "${Math.round(it)} W" },
                g.tempC?.let { "${Math.round(it)} °C" },
            ).joinToString("\n")

            ramText.text = "RAM  ${fmtGb(s.ramUsedMb)} / ${fmtGb(s.ramTotalMb)}  (${fmtPct(s.ramUsage)})"
            ramBar.setValue(s.ramUsage)
            if (g?.memUsedMb != null) {
                val total = g.memTotalMb
                val pct = if (total != null && total > 0) g.memUsedMb / total * 100 else null
                vramText.text = if (total != null) "VRAM  ${fmtGb(g.memUsedMb)} / ${fmtGb(total)}  (${fmtPct(pct)})"
                else "VRAM  ${fmtGb(g.memUsedMb)} belegt"
                vramBar.setValue(pct)
            } else {
                vramText.text = "VRAM –"
                vramBar.setValue(null)
            }

            showNetwork(s)

            graph.push(cpuSeries, s.cpuUsage.toFloat())
            graph.push(gpuSeries, g?.usage?.toFloat() ?: Float.NaN)
            graph.commit()
        }.onFailure {
            failures++
            statusText.setTextColor(getColor(R.color.error))
            statusText.text = "Keine Verbindung zu $addr – läuft der Server auf dem PC?"
            if (failures >= 3) {
                cpuGauge.setValue(null)
                gpuGauge.setValue(null)
            }
            graph.push(cpuSeries, Float.NaN)
            graph.push(gpuSeries, Float.NaN)
            graph.commit()
        }
    }

    private fun showNetwork(s: PcStats) {
        val w = s.wifi
        wifiText.text = when {
            w == null -> "WLAN –"
            !w.connected -> "WLAN  nicht verbunden"
            else -> "WLAN  " + listOfNotNull(
                w.ssid ?: "verbunden",
                w.signal?.let { "${Math.round(it)} %" },
                w.band,
                w.rxMbps?.let { "${Math.round(it)} MBit/s" },
            ).joinToString("  ·  ")
        }
        wifiBar.setValue(if (w?.connected == true) w.signal else null)

        val t = s.traffic
        trafficText.text = if (t == null) "Traffic –"
        else "${if (t.isWifi) "WLAN-Traffic" else "Traffic (${t.iface})"}  ↓ ${fmtRate(t.downBps)}   ↑ ${fmtRate(t.upBps)}"
    }

    private fun search() {
        if (searching) return
        searching = true
        searchButton.isEnabled = false
        searchButton.text = "Suche…"
        Thread {
            val found = runCatching { Discovery.search(this) }.getOrDefault(emptyList())
            runOnUiThread {
                searching = false
                searchButton.isEnabled = true
                searchButton.text = "Suchen"
                if (isFinishing) return@runOnUiThread
                when (found.size) {
                    0 -> AlertDialog.Builder(this)
                        .setTitle("Kein PC gefunden")
                        .setMessage(
                            "Stelle sicher, dass\n\n" +
                                "• der PC Monitor Server auf dem PC läuft,\n" +
                                "• Handy und PC im selben Netzwerk sind,\n" +
                                "• die Windows-Firewall den Server erlaubt.\n\n" +
                                "Du kannst die IP-Adresse auch manuell eingeben (wird im Server-Fenster angezeigt)."
                        )
                        .setPositiveButton("IP eingeben") { _, _ -> askAddress() }
                        .setNegativeButton("Schließen", null)
                        .show()
                    1 -> choose(found[0])
                    else -> AlertDialog.Builder(this)
                        .setTitle("PC wählen")
                        .setItems(found.map { "${it.name}  (${it.address})" }.toTypedArray()) { _, i -> choose(found[i]) }
                        .show()
                }
            }
        }.start()
    }

    private fun choose(pc: FoundPc) {
        Prefs.setAddress(this, pc.address)
        hostText.text = pc.name
        statusText.setTextColor(getColor(R.color.muted))
        statusText.text = "Verbinde mit ${pc.address}…"
        Toast.makeText(this, "Verbunden mit ${pc.name}", Toast.LENGTH_SHORT).show()
        MonitorService.refresh(this)
    }

    private fun askAddress() {
        val input = EditText(this).apply {
            hint = "z.B. 192.168.178.20"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(Prefs.address(this@MainActivity) ?: "")
            setSingleLine()
        }
        val pad = (20 * resources.displayMetrics.density).toInt()
        val box = LinearLayout(this).apply { setPadding(pad, pad / 2, pad, 0); addView(input) }
        AlertDialog.Builder(this)
            .setTitle("IP-Adresse des PCs")
            .setMessage("Port optional, Standard ist ${StatsClient.DEFAULT_PORT}.")
            .setView(box)
            .setPositiveButton("OK") { _, _ ->
                val v = input.text.toString().trim()
                if (v.isNotEmpty()) choose(FoundPc(v, v))
            }
            .setNegativeButton("Abbrechen", null)
            .show()
    }

    private fun showMenu(anchor: View) {
        val menu = PopupMenu(this, anchor)
        menu.menu.add(0, 1, 0, "Anzeige in Benachrichtigung").apply {
            isCheckable = true; isChecked = Prefs.notify(this@MainActivity)
        }
        menu.menu.add(0, 2, 1, "Bildschirm anlassen").apply {
            isCheckable = true; isChecked = Prefs.keepScreenOn(this@MainActivity)
        }
        menu.menu.add(0, 3, 2, "IP-Adresse eingeben")
        menu.menu.add(0, 4, 3, "Auf Nest Hub anzeigen…")
        menu.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                1 -> setNotify(!Prefs.notify(this))
                2 -> { Prefs.setKeepScreenOn(this, !Prefs.keepScreenOn(this)); applyKeepScreenOn() }
                3 -> askAddress()
                4 -> showCastDialog()
            }
            true
        }
        menu.show()
    }

    /** Fragt den PC nach Cast-Geräten und lässt eins auswählen. */
    private fun showCastDialog() {
        val addr = Prefs.address(this) ?: run { askAddress(); return }
        Toast.makeText(this, "Suche Nest Hub / Chromecast…", Toast.LENGTH_SHORT).show()
        Thread {
            val result = runCatching { CastClient.scan(addr) }
            runOnUiThread {
                if (isFinishing) return@runOnUiThread
                result.onFailure {
                    AlertDialog.Builder(this).setTitle("Nicht möglich")
                        .setMessage(it.message ?: "PC nicht erreichbar")
                        .setPositiveButton("OK", null).show()
                }.onSuccess { st ->
                    if (!st.available) {
                        AlertDialog.Builder(this).setTitle("Nicht verfügbar")
                            .setMessage("Dem PC-Server fehlt die Cast-Unterstützung.")
                            .setPositiveButton("OK", null).show()
                        return@onSuccess
                    }
                    val names = st.devices.toMutableList()
                    st.active?.let { if (it !in names) names.add(0, it) }
                    val labels = names.map { if (it == st.active) "$it  ✓" else it }.toMutableList()
                    if (st.active != null) labels.add("Anzeige beenden")
                    if (labels.isEmpty()) {
                        AlertDialog.Builder(this).setTitle("Kein Gerät gefunden")
                            .setMessage("Nest Hub und PC müssen im selben Netzwerk sein. " +
                                "Evtl. blockiert die Windows-Firewall die Suche (mDNS).")
                            .setPositiveButton("OK", null).show()
                        return@onSuccess
                    }
                    AlertDialog.Builder(this)
                        .setTitle("Auf welchem Gerät anzeigen?")
                        .setItems(labels.toTypedArray()) { _, i ->
                            if (i < names.size) castAction { CastClient.start(addr, names[i]) }
                            else castAction { CastClient.stop(addr) }
                        }
                        .setNegativeButton("Abbrechen", null)
                        .show()
                }
            }
        }.start()
    }

    private fun castAction(block: () -> CastClient.CastState) {
        Thread {
            val r = runCatching(block)
            runOnUiThread {
                val msg = r.fold({ it.status.ifBlank { "OK" } }, { "Fehler: ${it.message}" })
                Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    private fun setNotify(on: Boolean) {
        Prefs.setNotify(this, on)
        if (on) {
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
            }
            MonitorService.start(this)
        } else {
            MonitorService.stop(this)
        }
    }

    companion object {
        private const val REQ_NOTIFY = 1
    }
}
