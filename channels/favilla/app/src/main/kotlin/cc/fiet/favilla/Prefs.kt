package cc.fiet.favilla

import android.content.Context
import android.content.SharedPreferences

object Prefs {
    private const val FILE = "favilla_prefs"
    private const val K_URL = "server_url"
    private const val K_TOKEN = "ingest_token"
    private const val K_SOURCE = "source_tag"
    private const val K_CHAT_BACKEND = "chat_backend"
    private const val K_CUSTOM_API_URL = "custom_api_url"
    private const val K_VISION_URL = "vision_url"
    private const val K_VISION_KEY = "vision_key"
    private const val K_VISION_MODEL = "vision_model"
    private const val K_STT_URL = "stt_url"
    private const val K_STT_KEY = "stt_key"
    private const val K_TTS_URL = "tts_url"
    private const val K_TTS_KEY = "tts_key"

    private fun sp(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    var Context.serverUrl: String
        get() = sp(this).getString(K_URL, "https://fiet.cc") ?: "https://fiet.cc"
        set(v) { sp(this).edit().putString(K_URL, v.trim().trimEnd('/')).apply() }

    var Context.ingestToken: String
        get() = sp(this).getString(K_TOKEN, "") ?: ""
        set(v) { sp(this).edit().putString(K_TOKEN, v.trim()).apply() }

    var Context.sourceTag: String
        get() = sp(this).getString(K_SOURCE, "android") ?: "android"
        set(v) { sp(this).edit().putString(K_SOURCE, v.trim().ifEmpty { "android" }).apply() }

    var Context.chatBackend: String
        get() = sp(this).getString(K_CHAT_BACKEND, "cc") ?: "cc"
        set(v) { sp(this).edit().putString(K_CHAT_BACKEND, v.trim().ifEmpty { "cc" }).apply() }

    var Context.customApiUrl: String
        get() = sp(this).getString(K_CUSTOM_API_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_CUSTOM_API_URL, v.trim()).apply() }

    var Context.visionApiUrl: String
        get() = sp(this).getString(K_VISION_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_VISION_URL, v.trim().trimEnd('/')).apply() }

    var Context.visionApiKey: String
        get() = sp(this).getString(K_VISION_KEY, "") ?: ""
        set(v) { sp(this).edit().putString(K_VISION_KEY, v.trim()).apply() }

    var Context.visionModel: String
        get() = sp(this).getString(K_VISION_MODEL, "") ?: ""
        set(v) { sp(this).edit().putString(K_VISION_MODEL, v.trim()).apply() }

    var Context.sttApiUrl: String
        get() = sp(this).getString(K_STT_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_STT_URL, v.trim().trimEnd('/')).apply() }

    var Context.sttApiKey: String
        get() = sp(this).getString(K_STT_KEY, "") ?: ""
        set(v) { sp(this).edit().putString(K_STT_KEY, v.trim()).apply() }

    var Context.ttsApiUrl: String
        get() = sp(this).getString(K_TTS_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_TTS_URL, v.trim().trimEnd('/')).apply() }

    var Context.ttsApiKey: String
        get() = sp(this).getString(K_TTS_KEY, "") ?: ""
        set(v) { sp(this).edit().putString(K_TTS_KEY, v.trim()).apply() }
}
