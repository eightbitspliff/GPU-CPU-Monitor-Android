package com.pcmonitor.app

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class GpuStats(
    val name: String,
    val usage: Double?,
    val memUsedMb: Double?,
    val memTotalMb: Double?,
    val tempC: Double?,
    val powerW: Double?,
    val clockMhz: Double?,
)

data class PcStats(
    val host: String,
    val cpuName: String,
    val cpuUsage: Double,
    val cpuFreqMhz: Double?,
    val cpuTempC: Double?,
    val cpuPowerW: Double?,
    /** Hinweis vom Server, warum die CPU-Temperatur fehlt. */
    val cpuTempNote: String?,
    /** Auslastung je (logischem) Kern in %. */
    val coreUsage: List<Double>,
    /** Aktueller Takt je Kern in MHz, gleiche Reihenfolge wie [coreUsage]; leer = unbekannt. */
    val coreFreqMhz: List<Double?>,
    val ramUsedMb: Double,
    val ramTotalMb: Double,
    val ramUsage: Double,
    val gpus: List<GpuStats>,
) {
    val gpu: GpuStats? get() = gpus.firstOrNull()
}

object StatsClient {
    const val DEFAULT_PORT = 47811

    /** Macht aus "192.168.0.10" oder "192.168.0.10:47811" eine URL. */
    fun urlFor(address: String): String {
        val a = address.trim().removePrefix("http://").trimEnd('/')
        val withPort = if (a.contains(':')) a else "$a:$DEFAULT_PORT"
        return "http://$withPort/stats"
    }

    fun fetch(address: String): PcStats {
        val conn = URL(urlFor(address)).openConnection() as HttpURLConnection
        conn.connectTimeout = 2000
        conn.readTimeout = 2000
        conn.useCaches = false
        try {
            if (conn.responseCode != 200) error("HTTP ${conn.responseCode}")
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            return parse(JSONObject(body))
        } finally {
            conn.disconnect()
        }
    }

    private fun JSONObject.num(key: String): Double? =
        if (has(key) && !isNull(key)) optDouble(key).takeIf { !it.isNaN() } else null

    private fun parse(j: JSONObject): PcStats {
        val cpu = j.getJSONObject("cpu")
        val ram = j.getJSONObject("ram")
        val gpuArr = j.optJSONArray("gpus")
        val gpus = buildList {
            if (gpuArr != null) for (i in 0 until gpuArr.length()) {
                val g = gpuArr.getJSONObject(i)
                add(
                    GpuStats(
                        name = g.optString("name", "GPU").ifBlank { "GPU" },
                        usage = g.num("usage"),
                        memUsedMb = g.num("mem_used_mb"),
                        memTotalMb = g.num("mem_total_mb"),
                        tempC = g.num("temp_c"),
                        powerW = g.num("power_w"),
                        clockMhz = g.num("clock_mhz"),
                    )
                )
            }
        }
        fun numList(key: String): List<Double?> {
            val arr = cpu.optJSONArray(key) ?: return emptyList()
            return List(arr.length()) { i -> if (arr.isNull(i)) null else arr.optDouble(i).takeIf { !it.isNaN() } }
        }
        return PcStats(
            host = j.optString("host", "PC"),
            cpuName = cpu.optString("name", "CPU"),
            cpuUsage = cpu.num("usage") ?: 0.0,
            cpuFreqMhz = cpu.num("freq_mhz"),
            cpuTempC = cpu.num("temp_c"),
            cpuPowerW = cpu.num("power_w"),
            cpuTempNote = if (cpu.isNull("temp_note")) null else cpu.optString("temp_note").ifBlank { null },
            coreUsage = numList("cores").map { it ?: 0.0 },
            coreFreqMhz = numList("core_freq_mhz"),
            ramUsedMb = ram.num("used_mb") ?: 0.0,
            ramTotalMb = ram.num("total_mb") ?: 0.0,
            ramUsage = ram.num("usage") ?: 0.0,
            gpus = gpus,
        )
    }
}

