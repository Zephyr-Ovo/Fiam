package cc.fiet.favilla

import android.content.Context
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Phone-side toggle: signals "this device is currently reachable by Fiet via the
 * desktop ADB bridge." The actual driving happens off-device (scrcpy/adb on the
 * desktop). This object only persists state and announces it to the server so
 * the desktop side can react.
 */
object AiBridge {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Volatile
    var isActive: Boolean = false
        private set

    fun statusLabel(@Suppress("UNUSED_PARAMETER") ctx: Context): String =
        if (isActive) "bridge: armed (ADB on desktop can drive)" else "bridge: idle"

    fun setActive(ctx: Context, active: Boolean) {
        isActive = active
        val token = ctx.ingestToken
        if (token.isBlank()) return
        val url = ctx.serverUrl
        val src = ctx.sourceTag
        scope.launch {
            runCatching {
                CaptureClient.send(
                    serverUrl = url,
                    token = token,
                    text = if (active) "[ai-bridge] armed" else "[ai-bridge] released",
                    source = "$src:bridge",
                    tags = listOf("ai-bridge", if (active) "armed" else "released"),
                    kind = "control",
                )
            }
        }
    }
}
