package cc.fiet.favilla

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import kotlinx.coroutines.launch

/**
 * Transparent activity launched by the floating bubble after it has (best-effort)
 * read the current text selection via [FavillaAccessibilityService].
 *
 * - If EXTRA_TEXT is non-blank -> POST it immediately.
 * - Otherwise -> show an input dialog so the user can type a short note.
 */
class QuickCaptureActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val picked = intent.getStringExtra(EXTRA_TEXT)?.trim().orEmpty()
        if (picked.isNotEmpty()) {
            dispatch(picked, tag = "selection")
        } else {
            Toast.makeText(this, R.string.toast_nothing_selected, Toast.LENGTH_SHORT).show()
            showInputDialog()
        }
    }

    private fun showInputDialog() {
        val et = EditText(this).apply {
            setHint(R.string.dialog_input_hint)
            setSingleLine(false)
            minLines = 2
            maxLines = 6
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_input_title)
            .setView(et)
            .setPositiveButton(R.string.dialog_input_send) { _, _ ->
                val txt = et.text.toString().trim()
                if (txt.isEmpty()) finish() else dispatch(txt, tag = "typed")
            }
            .setNegativeButton(R.string.dialog_input_cancel) { _, _ -> finish() }
            .setOnCancelListener { finish() }
            .show()
    }

    private fun dispatch(text: String, tag: String) {
        val token = ingestToken
        if (token.isBlank()) {
            Toast.makeText(this, "Favilla: set token in app first", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java))
            finish(); return
        }
        Toast.makeText(this, "Favilla 🐦\u200D⬛ …", Toast.LENGTH_SHORT).show()
        lifecycleScope.launch {
            val r = CaptureClient.send(
                serverUrl = serverUrl,
                token = token,
                text = text,
                source = "$sourceTag:bubble",
                tags = listOf("bubble", tag),
            )
            Toast.makeText(
                applicationContext,
                if (r.ok) "Favilla ✓ ${r.id ?: ""}" else "Favilla ✗ ${r.error ?: "failed"}",
                Toast.LENGTH_SHORT,
            ).show()
            finish()
        }
    }

    companion object {
        const val EXTRA_TEXT = "cc.fiet.favilla.EXTRA_TEXT"

        fun launch(ctx: Context, preselectedText: String? = null) {
            ctx.startActivity(
                Intent(ctx, QuickCaptureActivity::class.java)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .addFlags(Intent.FLAG_ACTIVITY_NO_ANIMATION)
                    .putExtra(EXTRA_TEXT, preselectedText)
            )
        }
    }
}
