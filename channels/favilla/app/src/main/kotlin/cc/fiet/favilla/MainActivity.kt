package cc.fiet.favilla

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.accessibility.AccessibilityManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import cc.fiet.favilla.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.etUrl.setText(serverUrl)
        b.etToken.setText(ingestToken)
        b.etSource.setText(sourceTag)

        b.btnSave.setOnClickListener {
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
            toast("saved")
        }

        b.btnTest.setOnClickListener {
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
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
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
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
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
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
        val am = getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
        val want = "$packageName/${FavillaAccessibilityService::class.java.name}"
        val enabled = Settings.Secure.getString(
            contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return am.isEnabled && enabled.split(':').any { it.equals(want, ignoreCase = true) }
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
