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
}
