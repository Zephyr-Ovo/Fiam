package cc.fiet.favilla

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

sealed class ChatMessage {
    abstract val id: String

    data class User(override val id: String, val text: String) : ChatMessage()
    data class Assistant(
        override val id: String,
        val text: String,
        val thoughts: List<String> = emptyList(),
        val cotLocked: Boolean = false,
        val cotIntent: String = "default",
        val thoughtsExpanded: Boolean = false,
    ) : ChatMessage()
    data class Recall(override val id: String, val text: String, val collapsed: Boolean = false) : ChatMessage()
    data class System(override val id: String, val text: String) : ChatMessage()
    data class Typing(override val id: String = "typing") : ChatMessage()
}

class ChatViewModel : ViewModel() {

    val sessionId: String = "app-" + UUID.randomUUID().toString().substring(0, 8) +
        "-" + (System.currentTimeMillis() / 1000).toString()

    private val _messages = MutableStateFlow<List<ChatMessage>>(emptyList())
    val messages: StateFlow<List<ChatMessage>> = _messages.asStateFlow()

    private val _sending = MutableStateFlow(false)
    val sending: StateFlow<Boolean> = _sending.asStateFlow()

    fun appendUser(text: String): String {
        val id = UUID.randomUUID().toString()
        _messages.value = _messages.value + ChatMessage.User(id, text)
        return id
    }

    fun appendAssistant(
        text: String,
        thoughts: List<String> = emptyList(),
        cotLocked: Boolean = false,
        cotIntent: String = "default",
    ) {
        val id = UUID.randomUUID().toString()
        _messages.value = _messages.value + ChatMessage.Assistant(
            id = id,
            text = text,
            thoughts = thoughts,
            cotLocked = cotLocked,
            cotIntent = cotIntent,
        )
    }

    fun toggleThoughts(id: String) {
        _messages.value = _messages.value.map {
            if (it is ChatMessage.Assistant && it.id == id) it.copy(thoughtsExpanded = !it.thoughtsExpanded) else it
        }
    }

    fun appendRecall(text: String) {
        val id = UUID.randomUUID().toString()
        _messages.value = _messages.value + ChatMessage.Recall(id, text)
    }

    fun appendSystem(text: String) {
        val id = UUID.randomUUID().toString()
        _messages.value = _messages.value + ChatMessage.System(id, text)
    }

    fun startTyping() {
        if (_messages.value.lastOrNull() is ChatMessage.Typing) return
        _messages.value = _messages.value + ChatMessage.Typing()
        _sending.value = true
    }

    fun stopTyping() {
        _messages.value = _messages.value.filterNot { it is ChatMessage.Typing }
        _sending.value = false
    }

    fun toggleRecall(id: String) {
        _messages.value = _messages.value.map {
            if (it is ChatMessage.Recall && it.id == id) it.copy(collapsed = !it.collapsed) else it
        }
    }

    fun clear() {
        _messages.value = emptyList()
    }
}
