package cc.fiet.favilla

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class CaptureResult(val ok: Boolean, val queued: Boolean, val id: String?, val error: String?)
data class AppStatusResult(val ok: Boolean, val summary: String, val error: String?)
data class SplashResult(val ok: Boolean, val line: String, val error: String?)
data class ChatResult(
    val ok: Boolean,
    val reply: String,
    val recall: String?,
    val error: String?,
)

object CaptureClient {
    private const val USER_AGENT = "favilla/0.3.3 (Android)"

    private fun endpoint(serverUrl: String, path: String): URL =
        URL(serverUrl.trim().trimEnd('/') + path)

    private fun errorMessage(e: Exception): String = e.message ?: e.javaClass.simpleName

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

        val conn = try {
            (endpoint(serverUrl, "/api/capture").openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 10_000
                readTimeout = 15_000
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("X-Fiam-Token", token)
                setRequestProperty("User-Agent", USER_AGENT)
            }
        } catch (e: Exception) {
            return@withContext CaptureResult(false, false, null, errorMessage(e))
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
            CaptureResult(false, false, null, errorMessage(e))
        } finally {
            conn.disconnect()
        }
    }

    suspend fun appSplash(serverUrl: String, token: String): SplashResult = withContext(Dispatchers.IO) {
        val conn = try {
            (endpoint(serverUrl, "/api/app/splash").openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 800
                readTimeout = 900
                setRequestProperty("X-Fiam-Token", token)
                setRequestProperty("User-Agent", USER_AGENT)
            }
        } catch (e: Exception) {
            return@withContext SplashResult(false, "", errorMessage(e))
        }
        try {
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val resp = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code in 200..299) {
                val j = JSONObject(resp)
                SplashResult(true, j.optString("line", ""), null)
            } else {
                SplashResult(false, "", "HTTP $code: $resp")
            }
        } catch (e: Exception) {
            SplashResult(false, "", errorMessage(e))
        } finally {
            conn.disconnect()
        }
    }

    suspend fun appStatus(serverUrl: String, token: String): AppStatusResult = withContext(Dispatchers.IO) {
        val conn = try {
            (endpoint(serverUrl, "/api/app/status").openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 10_000
                readTimeout = 15_000
                setRequestProperty("X-Fiam-Token", token)
                setRequestProperty("User-Agent", USER_AGENT)
            }
        } catch (e: Exception) {
            return@withContext AppStatusResult(false, "", errorMessage(e))
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
            AppStatusResult(false, "", errorMessage(e))
        } finally {
            conn.disconnect()
        }
    }

    suspend fun chatCc(
        serverUrl: String,
        token: String,
        text: String,
        source: String,
        sessionId: String,
    ): ChatResult = withContext(Dispatchers.IO) {
        val body = JSONObject().apply {
            put("backend", "cc")
            put("text", text)
            put("source", source)
            put("session_id", sessionId)
        }
        try {
            postChat(endpoint(serverUrl, "/api/app/chat"), token, body)
        } catch (e: Exception) {
            ChatResult(false, "", null, errorMessage(e))
        }
    }

    suspend fun chatCustom(
        endpointUrl: String,
        token: String,
        text: String,
        source: String,
        sessionId: String,
    ): ChatResult = withContext(Dispatchers.IO) {
        if (endpointUrl.isBlank()) return@withContext ChatResult(false, "", null, "Custom API URL is empty")
        val body = JSONObject().apply {
            put("text", text)
            put("source", source)
            put("session_id", sessionId)
        }
        try {
            postChat(URL(endpointUrl.trim()), token, body)
        } catch (e: Exception) {
            ChatResult(false, "", null, errorMessage(e))
        }
    }

    private fun postChat(endpoint: URL, token: String, body: JSONObject): ChatResult {
        val conn = (endpoint.openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 10_000
            readTimeout = 260_000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            if (token.isNotBlank()) setRequestProperty("X-Fiam-Token", token)
            setRequestProperty("User-Agent", USER_AGENT)
        }
        return try {
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val resp = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code in 200..299) {
                val json = JSONObject(resp)
                ChatResult(
                    ok = json.optBoolean("ok", true),
                    reply = json.optString("reply", json.optString("text", "")),
                    recall = json.optString("recall", "").takeIf { it.isNotBlank() },
                    error = null,
                )
            } else {
                ChatResult(false, "", null, "HTTP $code: $resp")
            }
        } catch (e: Exception) {
            ChatResult(false, "", null, errorMessage(e))
        } finally {
            conn.disconnect()
        }
    }
}
