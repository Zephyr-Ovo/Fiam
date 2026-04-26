package cc.fiet.favilla

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.launch
import org.json.JSONObject

class StatsFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_stats, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        bindCard(view, R.id.cardFlow, getString(R.string.stats_metric_flow), "—")
        bindCard(view, R.id.cardThinking, getString(R.string.stats_metric_thinking), "—")
        bindCard(view, R.id.cardInteraction, getString(R.string.stats_metric_interaction), "—")
        bindCard(view, R.id.cardEvents, getString(R.string.stats_metric_events), "—")
        bindCard(view, R.id.cardEmbeddings, getString(R.string.stats_metric_embeddings), "—")

        view.findViewById<MaterialButton>(R.id.btnRefresh).setOnClickListener { refresh(view) }
        refresh(view)
    }

    private fun bindCard(root: View, cardId: Int, label: String, value: String) {
        val card = root.findViewById<View>(cardId) ?: return
        card.findViewById<TextView>(R.id.tvLabel).text = label
        card.findViewById<TextView>(R.id.tvValue).text = value
    }

    private fun setValue(root: View, cardId: Int, value: String) {
        val card = root.findViewById<View>(cardId) ?: return
        card.findViewById<TextView>(R.id.tvValue).text = value
    }

    private fun refresh(root: View) {
        val ctx = context ?: return
        val daemon = root.findViewById<TextView>(R.id.tvDaemon)
        val err = root.findViewById<TextView>(R.id.tvError)
        daemon.text = getString(R.string.stats_loading)
        err.visibility = View.GONE

        viewLifecycleOwner.lifecycleScope.launch {
            val r = CaptureClient.appStatus(ctx.serverUrl, ctx.ingestToken)
            if (!r.ok) {
                daemon.text = "daemon: error"
                err.text = r.error ?: "unknown"
                err.visibility = View.VISIBLE
                return@launch
            }
            // The summary string is also a JSON-able body; parse what we got via summary lines.
            // appStatus returns formatted summary; we re-fetch to get raw fields.
            val raw = runCatching { fetchStatusRaw(ctx.serverUrl, ctx.ingestToken) }.getOrNull()
            if (raw != null) {
                daemon.text = "${getString(R.string.stats_daemon)}: ${raw.optString("daemon", "unknown")}"
                setValue(root, R.id.cardFlow, raw.optInt("flow_beats", 0).toString())
                setValue(root, R.id.cardThinking, raw.optInt("thinking_beats", 0).toString())
                setValue(root, R.id.cardInteraction, raw.optInt("interaction_beats", 0).toString())
                setValue(root, R.id.cardEvents, raw.optInt("events", 0).toString())
                setValue(root, R.id.cardEmbeddings, raw.optInt("embeddings", 0).toString())
            } else {
                daemon.text = r.summary
            }
        }
    }

    private suspend fun fetchStatusRaw(serverUrl: String, token: String): JSONObject? =
        kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            val url = java.net.URL(serverUrl.trimEnd('/') + "/api/app/status")
            val conn = (url.openConnection() as java.net.HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 10_000
                readTimeout = 15_000
                setRequestProperty("X-Fiam-Token", token)
            }
            try {
                val code = conn.responseCode
                if (code !in 200..299) return@withContext null
                val txt = conn.inputStream.bufferedReader().use { it.readText() }
                JSONObject(txt)
            } catch (_: Exception) {
                null
            } finally {
                conn.disconnect()
            }
        }
}
