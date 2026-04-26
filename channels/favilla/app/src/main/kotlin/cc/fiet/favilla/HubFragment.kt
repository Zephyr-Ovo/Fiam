package cc.fiet.favilla

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.GridLayout
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import com.google.android.material.button.MaterialButton
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.launch

class HubFragment : Fragment() {

    private val vm: ChatViewModel by activityViewModels()
    private var aiStatus: TextView? = null
    private var voiceStatus: TextView? = null

    private val pickImage =
        registerForActivityResult(ActivityResultContracts.GetContent()) { uri: Uri? ->
            if (uri != null) sendImage(uri)
        }

    private val requestMic =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            voiceStatus?.text = if (granted) "ready: tap to record" else "microphone denied"
        }

    private data class QuickAction(
        val iconRes: Int,
        val labelRes: Int,
        val marker: String,
        val tag: String,
    )

    private val quickActions: List<QuickAction> by lazy {
        listOf(
            QuickAction(R.drawable.ic_home, R.string.qa_home, "回家", "home"),
            QuickAction(R.drawable.ic_calendar, R.string.qa_calendar, "看日程", "calendar"),
            QuickAction(R.drawable.ic_clock, R.string.qa_clock, "开始计时", "clock"),
            QuickAction(R.drawable.ic_book, R.string.qa_book, "在阅读", "reading"),
            QuickAction(R.drawable.ic_todo, R.string.qa_todo, "待办", "todo"),
            QuickAction(R.drawable.ic_watch, R.string.qa_watch, "运动中", "fitness"),
            QuickAction(R.drawable.ic_dashboard, R.string.qa_dashboard, "查看状态", "dashboard"),
            QuickAction(R.drawable.ic_apps_add, R.string.qa_apps_add, "更多", "more"),
        )
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_hub, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        aiStatus = view.findViewById(R.id.tvAiStatus)
        voiceStatus = view.findViewById(R.id.tvVoiceStatus)

        val swAi: MaterialSwitch = view.findViewById(R.id.swAiControl)
        swAi.isChecked = AiBridge.isActive
        aiStatus?.text = AiBridge.statusLabel(requireContext())
        swAi.setOnCheckedChangeListener { _, checked ->
            AiBridge.setActive(requireContext(), checked)
            aiStatus?.text = AiBridge.statusLabel(requireContext())
        }

        view.findViewById<MaterialButton>(R.id.btnVoice).setOnClickListener {
            val ctx = requireContext()
            val granted = ContextCompat.checkSelfPermission(ctx, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestMic.launch(Manifest.permission.RECORD_AUDIO)
                return@setOnClickListener
            }
            voiceStatus?.text = "voice queued — STT channel not wired yet"
            Toast.makeText(ctx, "Voice queued (stub).", Toast.LENGTH_SHORT).show()
        }

        view.findViewById<MaterialButton>(R.id.btnPickImage).setOnClickListener {
            pickImage.launch("image/*")
        }

        val grid: GridLayout = view.findViewById(R.id.gridQuick)
        grid.removeAllViews()
        val inflater = LayoutInflater.from(requireContext())
        quickActions.forEach { qa ->
            val item = inflater.inflate(R.layout.item_quick_action, grid, false)
            item.findViewById<ImageView>(R.id.ivQa).setImageResource(qa.iconRes)
            item.findViewById<TextView>(R.id.tvQa).setText(qa.labelRes)
            val lp = GridLayout.LayoutParams(item.layoutParams).apply {
                width = 0
                height = GridLayout.LayoutParams.WRAP_CONTENT
                columnSpec = GridLayout.spec(GridLayout.UNDEFINED, 1, 1f)
                setGravity(Gravity.FILL_HORIZONTAL)
            }
            item.layoutParams = lp
            item.setOnClickListener { sendMarker(qa) }
            grid.addView(item)
        }
    }

    private fun sendMarker(qa: QuickAction) {
        val ctx = context ?: return
        val text = "[标记] ${qa.marker}"
        vm.appendUser(text)
        viewLifecycleOwner.lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = ctx.serverUrl,
                token = ctx.ingestToken,
                text = text,
                source = ctx.sourceTag,
                tags = listOf("marker", qa.tag),
                kind = "marker",
            )
        }
    }

    private fun sendImage(uri: Uri) {
        val ctx = context ?: return
        // Image content is routed to a separate vision API (configured in Settings).
        // The vision model produces a textual description that enters the flow as an
        // "action" beat (i.e. "the user looked at X"). The raw image is intentionally
        // NOT shipped to the main chat AI to save tokens.
        // TODO(vision): when Prefs.visionApiUrl is set, call it here and post the
        // description with kind="action" tags=["vision"]. For now we drop a placeholder
        // marker so backend can still see something happened.
        vm.appendUser("[image] ${uri.lastPathSegment ?: uri}")
        Toast.makeText(ctx, "image queued (vision pending).", Toast.LENGTH_SHORT).show()
        viewLifecycleOwner.lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = ctx.serverUrl,
                token = ctx.ingestToken,
                text = "[image] $uri",
                source = ctx.sourceTag,
                tags = listOf("image", "vision_pending"),
                kind = "action",
            )
        }
    }
}
