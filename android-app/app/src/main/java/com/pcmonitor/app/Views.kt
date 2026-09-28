package com.pcmonitor.app

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.util.AttributeSet
import android.util.TypedValue
import android.view.View
import android.view.animation.DecelerateInterpolator
import kotlin.math.min

private fun View.dp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics)

/** Runde Anzeige (270°-Bogen) mit Prozentwert in der Mitte. */
class GaugeView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    var label: String = ""
        set(v) { field = v; invalidate() }
    var color: Int = Color.WHITE
        set(v) { field = v; arcPaint.color = v; invalidate() }

    private var shown = 0f
    private var hasValue = false
    private var animator: ValueAnimator? = null

    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; color = context.getColor(R.color.track)
    }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND
    }
    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.text); textAlign = Paint.Align.CENTER
        typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
    }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.muted); textAlign = Paint.Align.CENTER
    }
    private val rect = RectF()

    /** Wert 0..100, oder null für "keine Daten". */
    fun setValue(value: Double?) {
        animator?.cancel()
        if (value == null) {
            hasValue = false; shown = 0f; invalidate(); return
        }
        hasValue = true
        val target = value.toFloat().coerceIn(0f, 100f)
        animator = ValueAnimator.ofFloat(shown, target).apply {
            duration = 600
            interpolator = DecelerateInterpolator()
            addUpdateListener { shown = it.animatedValue as Float; invalidate() }
            start()
        }
    }

    override fun onDraw(canvas: Canvas) {
        val size = min(width - paddingLeft - paddingRight, height - paddingTop - paddingBottom).toFloat()
        if (size <= 0) return
        val stroke = size * 0.09f
        trackPaint.strokeWidth = stroke
        arcPaint.strokeWidth = stroke
        val cx = width / 2f
        val cy = height / 2f
        val r = (size - stroke) / 2f
        rect.set(cx - r, cy - r, cx + r, cy + r)

        canvas.drawArc(rect, 135f, 270f, false, trackPaint)
        if (hasValue && shown > 0.3f) canvas.drawArc(rect, 135f, 270f * shown / 100f, false, arcPaint)

        valuePaint.textSize = size * 0.24f
        val text = if (hasValue) "${Math.round(shown)}%" else "–"
        canvas.drawText(text, cx, cy + valuePaint.textSize * 0.35f, valuePaint)
        labelPaint.textSize = size * 0.11f
        canvas.drawText(label, cx, cy + r * 0.78f, labelPaint)
    }
}

/** Liniendiagramm für den Verlauf von CPU und GPU. */
class GraphView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val capacity = 120
    private val series = mutableListOf<Pair<Int, ArrayDeque<Float>>>()

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.track); strokeWidth = dp(1f)
    }
    private val gridText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.muted); textSize = dp(9f)
    }
    private val linePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = dp(2f); strokeJoin = Paint.Join.ROUND
    }
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val path = Path()

    fun addSeries(color: Int): Int {
        series.add(color to ArrayDeque())
        return series.size - 1
    }

    /** NaN = kein Wert (Lücke). */
    fun push(index: Int, value: Float) {
        val q = series[index].second
        q.addLast(value)
        while (q.size > capacity) q.removeFirst()
    }

    fun commit() = invalidate()

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0 || h <= 0) return
        for (p in listOf(0, 25, 50, 75, 100)) {
            val y = h - h * p / 100f
            canvas.drawLine(0f, y, w, y, gridPaint)
            if (p in 25..75) canvas.drawText("$p", dp(2f), y - dp(2f), gridText)
        }
        val step = w / (capacity - 1)
        for ((color, q) in series) {
            if (q.isEmpty()) continue
            val offset = capacity - q.size
            path.reset()
            var started = false
            var firstX = 0f
            var lastX = 0f
            q.forEachIndexed { i, v ->
                if (v.isNaN()) return@forEachIndexed
                val x = (offset + i) * step
                val y = h - h * v.coerceIn(0f, 100f) / 100f
                if (!started) { path.moveTo(x, y); started = true; firstX = x } else path.lineTo(x, y)
                lastX = x
            }
            if (!started) continue
            linePaint.color = color
            canvas.drawPath(path, linePaint)
            path.lineTo(lastX, h); path.lineTo(firstX, h); path.close()
            fillPaint.color = (color and 0x00FFFFFF) or 0x22000000
            canvas.drawPath(path, fillPaint)
        }
    }
}

/** Einfacher horizontaler Balken 0..100. */
class BarView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {
    var color: Int = Color.WHITE
        set(v) { field = v; fg.color = v; invalidate() }
    private var value = 0f
    private val bg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.track) }
    private val fg = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    fun setValue(v: Double?) {
        value = (v ?: 0.0).toFloat().coerceIn(0f, 100f); invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val r = height / 2f
        rect.set(0f, 0f, width.toFloat(), height.toFloat())
        canvas.drawRoundRect(rect, r, r, bg)
        if (value > 0f) {
            rect.set(0f, 0f, width * value / 100f, height.toFloat())
            canvas.drawRoundRect(rect, r, r, fg)
        }
    }
}
