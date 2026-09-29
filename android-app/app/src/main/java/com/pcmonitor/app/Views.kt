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

        valuePaint.textSize = size * 0.26f
        val text = if (hasValue) "${Math.round(shown)}%" else "–"
        canvas.drawText(text, cx, cy + valuePaint.textSize * 0.35f, valuePaint)
        labelPaint.textSize = size * 0.13f
        canvas.drawText(label, cx, cy + r * 0.78f, labelPaint)
    }
}

/** Liniendiagramm für den Verlauf von CPU und GPU. */
class GraphView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    private val capacity = 150 // 5 Minuten bei einem Wert alle 2 s
    private val series = mutableListOf<Pair<Int, ArrayDeque<Float>>>()

    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.track); strokeWidth = dp(1f)
    }
    private val gridText = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.muted); textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, 11f, resources.displayMetrics)
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

/** Raster mit einer Zelle je CPU-Kern: Nummer, aktueller Takt und Auslastungsbalken. */
class CoreGridView @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null,
) : View(context, attrs) {

    var color: Int = Color.WHITE
        set(v) { field = v; barPaint.color = v; invalidate() }

    private var usage: List<Double> = emptyList()
    private var freqMhz: List<Double?> = emptyList()

    private val minCellWidth = dp(66f)
    private val rowHeight = dp(30f)
    private val gap = dp(8f)
    private val barHeight = dp(4f)

    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.muted); textSize = sp(13f)
    }
    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.text); textSize = sp(14f); textAlign = Paint.Align.RIGHT
        typeface = android.graphics.Typeface.create("sans-serif-medium", android.graphics.Typeface.NORMAL)
    }
    private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.track) }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rect = RectF()

    private fun sp(v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, v, resources.displayMetrics)

    /** Zeigt den Takt, wenn bekannt – sonst die Auslastung je Kern. */
    val showsFrequency get() = freqMhz.any { it != null }

    fun setCores(usage: List<Double>, freqMhz: List<Double?>) {
        val oldCount = count
        this.usage = usage
        this.freqMhz = freqMhz
        if (count != oldCount) requestLayout()
        invalidate()
    }

    private val count get() = maxOf(usage.size, freqMhz.size)

    private fun columns(width: Int): Int {
        val w = width - paddingLeft - paddingRight
        return ((w + gap) / (minCellWidth + gap)).toInt().coerceIn(1, maxOf(1, count))
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val rows = if (count == 0) 0 else (count + columns(w) - 1) / columns(w)
        val h = (rows * rowHeight + paddingTop + paddingBottom).toInt()
        setMeasuredDimension(w, resolveSize(h, heightMeasureSpec))
    }

    override fun onDraw(canvas: Canvas) {
        val n = count
        if (n == 0) return
        val cols = columns(width)
        val cellW = (width - paddingLeft - paddingRight - gap * (cols - 1)) / cols
        val freq = showsFrequency
        for (i in 0 until n) {
            val x = paddingLeft + (i % cols) * (cellW + gap)
            val y = paddingTop + (i / cols) * rowHeight
            val base = y + rowHeight - barHeight - dp(6f)
            canvas.drawText("${i + 1}", x, base - dp(3f), labelPaint)
            val u = usage.getOrNull(i)
            val value = if (freq) freqMhz.getOrNull(i)?.let { String.format(java.util.Locale.GERMANY, "%.2f", it / 1000.0) } ?: "–"
            else u?.let { "${Math.round(it)}%" } ?: "–"
            canvas.drawText(value, x + cellW, base - dp(3f), valuePaint)
            rect.set(x, base, x + cellW, base + barHeight)
            canvas.drawRoundRect(rect, barHeight / 2, barHeight / 2, trackPaint)
            val p = (u ?: 0.0).toFloat().coerceIn(0f, 100f)
            if (p > 0f) {
                rect.right = x + cellW * p / 100f
                canvas.drawRoundRect(rect, barHeight / 2, barHeight / 2, barPaint)
            }
        }
    }
}
