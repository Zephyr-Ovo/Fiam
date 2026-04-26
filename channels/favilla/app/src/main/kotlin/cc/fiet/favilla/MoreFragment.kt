package cc.fiet.favilla

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.chatBackend
import cc.fiet.favilla.Prefs.customApiUrl
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import cc.fiet.favilla.Prefs.sttApiKey
import cc.fiet.favilla.Prefs.sttApiUrl
import cc.fiet.favilla.Prefs.ttsApiKey
import cc.fiet.favilla.Prefs.ttsApiUrl
import cc.fiet.favilla.Prefs.visionApiKey
import cc.fiet.favilla.Prefs.visionApiUrl
import cc.fiet.favilla.Prefs.visionModel
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.launch

class MoreFragment : Fragment() {

    private var permView: TextView? = null

    private val requestCamera =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { refreshPerms() }
    private val requestMic =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { refreshPerms() }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_more, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val ctx = requireContext()

        val etUrl: TextInputEditText = view.findViewById(R.id.etUrl)
        val etToken: TextInputEditText = view.findViewById(R.id.etToken)
        val etSource: TextInputEditText = view.findViewById(R.id.etSource)
        val etCustom: TextInputEditText = view.findViewById(R.id.etCustomApiUrl)
        val etVisionUrl: TextInputEditText = view.findViewById(R.id.etVisionUrl)
        val etVisionKey: TextInputEditText = view.findViewById(R.id.etVisionKey)
        val etVisionModel: TextInputEditText = view.findViewById(R.id.etVisionModel)
        val etSttUrl: TextInputEditText = view.findViewById(R.id.etSttUrl)
        val etSttKey: TextInputEditText = view.findViewById(R.id.etSttKey)
        val etTtsUrl: TextInputEditText = view.findViewById(R.id.etTtsUrl)
        val etTtsKey: TextInputEditText = view.findViewById(R.id.etTtsKey)
        val rg: RadioGroup = view.findViewById(R.id.rgBackend)
        val rbCc: RadioButton = view.findViewById(R.id.rbBackendCc)
        val rbApi: RadioButton = view.findViewById(R.id.rbBackendApi)

        etUrl.setText(ctx.serverUrl)
        etToken.setText(ctx.ingestToken)
        etSource.setText(ctx.sourceTag)
        etCustom.setText(ctx.customApiUrl)
        etVisionUrl.setText(ctx.visionApiUrl)
        etVisionKey.setText(ctx.visionApiKey)
        etVisionModel.setText(ctx.visionModel)
        etSttUrl.setText(ctx.sttApiUrl)
        etSttKey.setText(ctx.sttApiKey)
        etTtsUrl.setText(ctx.ttsApiUrl)
        etTtsKey.setText(ctx.ttsApiKey)
        if (ctx.chatBackend == "api") rbApi.isChecked = true else rbCc.isChecked = true

        view.findViewById<MaterialButton>(R.id.btnSave).setOnClickListener {
            ctx.serverUrl = etUrl.text.toString()
            ctx.ingestToken = etToken.text.toString()
            ctx.sourceTag = etSource.text.toString()
            ctx.customApiUrl = etCustom.text.toString()
            ctx.visionApiUrl = etVisionUrl.text.toString()
            ctx.visionApiKey = etVisionKey.text.toString()
            ctx.visionModel = etVisionModel.text.toString()
            ctx.sttApiUrl = etSttUrl.text.toString()
            ctx.sttApiKey = etSttKey.text.toString()
            ctx.ttsApiUrl = etTtsUrl.text.toString()
            ctx.ttsApiKey = etTtsKey.text.toString()
            ctx.chatBackend = if (rbApi.isChecked) "api" else "cc"
            Toast.makeText(ctx, "saved", Toast.LENGTH_SHORT).show()
        }

        view.findViewById<MaterialButton>(R.id.btnOverlay).setOnClickListener {
            runCatching {
                startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:${ctx.packageName}"),
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
        }
        view.findViewById<MaterialButton>(R.id.btnA11y).setOnClickListener {
            runCatching {
                startActivity(
                    Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
        }
        view.findViewById<MaterialButton>(R.id.btnCamera).setOnClickListener {
            requestCamera.launch(Manifest.permission.CAMERA)
        }
        view.findViewById<MaterialButton>(R.id.btnMic).setOnClickListener {
            requestMic.launch(Manifest.permission.RECORD_AUDIO)
        }

        view.findViewById<MaterialButton>(R.id.btnStartBubble).setOnClickListener {
            if (!Settings.canDrawOverlays(ctx)) {
                Toast.makeText(ctx, R.string.need_overlay_perm, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            FloatingService.start(ctx)
            Toast.makeText(ctx, "bubble started", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<MaterialButton>(R.id.btnStopBubble).setOnClickListener {
            FloatingService.stop(ctx)
        }
        view.findViewById<MaterialButton>(R.id.btnStartReadalong).setOnClickListener {
            if (!Settings.canDrawOverlays(ctx)) {
                Toast.makeText(ctx, R.string.need_overlay_perm, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            val sessionId = FloatingService.newSessionId()
            FloatingService.start(ctx, sessionId)
            sendInteractionPhase("start", "开始共读邀请", sessionId)
            Toast.makeText(ctx, "readalong started", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<MaterialButton>(R.id.btnEndReadalong).setOnClickListener {
            val sessionId = FloatingService.sessionId
            sendInteractionPhase("end", "结束共读", sessionId)
            FloatingService.stop(ctx)
        }

        permView = view.findViewById(R.id.tvPermStatus)
        refreshPerms()
    }

    override fun onResume() {
        super.onResume()
        refreshPerms()
    }

    private fun refreshPerms() {
        val ctx = context ?: return
        val overlay = if (Settings.canDrawOverlays(ctx))
            getString(R.string.status_overlay_ok) else getString(R.string.status_overlay_missing)
        val a11y = if (isA11yEnabled(ctx))
            getString(R.string.status_a11y_ok) else getString(R.string.status_a11y_missing)
        val cam = if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED)
            getString(R.string.status_camera_ok) else getString(R.string.status_camera_missing)
        val mic = if (ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED)
            getString(R.string.status_mic_ok) else getString(R.string.status_mic_missing)
        permView?.text = "$overlay\n$a11y\n$cam\n$mic"
    }

    private fun isA11yEnabled(ctx: android.content.Context): Boolean {
        val want = "${ctx.packageName}/${FavillaAccessibilityService::class.java.name}"
        val enabled = Settings.Secure.getString(
            ctx.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return enabled.split(':').any { it.equals(want, ignoreCase = true) }
    }

    private fun sendInteractionPhase(phase: String, text: String, sessionIdOverride: String?) {
        val ctx = context ?: return
        if (ctx.ingestToken.isBlank()) {
            Toast.makeText(ctx, "Set token first", Toast.LENGTH_SHORT).show(); return
        }
        val sessionId = sessionIdOverride ?: FloatingService.sessionId
            ?: ("weread-" + System.currentTimeMillis())
        viewLifecycleOwner.lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = ctx.serverUrl,
                token = ctx.ingestToken,
                text = text,
                source = "${ctx.sourceTag}:readalong",
                tags = listOf("interaction", "weread", phase),
                kind = "interaction",
                interaction = "weread",
                sessionId = sessionId,
                phase = phase,
            )
        }
    }
}
