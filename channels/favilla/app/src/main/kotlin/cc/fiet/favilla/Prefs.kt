package cc.fiet.favilla

import android.content.Context
import android.content.SharedPreferences

object Prefs {
    private const val FILE = "favilla_prefs"
    private const val K_URL = "server_url"
    private const val K_TOKEN = "ingest_token"
    private const val K_SOURCE = "source_tag"

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
}
