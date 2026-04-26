package cc.fiet.favilla

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class CaptureResult(val ok: Boolean, val queued: Boolean, val id: String?, val error: String?)
data class AppStatusResult(val ok: Boolean, val summary: String, val error: String?)

object CaptureClient {
    suspend fun send(
        serverUrl: String,
        token: String,
        text: String,
        source: String,
        url: String? = null,
        tags: List<String> = emptyList(),
        kind: String? = null,
        interaction: String? = null,
        sessionId: String? = null,
        phase: String? = null,
    ): CaptureResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("text", text)
            put("source", source)
            if (!url.isNullOrBlank()) put("url", url)
            if (tags.isNotEmpty()) put("tags", org.json.JSONArray(tags))
            if (!kind.isNullOrBlank()) put("kind", kind)
            if (!interaction.isNullOrBlank()) put("interaction", interaction)
            if (!sessionId.isNullOrBlank()) put("session_id", sessionId)
            if (!phase.isNullOrBlank()) put("phase", phase)
        }.toString().toByteArray(Charsets.UTF_8)

        val endpoint = URL(serverUrl.trimEnd('/') + "/api/capture")
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10_000
            readTimeout = 15_000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("X-Fiam-Token", token)
            setRequestProperty("User-Agent", "favilla/0.1 (Android)")
        }
        try {
            conn.outputStream.use { it.write(body) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val resp = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code in 200..299) {
                val j = JSONObject(resp)
                CaptureResult(
                    ok = true,
                    queued = j.optBoolean("queued", false),
                    id = j.optString("id", "").takeIf { it.isNotBlank() },
                    error = null,
                )
            } else {
                CaptureResult(false, false, null, "HTTP $code: $resp")
            }
        } catch (e: Exception) {
            CaptureResult(false, false, null, e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }

    suspend fun appStatus(serverUrl: String, token: String): AppStatusResult = withContext(Dispatchers.IO) {
        val endpoint = URL(serverUrl.trimEnd('/') + "/api/app/status")
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            connectTimeout = 10_000
            readTimeout = 15_000
            setRequestProperty("X-Fiam-Token", token)
            setRequestProperty("User-Agent", "favilla/0.1 (Android)")
        }
        try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val resp = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code in 200..299) {
                val j = JSONObject(resp)
                val summary = buildString {
                    appendLine("daemon: ${j.optString("daemon", "unknown")}")
                    appendLine("flow beats: ${j.optInt("flow_beats", 0)}")
                    appendLine("thinking beats: ${j.optInt("thinking_beats", 0)}")
                    appendLine("interaction beats: ${j.optInt("interaction_beats", 0)}")
                    appendLine("events: ${j.optInt("events", 0)}")
                    append("embeddings: ${j.optInt("embeddings", 0)}")
                }
                AppStatusResult(true, summary, null)
            } else {
                AppStatusResult(false, "", "HTTP $code: $resp")
            }
        } catch (e: Exception) {
            AppStatusResult(false, "", e.message ?: e.javaClass.simpleName)
        } finally {
            conn.disconnect()
        }
    }
}
