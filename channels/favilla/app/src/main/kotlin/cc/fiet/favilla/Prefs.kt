package cc.fiet.favilla

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object Prefs {
    private const val FILE = "favilla_prefs"
    private const val KEY_ALIAS = "favilla_prefs_secret"
    private const val SECURE_PREFIX = "enc:v1:"
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

    private fun SharedPreferences.getSecureString(key: String): String {
        val stored = getString(key, "") ?: ""
        if (stored.isBlank()) return ""
        if (stored.startsWith(SECURE_PREFIX)) return decrypt(stored).orEmpty()
        val value = stored.trim()
        edit().putSecureString(key, value).apply()
        return value
    }

    private fun SharedPreferences.Editor.putSecureString(
        key: String,
        value: String,
    ): SharedPreferences.Editor {
        val normalized = value.trim()
        return putString(key, if (normalized.isBlank()) "" else encrypt(normalized) ?: normalized)
    }

    private fun encrypt(value: String): String? = runCatching {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val cipherText = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        SECURE_PREFIX + encode(cipher.iv) + ":" + encode(cipherText)
    }.getOrNull()

    private fun decrypt(value: String): String? = runCatching {
        val payload = value.removePrefix(SECURE_PREFIX)
        val parts = payload.split(':', limit = 2)
        if (parts.size != 2) return@runCatching ""
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            secretKey(),
            GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)),
        )
        String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
    }.getOrNull()

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val keyGenerator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore",
        )
        val spec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .build()
        keyGenerator.init(spec)
        return keyGenerator.generateKey()
    }

    private fun encode(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)

    var Context.serverUrl: String
        get() = sp(this).getString(K_URL, "https://fiet.cc") ?: "https://fiet.cc"
        set(v) { sp(this).edit().putString(K_URL, v.trim().trimEnd('/')).apply() }

    var Context.ingestToken: String
        get() = sp(this).getSecureString(K_TOKEN)
        set(v) { sp(this).edit().putSecureString(K_TOKEN, v).apply() }

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
        get() = sp(this).getSecureString(K_VISION_KEY)
        set(v) { sp(this).edit().putSecureString(K_VISION_KEY, v).apply() }

    var Context.visionModel: String
        get() = sp(this).getString(K_VISION_MODEL, "") ?: ""
        set(v) { sp(this).edit().putString(K_VISION_MODEL, v.trim()).apply() }

    var Context.sttApiUrl: String
        get() = sp(this).getString(K_STT_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_STT_URL, v.trim().trimEnd('/')).apply() }

    var Context.sttApiKey: String
        get() = sp(this).getSecureString(K_STT_KEY)
        set(v) { sp(this).edit().putSecureString(K_STT_KEY, v).apply() }

    var Context.ttsApiUrl: String
        get() = sp(this).getString(K_TTS_URL, "") ?: ""
        set(v) { sp(this).edit().putString(K_TTS_URL, v.trim().trimEnd('/')).apply() }

    var Context.ttsApiKey: String
        get() = sp(this).getSecureString(K_TTS_KEY)
        set(v) { sp(this).edit().putSecureString(K_TTS_KEY, v).apply() }
}
