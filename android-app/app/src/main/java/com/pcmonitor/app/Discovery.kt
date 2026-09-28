package com.pcmonitor.app

import android.content.Context
import android.net.wifi.WifiManager
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException

data class FoundPc(val name: String, val address: String)

/** Findet PCs mit laufendem Server per UDP-Broadcast im WLAN. */
object Discovery {
    private const val PORT = 47810
    private val REQUEST = "PCMON_DISCOVER".toByteArray()

    fun search(ctx: Context, timeoutMs: Int = 2500): List<FoundPc> {
        val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val lock = wifi.createMulticastLock("pcmon-discovery").apply { setReferenceCounted(false) }
        val found = LinkedHashMap<String, FoundPc>()
        try {
            lock.acquire()
            DatagramSocket().use { socket ->
                socket.broadcast = true
                socket.soTimeout = 400
                val targets = broadcastAddresses()
                val deadline = System.currentTimeMillis() + timeoutMs
                var nextSend = 0L
                val buf = ByteArray(1024)
                while (System.currentTimeMillis() < deadline) {
                    if (System.currentTimeMillis() >= nextSend) {
                        for (t in targets) {
                            runCatching { socket.send(DatagramPacket(REQUEST, REQUEST.size, t, PORT)) }
                        }
                        nextSend = System.currentTimeMillis() + 800
                    }
                    val packet = DatagramPacket(buf, buf.size)
                    try {
                        socket.receive(packet)
                    } catch (_: SocketTimeoutException) {
                        continue
                    }
                    val text = String(packet.data, 0, packet.length)
                    runCatching {
                        val j = JSONObject(text)
                        if (j.optString("app") == "pcmon") {
                            val ip = packet.address.hostAddress ?: return@runCatching
                            val port = j.optInt("port", StatsClient.DEFAULT_PORT)
                            val addr = "$ip:$port"
                            found[addr] = FoundPc(j.optString("name", ip), addr)
                        }
                    }
                }
            }
        } finally {
            runCatching { lock.release() }
        }
        return found.values.toList()
    }

    private fun broadcastAddresses(): List<InetAddress> {
        val list = mutableListOf<InetAddress>(InetAddress.getByName("255.255.255.255"))
        runCatching {
            for (nif in NetworkInterface.getNetworkInterfaces()) {
                if (!nif.isUp || nif.isLoopback) continue
                for (ia in nif.interfaceAddresses) {
                    ia.broadcast?.let { if (it !in list) list.add(it) }
                }
            }
        }
        return list
    }
}
