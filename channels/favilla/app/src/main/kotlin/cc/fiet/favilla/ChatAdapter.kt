package cc.fiet.favilla

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView

class ChatAdapter(
    private val onRecallToggle: (String) -> Unit,
    private val onThoughtsToggle: (String) -> Unit = {},
) : ListAdapter<ChatMessage, RecyclerView.ViewHolder>(DIFF) {

    override fun getItemViewType(position: Int): Int = when (getItem(position)) {
        is ChatMessage.User -> TYPE_USER
        is ChatMessage.Assistant -> TYPE_ASSISTANT
        is ChatMessage.Recall -> TYPE_RECALL
        is ChatMessage.System -> TYPE_SYSTEM
        is ChatMessage.Typing -> TYPE_TYPING
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_USER -> UserVH(inflater.inflate(R.layout.item_chat_user, parent, false))
            TYPE_ASSISTANT -> AssistantVH(inflater.inflate(R.layout.item_chat_assistant, parent, false), onThoughtsToggle)
            TYPE_RECALL -> RecallVH(inflater.inflate(R.layout.item_chat_recall, parent, false), onRecallToggle)
            TYPE_SYSTEM -> SystemVH(inflater.inflate(R.layout.item_chat_system, parent, false))
            TYPE_TYPING -> TypingVH(inflater.inflate(R.layout.item_chat_typing, parent, false))
            else -> error("unknown viewType $viewType")
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val msg = getItem(position)) {
            is ChatMessage.User -> (holder as UserVH).bind(msg)
            is ChatMessage.Assistant -> (holder as AssistantVH).bind(msg)
            is ChatMessage.Recall -> (holder as RecallVH).bind(msg)
            is ChatMessage.System -> (holder as SystemVH).bind(msg)
            is ChatMessage.Typing -> (holder as TypingVH).bind()
        }
    }

    class UserVH(v: View) : RecyclerView.ViewHolder(v) {
        private val tv: TextView = v.findViewById(R.id.tvText)
        fun bind(m: ChatMessage.User) { tv.text = m.text }
    }

    class AssistantVH(v: View, private val onToggle: (String) -> Unit) : RecyclerView.ViewHolder(v) {
        private val tv: TextView = v.findViewById(R.id.tvText)
        private val cotToggle: TextView = v.findViewById(R.id.tvCotToggle)
        private val tvThoughts: TextView = v.findViewById(R.id.tvThoughts)
        fun bind(m: ChatMessage.Assistant) {
            tv.text = m.text
            val hasThoughts = m.thoughts.isNotEmpty()
            val locked = m.cotLocked && !hasThoughts
            when {
                hasThoughts -> {
                    cotToggle.visibility = View.VISIBLE
                    cotToggle.text = if (m.thoughtsExpanded) "\uD83D\uDCAD hide thinking" else "\uD83D\uDCAD show thinking"
                    cotToggle.setOnClickListener { onToggle(m.id) }
                    tvThoughts.visibility = if (m.thoughtsExpanded) View.VISIBLE else View.GONE
                    tvThoughts.text = m.thoughts.joinToString("\n\n")
                }
                locked -> {
                    cotToggle.visibility = View.VISIBLE
                    cotToggle.text = "\uD83D\uDD12 thinking withheld this turn"
                    cotToggle.setOnClickListener(null)
                    tvThoughts.visibility = View.GONE
                }
                else -> {
                    cotToggle.visibility = View.GONE
                    tvThoughts.visibility = View.GONE
                    cotToggle.setOnClickListener(null)
                }
            }
        }
    }

    class RecallVH(v: View, private val onToggle: (String) -> Unit) : RecyclerView.ViewHolder(v) {
        private val tv: TextView = v.findViewById(R.id.tvText)
        private val toggle: TextView = v.findViewById(R.id.tvToggle)
        fun bind(m: ChatMessage.Recall) {
            tv.text = m.text
            tv.visibility = if (m.collapsed) View.GONE else View.VISIBLE
            toggle.text = if (m.collapsed) "show" else "hide"
            toggle.setOnClickListener { onToggle(m.id) }
        }
    }

    class SystemVH(v: View) : RecyclerView.ViewHolder(v) {
        private val tv: TextView = v.findViewById(R.id.tvText)
        fun bind(m: ChatMessage.System) { tv.text = m.text }
    }

    class TypingVH(v: View) : RecyclerView.ViewHolder(v) {
        private val dots = listOf<View>(
            v.findViewById(R.id.dot1),
            v.findViewById(R.id.dot2),
            v.findViewById(R.id.dot3),
        )
        fun bind() {
            dots.forEachIndexed { i, dot ->
                dot.alpha = 0.3f
                dot.animate().cancel()
                dot.animate()
                    .alpha(1f)
                    .setStartDelay((i * 180L))
                    .setDuration(420L)
                    .withEndAction {
                        dot.animate().alpha(0.3f).setDuration(420L).start()
                    }
                    .start()
            }
        }
    }

    companion object {
        private const val TYPE_USER = 1
        private const val TYPE_ASSISTANT = 2
        private const val TYPE_RECALL = 3
        private const val TYPE_SYSTEM = 4
        private const val TYPE_TYPING = 5

        private val DIFF = object : DiffUtil.ItemCallback<ChatMessage>() {
            override fun areItemsTheSame(a: ChatMessage, b: ChatMessage) = a.id == b.id
            override fun areContentsTheSame(a: ChatMessage, b: ChatMessage) = a == b
        }
    }
}