/** Fragt den PC in einem eigenen Thread regelmäßig ab. */
class Poller(
    private val addressProvider: () -> String?,
    private val intervalMs: Long,
    private val onResult: (Result<PcStats>) -> Unit,
) {
    @Volatile private var running = false
    private var thread: Thread? = null

    fun start() {
        if (running) return
        running = true
        thread = Thread {
            while (running) {
                val started = System.currentTimeMillis()
                val address = addressProvider()
                if (address != null) {
                    val result = runCatching { StatsClient.fetch(address) }
                    if (running) onResult(result)
                }
                val wait = intervalMs - (System.currentTimeMillis() - started)
                if (wait > 0) try { Thread.sleep(wait) } catch (_: InterruptedException) { }
            }
        }.apply { isDaemon = true; name = "pc-poller"; start() }
    }

    fun stop() {
        running = false
        thread?.interrupt()
        thread = null
    }
}

object Prefs {
    private const val FILE = "pcmon"
    private const val KEY_ADDRESS = "address"
    private const val KEY_NOTIFY = "notify"
    private const val KEY_KEEP_ON = "keep_on"

    private fun p(ctx: android.content.Context) =
        ctx.getSharedPreferences(FILE, android.content.Context.MODE_PRIVATE)

    fun address(ctx: android.content.Context): String? = p(ctx).getString(KEY_ADDRESS, null)
    fun setAddress(ctx: android.content.Context, v: String?) = p(ctx).edit().putString(KEY_ADDRESS, v).apply()

    fun notify(ctx: android.content.Context) = p(ctx).getBoolean(KEY_NOTIFY, false)
    fun setNotify(ctx: android.content.Context, v: Boolean) = p(ctx).edit().putBoolean(KEY_NOTIFY, v).apply()

    fun keepScreenOn(ctx: android.content.Context) = p(ctx).getBoolean(KEY_KEEP_ON, true)
    fun setKeepScreenOn(ctx: android.content.Context, v: Boolean) = p(ctx).edit().putBoolean(KEY_KEEP_ON, v).apply()
}

fun fmtPct(v: Double?) = if (v == null) "–" else "${Math.round(v)} %"

fun fmtGb(mb: Double?) = if (mb == null) "–" else String.format(java.util.Locale.GERMANY, "%.1f GB", mb / 1024.0)

/** Steuert über den PC-Server die Anzeige auf Nest Hub / Chromecast. */
object CastClient {
    data class CastState(val available: Boolean, val active: String?, val status: String, val devices: List<String>)

    private fun base(address: String) = StatsClient.urlFor(address).removeSuffix("/stats")

    private fun request(url: String, method: String): JSONObject {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 3000
        conn.readTimeout = 15000 // Gerätesuche dauert ein paar Sekunden
        try {
            if (method == "POST") {
                conn.doOutput = true
                conn.outputStream.use { }
            }
            if (conn.responseCode == 404) error("PC-Server ist zu alt – bitte neue PCMonitorServer.exe verwenden")
            if (conn.responseCode != 200) error("HTTP ${conn.responseCode}")
            return JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
        } finally {
            conn.disconnect()
        }
    }

    private fun parse(j: JSONObject): CastState {
        val arr = j.optJSONArray("devices")
        val devices = buildList { if (arr != null) for (i in 0 until arr.length()) add(arr.getJSONObject(i).optString("name")) }
        return CastState(
            available = j.optBoolean("available", false),
            active = if (j.isNull("active")) null else j.optString("active"),
            status = j.optString("status", ""),
            devices = devices,
        )
    }

    fun scan(address: String) = parse(request("${base(address)}/cast?scan=1", "GET"))

    fun start(address: String, device: String) =
        parse(request("${base(address)}/cast/start?device=" + java.net.URLEncoder.encode(device, "UTF-8"), "POST"))

    fun stop(address: String) = parse(request("${base(address)}/cast/stop", "POST"))
}
