package cc.fiet.favilla

import android.app.NotificationManager
import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import cc.fiet.favilla.Prefs.chatBackend
import cc.fiet.favilla.Prefs.customApiUrl
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

class ChatFragment : Fragment() {

    private val vm: ChatViewModel by activityViewModels()
    private lateinit var adapter: ChatAdapter
    private var rv: RecyclerView? = null
    private var et: EditText? = null
    private var subtitle: TextView? = null
    private var emptyView: View? = null
    private var visible: Boolean = false

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_chat, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        rv = view.findViewById(R.id.rvMessages)
        et = view.findViewById(R.id.etMessage)
        subtitle = view.findViewById(R.id.tvHeaderSubtitle)
        emptyView = view.findViewById(R.id.empty)

        adapter = ChatAdapter(onRecallToggle = { vm.toggleRecall(it) })
        rv?.layoutManager = LinearLayoutManager(requireContext()).apply { stackFromEnd = true }
        rv?.adapter = adapter

        view.findViewById<ImageButton>(R.id.btnSend).setOnClickListener { send() }
        view.findViewById<ImageButton>(R.id.btnAttach).setOnClickListener {
            Toast.makeText(requireContext(), "Use Hub → pick image to send", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<ImageButton>(R.id.btnMic).setOnClickListener {
            Toast.makeText(requireContext(), "Use Hub → voice to record", Toast.LENGTH_SHORT).show()
        }
        view.findViewById<MaterialButton>(R.id.btnNew).setOnClickListener {
            vm.clear()
        }

        viewLifecycleOwner.lifecycleScope.launch {
            vm.messages.collectLatest { msgs ->
                adapter.submitList(msgs.toList()) {
                    rv?.scrollToPosition(maxOf(0, adapter.itemCount - 1))
                }
                emptyView?.visibility = if (msgs.isEmpty()) View.VISIBLE else View.GONE
                rv?.visibility = if (msgs.isEmpty()) View.GONE else View.VISIBLE
            }
        }

        refreshSubtitle()
    }

    override fun onResume() {
        super.onResume()
        visible = true
        refreshSubtitle()
    }

    override fun onPause() {
        visible = false
        super.onPause()
    }

    private fun refreshSubtitle() {
        val ctx = context ?: return
        subtitle?.text = if (ctx.chatBackend == "api")
            getString(R.string.chat_subtitle_api) else getString(R.string.chat_subtitle_cc)
    }

    private fun send() {
        val ctx = context ?: return
        val text = et?.text?.toString()?.trim().orEmpty()
        if (text.isBlank()) return
        val backend = ctx.chatBackend
        if (backend == "cc" && ctx.ingestToken.isBlank()) {
            Toast.makeText(ctx, "Set token in More first", Toast.LENGTH_SHORT).show(); return
        }
        if (backend == "api" && ctx.customApiUrl.isBlank()) {
            Toast.makeText(ctx, "Set custom API URL in More first", Toast.LENGTH_SHORT).show(); return
        }
        vm.appendUser(text)
        et?.setText("")
        vm.startTyping()

        viewLifecycleOwner.lifecycleScope.launch {
            val result = if (backend == "api") {
                CaptureClient.chatCustom(
                    endpointUrl = ctx.customApiUrl,
                    token = ctx.ingestToken,
                    text = text,
                    source = ctx.sourceTag,
                    sessionId = vm.sessionId,
                )
            } else {
                CaptureClient.chatCc(
                    serverUrl = ctx.serverUrl,
                    token = ctx.ingestToken,
                    text = text,
                    source = ctx.sourceTag,
                    sessionId = vm.sessionId,
                )
            }
            vm.stopTyping()
            if (result.ok) {
                result.recall?.let { vm.appendRecall(it) }
                val reply = result.reply.ifBlank { "(no text returned)" }
                vm.appendAssistant(reply)
                if (!visible) postReplyNotification(reply)
            } else {
                vm.appendSystem("send failed: ${result.error ?: "unknown"}")
            }
        }
    }

    private fun postReplyNotification(text: String) {
        val ctx = context ?: return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return
        val n = NotificationCompat.Builder(ctx, MainActivity.REPLY_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_spark)
            .setContentTitle(getString(R.string.notif_reply_title))
            .setContentText(text.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        runCatching { nm.notify(MainActivity.REPLY_NOTIF_ID, n) }
    }
}
