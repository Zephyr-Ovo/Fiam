package cc.fiet.favilla

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.drawable.BitmapDrawable
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
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
            voiceStatus?.text = "voice capture coming soon — held safe in queue"
            Toast.makeText(ctx, "Voice queued (stub).", Toast.LENGTH_SHORT).show()
        }

        view.findViewById<MaterialButton>(R.id.btnPickImage).setOnClickListener {
            pickImage.launch("image/*")
        }

        // Stickers from assets/stickers/*.png
        val stickers = loadStickers()
        val rv: RecyclerView = view.findViewById(R.id.rvStickers)
        val empty: TextView = view.findViewById(R.id.tvStickersEmpty)
        if (stickers.isEmpty()) {
            empty.visibility = View.VISIBLE
            rv.visibility = View.GONE
        } else {
            empty.visibility = View.GONE
            rv.visibility = View.VISIBLE
            rv.layoutManager = GridLayoutManager(requireContext(), 4)
            rv.adapter = StickerAdapter(stickers) { name -> sendSticker(name) }
        }
    }

    private fun loadStickers(): List<String> {
        val am = context?.assets ?: return emptyList()
        return runCatching {
            (am.list("stickers") ?: emptyArray()).filter {
                it.endsWith(".png", true) || it.endsWith(".webp", true) || it.endsWith(".jpg", true)
            }.sorted()
        }.getOrDefault(emptyList())
    }

    private fun sendSticker(name: String) {
        val ctx = context ?: return
        vm.appendUser("[sticker] $name")
        Toast.makeText(ctx, "sticker queued: $name", Toast.LENGTH_SHORT).show()
        viewLifecycleOwner.lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = ctx.serverUrl,
                token = ctx.ingestToken,
                text = "[sticker:$name]",
                source = ctx.sourceTag,
                tags = listOf("sticker"),
            )
        }
    }

    private fun sendImage(uri: Uri) {
        val ctx = context ?: return
        vm.appendUser("[image] ${uri.lastPathSegment ?: uri}")
        Toast.makeText(ctx, "image queued (metadata only).", Toast.LENGTH_SHORT).show()
        viewLifecycleOwner.lifecycleScope.launch {
            CaptureClient.send(
                serverUrl = ctx.serverUrl,
                token = ctx.ingestToken,
                text = "[image] ${uri}",
                source = ctx.sourceTag,
                tags = listOf("image"),
            )
        }
    }

    private class StickerAdapter(
        private val items: List<String>,
        private val onClick: (String) -> Unit,
    ) : RecyclerView.Adapter<StickerAdapter.VH>() {
        class VH(v: View) : RecyclerView.ViewHolder(v) {
            val iv: ImageView = v.findViewById(R.id.ivSticker)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            val v = LayoutInflater.from(parent.context).inflate(R.layout.item_sticker, parent, false)
            return VH(v)
        }

        override fun onBindViewHolder(holder: VH, position: Int) {
            val name = items[position]
            val ctx = holder.itemView.context
            runCatching {
                ctx.assets.open("stickers/$name").use { stream ->
                    holder.iv.setImageDrawable(
                        BitmapDrawable(ctx.resources, BitmapFactory.decodeStream(stream)),
                    )
                }
            }
            holder.itemView.setOnClickListener { onClick(name) }
        }

        override fun getItemCount(): Int = items.size
    }
}
