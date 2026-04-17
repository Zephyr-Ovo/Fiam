package cc.fiet.favilla

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Silent accessibility service.
 *
 * Declares no event subscriptions and produces no audio / haptic / visual feedback.
 * Its only job is to stay connected so that, when the user taps the floating
 * bubble, we can synchronously pull the currently-selected text out of whatever
 * app is in the foreground via [readSelection].
 *
 * Nothing is logged, persisted, or sent anywhere by this class itself.
 */
class FavillaAccessibilityService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        // Re-assert minimal config at runtime (xml config is authoritative, this is defensive).
        serviceInfo = AccessibilityServiceInfo().apply {
            eventTypes = 0
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.DEFAULT
            notificationTimeout = 0
            packageNames = null
        }
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // intentionally empty; we pull on demand, never react to events
    }

    override fun onInterrupt() { /* no-op */ }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    /** Walk the active window's node tree and return the currently selected substring, if any. */
    fun readSelection(): String? {
        val root = rootInActiveWindow ?: return null
        return try { findSelection(root) } catch (_: Throwable) { null }
    }

    private fun findSelection(n: AccessibilityNodeInfo): String? {
        // Most editable text fields expose textSelectionStart / End.
        val start = n.textSelectionStart
        val end = n.textSelectionEnd
        val text = n.text?.toString()
        if (text != null && start in 0 until end && end <= text.length && end - start > 0) {
            return text.substring(start, end)
        }
        for (i in 0 until n.childCount) {
            val child = n.getChild(i) ?: continue
            val r = findSelection(child)
            if (!r.isNullOrEmpty()) return r
        }
        return null
    }

    companion object {
        @Volatile
        var instance: FavillaAccessibilityService? = null
            private set

        fun currentSelection(): String? = instance?.readSelection()
        fun isEnabled(): Boolean = instance != null
    }
}
