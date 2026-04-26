package cc.fiet.favilla

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import kotlinx.coroutines.launch

/**
 * Handles PROCESS_TEXT (text selection menu) and SEND (share sheet) intents.
 * Posts the selected/shared text to fiam's /api/capture endpoint, toasts the result, finishes.
 */
class CaptureActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val text = extractText(intent)
        if (text.isNullOrBlank()) {
            toast("Favilla: no text")
            finish(); return
        }

        val token = ingestToken
        if (token.isBlank()) {
            toast("Favilla: open the app and set token first")
            startActivity(Intent(this, MainActivity::class.java))
            finish(); return
        }

        toast("Favilla: capturing…")
        lifecycleScope.launch {
            val r = CaptureClient.send(
                serverUrl = serverUrl,
                token = token,
                text = text.trim(),
                source = sourceTag,
            )
            toast(if (r.ok) okToast(r) else "Favilla ✗ ${r.error ?: "failed"}")
            finish()
        }
    }

    private fun okToast(r: CaptureResult): String = when {
        r.id != null -> "Favilla ✓ ${r.id}"
        r.queued -> "Favilla ✓ queued"
        else -> "Favilla ✓"
    }

    private fun extractText(i: Intent?): String? {
        if (i == null) return null
        i.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.toString()?.let { return it }
        if (i.action == Intent.ACTION_SEND) {
            i.getStringExtra(Intent.EXTRA_TEXT)?.let { return it }
        }
        return null
    }

    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
