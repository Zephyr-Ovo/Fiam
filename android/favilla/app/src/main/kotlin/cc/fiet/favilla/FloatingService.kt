package cc.fiet.favilla

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView
import androidx.core.app.NotificationCompat
import kotlin.math.abs

/**
 * Foreground service that shows a draggable crow bubble over every app.
 * Tap = send clipboard to fiam. Long-press-drag = move. Double-tap = stop.
 */
class FloatingService : Service() {

    private lateinit var wm: WindowManager
    private var bubble: View? = null
    private lateinit var lp: WindowManager.LayoutParams

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        // A new bubble session = a new conversation boundary. Every capture
        // sent while this service is alive carries the same session tag so the
        // server (or later batching) can group them into one fiam event.
        sessionId = java.util.UUID.randomUUID().toString().substring(0, 8) +
            "-" + (System.currentTimeMillis() / 1000).toString()
        startInForeground()
        addBubble()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        bubble?.let { runCatching { wm.removeView(it) } }
        bubble = null
        sessionId = null
        // Ending the bubble = ending the conversation. Any follow-up cached
        // selection from the previous session must not leak into the next one.
        FavillaAccessibilityService.clearCache()
        super.onDestroy()
    }

    private fun startInForeground() {
        val ch = NOTIF_CHANNEL
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            if (nm.getNotificationChannel(ch) == null) {
                nm.createNotificationChannel(
                    NotificationChannel(
                        ch,
                        getString(R.string.notif_channel_name),
                        NotificationManager.IMPORTANCE_MIN
                    )
                )
            }
        }
        val notif: Notification = NotificationCompat.Builder(this, ch)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notif_running))
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        startForeground(NOTIF_ID, notif)
    }

    private fun dp(v: Float): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics
    ).toInt()

    private fun addBubble() {
        val size = dp(56f)
        val container = FrameLayout(this).apply {
            layoutParams = ViewGroup.LayoutParams(size, size)
            setBackgroundResource(R.drawable.bg_bubble)
        }
        val crow = TextView(this).apply {
            text = "🐦\u200D⬛"
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 26f)
            gravity = Gravity.CENTER
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ).apply { gravity = Gravity.CENTER }
        }
        container.addView(crow)

        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_PHONE

        lp = WindowManager.LayoutParams(
            size, size, type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(12f)
            y = dp(200f)
        }

        val gestures = BubbleGestureListener(
            lp = lp,
            onTap = {
                // Pull selection from accessibility BEFORE we launch any activity
                // (once QuickCaptureActivity is foreground, the underlying selection is gone).
                val sel = FavillaAccessibilityService.currentSelection()
                QuickCaptureActivity.launch(this, sel)
            },
            onDoubleTap = {
                stopSelf()
            },
            onMoved = { runCatching { wm.updateViewLayout(container, lp) } },
        )
        container.setOnTouchListener(gestures)

        wm.addView(container, lp)
        bubble = container
    }

    companion object {
        const val NOTIF_CHANNEL = "favilla_bubble"
        const val NOTIF_ID = 4201
        const val ACTION_STOP = "cc.fiet.favilla.STOP_BUBBLE"

        /**
         * Non-null while the floating bubble is active. All captures sent
         * during the same bubble lifetime share this id; toggling the bubble
         * off = closing the conversation (the event boundary).
         */
        @Volatile
        var sessionId: String? = null
            private set

        fun start(ctx: Context) {
            val i = Intent(ctx, FloatingService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i)
            else ctx.startService(i)
        }

        fun stop(ctx: Context) {
            val i = Intent(ctx, FloatingService::class.java).apply { action = ACTION_STOP }
            ctx.startService(i)
        }
    }
}

private class BubbleGestureListener(
    private val lp: WindowManager.LayoutParams,
    private val onTap: () -> Unit,
    private val onDoubleTap: () -> Unit,
    private val onMoved: () -> Unit,
) : View.OnTouchListener {

    private var downX = 0f
    private var downY = 0f
    private var startX = 0
    private var startY = 0
    private var downTime = 0L
    private var lastTapTime = 0L
    private var moved = false
    private val tapSlop = 16
    private val doubleTapMs = 300L
    private val tapMaxMs = 500L

    override fun onTouch(v: View, e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downX = e.rawX; downY = e.rawY
                startX = lp.x; startY = lp.y
                downTime = System.currentTimeMillis()
                moved = false
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = (e.rawX - downX).toInt()
                val dy = (e.rawY - downY).toInt()
                if (!moved && (abs(dx) > tapSlop || abs(dy) > tapSlop)) moved = true
                if (moved) {
                    lp.x = startX + dx
                    lp.y = startY + dy
                    onMoved()
                }
            }
            MotionEvent.ACTION_UP -> {
                if (!moved && System.currentTimeMillis() - downTime < tapMaxMs) {
                    val now = System.currentTimeMillis()
                    if (now - lastTapTime < doubleTapMs) {
                        lastTapTime = 0
                        onDoubleTap()
                    } else {
                        lastTapTime = now
                        v.postDelayed({
                            if (lastTapTime == now) onTap()
                        }, doubleTapMs + 10)
                    }
                }
            }
        }
        return true
    }
}
