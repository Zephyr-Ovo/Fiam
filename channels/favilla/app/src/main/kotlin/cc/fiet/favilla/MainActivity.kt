package cc.fiet.favilla

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.chatBackend
import cc.fiet.favilla.Prefs.customApiUrl
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import cc.fiet.favilla.databinding.ActivityMainBinding
import kotlinx.coroutines.launch
import java.util.UUID

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding
    private val chatLog = StringBuilder()
    private val chatSessionId: String by lazy {
        "app-" + UUID.randomUUID().toString().substring(0, 8) +
            "-" + (System.currentTimeMillis() / 1000).toString()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.etUrl.setText(serverUrl)
        b.etToken.setText(ingestToken)
        b.etSource.setText(sourceTag)
        b.etCustomApiUrl.setText(customApiUrl)
        if (chatBackend == "api") b.rbBackendApi.isChecked = true else b.rbBackendCc.isChecked = true
        resetChatLog()

        b.btnSave.setOnClickListener {
            saveSettings()
            toast("saved")
        }

        b.btnSendChat.setOnClickListener {
            sendChatMessage()
        }

        b.btnTest.setOnClickListener {
            saveSettings()
            b.btnTest.isEnabled = false
            b.tvStatus.text = "loading stats..."
            lifecycleScope.launch {
                val r = CaptureClient.appStatus(
                    serverUrl = serverUrl,
                    token = ingestToken,
                )
                b.tvStatus.text = if (r.ok) r.summary else "✗ ${r.error}"
                b.btnTest.isEnabled = true
            }
        }

        b.btnStartReadalong.setOnClickListener {
            saveSettings()
            if (ingestToken.isBlank()) {
                toast("Favilla: set token first")
                return@setOnClickListener
            }
            if (!Settings.canDrawOverlays(this)) {
                toast(getString(R.string.need_overlay_perm))
                return@setOnClickListener
            }
            val sessionId = FloatingService.newSessionId()
            FloatingService.start(this, sessionId)
            sendInteractionPhase("start", "开始共读邀请", sessionId)
            toast("readalong: started")
        }

        b.btnEndReadalong.setOnClickListener {
            saveSettings()
            val sessionId = FloatingService.sessionId
            sendInteractionPhase("end", "结束共读", sessionId)
            FloatingService.stop(this)
            toast(if (sessionId == null) "readalong: stopped" else "readalong: ended")
        }

        b.btnOverlayPerm.setOnClickListener {
            runCatching {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:$packageName"),
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
        }

        b.btnA11ySettings.setOnClickListener {
            runCatching {
                startActivity(
                    Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
        }

        b.btnStartBubble.setOnClickListener {
            if (!Settings.canDrawOverlays(this)) {
                toast(getString(R.string.need_overlay_perm))
                return@setOnClickListener
            }
            FloatingService.start(this)
            toast("bubble: started")
        }

        b.btnStopBubble.setOnClickListener {
            FloatingService.stop(this)
            toast("bubble: stopped")
        }

        b.tvHint.visibility = View.VISIBLE
    }

    override fun onResume() {
        super.onResume()
        refreshPermStatus()
    }

    private fun refreshPermStatus() {
        val overlay = if (Settings.canDrawOverlays(this))
            getString(R.string.status_overlay_ok) else getString(R.string.status_overlay_missing)
        val a11y = if (isA11yEnabled())
            getString(R.string.status_a11y_ok) else getString(R.string.status_a11y_missing)
        b.tvPermStatus.text = "$overlay\n$a11y"
    }

    private fun isA11yEnabled(): Boolean {
        val want = "$packageName/${FavillaAccessibilityService::class.java.name}"
        val enabled = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return enabled.split(':').any { it.equals(want, ignoreCase = true) }
    }

    private fun saveSettings() {
        serverUrl = b.etUrl.text.toString()
        ingestToken = b.etToken.text.toString()
        sourceTag = b.etSource.text.toString()
        customApiUrl = b.etCustomApiUrl.text.toString()
        chatBackend = if (b.rbBackendApi.isChecked) "api" else "cc"
    }

    private fun resetChatLog() {
        chatLog.clear()
        chatLog.append("Fiet: ready.")
        b.tvChatLog.text = chatLog.toString()
    }

    private fun appendChat(speaker: String, text: String) {
        if (chatLog.isNotEmpty()) chatLog.append("\n\n")
        chatLog.append(speaker).append(": ").append(text.trim())
        b.tvChatLog.text = chatLog.toString()
    }

    private fun sendChatMessage() {
        saveSettings()
        val text = b.etChatMessage.text.toString().trim()
        if (text.isBlank()) return
        val backend = chatBackend
        if (backend == "cc" && ingestToken.isBlank()) {
            toast("Favilla: set token first")
            return
        }
        if (backend == "api" && customApiUrl.isBlank()) {
            toast("Favilla: set custom API URL first")
            return
        }
        appendChat("Zephyr", text)
        b.etChatMessage.setText("")
        b.btnSendChat.isEnabled = false
        b.tvStatus.text = "sending to $backend..."
        lifecycleScope.launch {
            val result = if (backend == "api") {
                CaptureClient.chatCustom(
                    endpointUrl = customApiUrl,
                    token = ingestToken,
                    text = text,
                    source = sourceTag,
                    sessionId = chatSessionId,
                )
            } else {
                CaptureClient.chatCc(
                    serverUrl = serverUrl,
                    token = ingestToken,
                    text = text,
                    source = sourceTag,
                    sessionId = chatSessionId,
                )
            }
            if (result.ok) {
                result.recall?.let { appendChat("Fiet recall", it) }
                appendChat("Fiet", result.reply.ifBlank { "(no text returned)" })
                b.tvStatus.text = "sent via $backend"
            } else {
                appendChat("Favilla", "send failed: ${result.error ?: "unknown error"}")
                b.tvStatus.text = "send failed"
            }
            b.btnSendChat.isEnabled = true
        }
    }

    private fun sendInteractionPhase(phase: String, text: String, sessionIdOverride: String? = null) {
        val token = ingestToken
        if (token.isBlank()) {
            toast("Favilla: set token first")
            return
        }
        val sessionId = sessionIdOverride ?: FloatingService.sessionId ?: ("weread-" + System.currentTimeMillis())
        lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = serverUrl,
                token = token,
                text = text,
                source = "$sourceTag:readalong",
                tags = listOf("interaction", "weread", phase),
                kind = "interaction",
                interaction = "weread",
                sessionId = sessionId,
                phase = phase,
            )
        }
    }

    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
