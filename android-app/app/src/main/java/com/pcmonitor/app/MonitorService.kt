package com.pcmonitor.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Hält die Auslastung dauerhaft in der Benachrichtigungsleiste,
 * auch wenn die App im Hintergrund ist.
 */
class MonitorService : Service() {

    private lateinit var nm: NotificationManager
    private var poller: Poller? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "PC-Auslastung", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Dauerhafte Anzeige von CPU- und GPU-Auslastung"
                setShowBadge(false)
            }
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Prefs.setNotify(this, false)
            stopSelf()
            return START_NOT_STICKY
        }
        val initial = build("PC Monitor", "Verbinde…")
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIF_ID, initial, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIF_ID, initial)
        }
        if (poller == null) {
            poller = Poller({ Prefs.address(this) }, 2000L) { r -> update(r) }.also { it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        poller?.stop()
        poller = null
        super.onDestroy()
    }

    private fun update(result: Result<PcStats>) {
        val n = result.fold(
            onSuccess = { s ->
                val g = s.gpu
                val title = "CPU ${fmtPct(s.cpuUsage)}   ·   GPU ${fmtPct(g?.usage)}"
                val details = listOfNotNull(
                    "RAM ${fmtPct(s.ramUsage)}",
                    g?.tempC?.let { "GPU ${Math.round(it)} °C" },
                    s.cpuTempC?.let { "CPU ${Math.round(it)} °C" },
                    s.cpuPowerW?.let { "CPU ${Math.round(it)} W" },
                    g?.powerW?.let { "GPU ${Math.round(it)} W" },
                    s.host,
                ).joinToString("  ·  ")
                build(title, details)
            },
            onFailure = { build("PC nicht erreichbar", Prefs.address(this) ?: "Kein PC gewählt") },
        )
        nm.notify(NOTIF_ID, n)
    }

    private fun build(title: String, text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, MonitorService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_STATUS)
            .addAction(Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Beenden", stop).build())
            .build()
    }

    companion object {
        private const val CHANNEL = "pc_stats"
        private const val NOTIF_ID = 1
        private const val ACTION_STOP = "com.pcmonitor.app.STOP"

        fun start(ctx: Context) {
            ctx.startForegroundService(Intent(ctx, MonitorService::class.java))
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, MonitorService::class.java))
        }

        /** Nach Wechsel des PCs neu starten, falls aktiv. */
        fun refresh(ctx: Context) {
            if (Prefs.notify(ctx)) start(ctx)
        }
    }
}
