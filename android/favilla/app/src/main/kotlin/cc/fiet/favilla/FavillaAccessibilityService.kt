package cc.fiet.favilla

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Silent accessibility service.
 *
 * Subscribes ONLY to text-selection-change (and a few related) events so we can
 * remember the user's most recent selection. Tapping the floating bubble would
 * otherwise clear the selection before we could read it back — so we cache it
 * the moment the user selects, and replay from cache when the bubble asks.
 *
 * No audio, no persistence, no network. The cache is in memory only.
 */
class FavillaAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        serviceInfo = AccessibilityServiceInfo().apply {
            eventTypes = AccessibilityEvent.TYPE_VIEW_TEXT_SELECTION_CHANGED or
                AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED or
                AccessibilityEvent.TYPE_VIEW_CLICKED or
                AccessibilityEvent.TYPE_VIEW_LONG_CLICKED or
                AccessibilityEvent.TYPE_VIEW_FOCUSED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.DEFAULT
            notificationTimeout = 0
            packageNames = null
        }
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        event ?: return
        val pkg = event.packageName?.toString().orEmpty()
        if (pkg == packageName) return // ignore our own bubble / UI

        // 1) The event often carries the selection range + text directly.
        val joined = event.text?.joinToString("") { it?.toString().orEmpty() }.orEmpty()
        val from = event.fromIndex
        val to = event.toIndex
        if (joined.isNotEmpty() && from in 0 until to && to <= joined.length) {
            val sub = joined.substring(from, to).trim()
            if (sub.isNotEmpty()) {
                cache(sub, pkg); return
            }
        }

        // 2) Otherwise inspect the source node and its children.
        val src = event.source ?: return
        val picked = try { findSelectionIn(src) } catch (_: Throwable) { null }
        if (!picked.isNullOrBlank()) cache(picked.trim(), pkg)
    }

    override fun onInterrupt() { /* no-op */ }

    override fun onDestroy() {
        if (instance === this) instance = null
        lastSelection = null
        super.onDestroy()
    }

    private fun cache(text: String, pkg: String) {
        lastSelection = Selection(text = text, pkg = pkg, at = System.currentTimeMillis())
    }

    /** Walk the active window's tree right now; used as last-resort fallback. */
    fun readActiveSelection(): String? {
        val root = rootInActiveWindow ?: return null
        return try { findSelectionIn(root) } catch (_: Throwable) { null }
    }

    private fun findSelectionIn(n: AccessibilityNodeInfo): String? {
        val start = n.textSelectionStart
        val end = n.textSelectionEnd
        val text = n.text?.toString()
        if (text != null && start in 0 until end && end <= text.length && end - start > 0) {
            return text.substring(start, end)
        }
        for (i in 0 until n.childCount) {
            val child = n.getChild(i) ?: continue
            val r = findSelectionIn(child)
            if (!r.isNullOrEmpty()) return r
        }
        return null
    }

    data class Selection(val text: String, val pkg: String, val at: Long)

    companion object {
        @Volatile
        var instance: FavillaAccessibilityService? = null
            private set

        @Volatile
        var lastSelection: Selection? = null
            private set

        /** Cached selection is considered fresh for this long. */
        private const val MAX_AGE_MS = 60_000L

        /** Returns fresh cached selection, else polls the active window. */
        fun currentSelection(): String? {
            val cached = lastSelection
            if (cached != null && System.currentTimeMillis() - cached.at < MAX_AGE_MS) {
                return cached.text
            }
            return instance?.readActiveSelection()
        }

        /** Called after a successful send so the next bubble tap won't reuse stale text. */
        fun clearCache() { lastSelection = null }

        fun isEnabled(): Boolean = instance != null
    }
}
