package cc.fiet.favilla

import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.TextUtils
import android.util.TypedValue
import android.view.Gravity
import android.view.ViewGroup
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import kotlinx.coroutines.launch

class QuickCaptureActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        var quote = intent.getStringExtra(EXTRA_TEXT)?.trim().orEmpty()
        if (quote.isEmpty()) quote = readClipboardOrEmpty().trim()
        showComposeDialog(quote)
    }

    private fun readClipboardOrEmpty(): String {
        return try {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return ""
            val clip = cm.primaryClip ?: return ""
            if (clip.itemCount == 0) return ""
            clip.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
        } catch (_: Throwable) { "" }
    }

    private fun dp(v: Float): Int = TypedValue.applyDimension(
        TypedValue.COMPLEX_UNIT_DIP, v, resources.displayMetrics
    ).toInt()

    private fun showComposeDialog(quote: String) {
        val pad = dp(16f)
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
        }
        if (quote.isNotEmpty()) {
            val quoteView = TextView(this).apply {
                text = quote
                setTextColor(Color.parseColor("#D59575"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f)
                maxLines = 6
                ellipsize = TextUtils.TruncateAt.END
                setPadding(dp(12f), dp(8f), dp(12f), dp(8f))
                setBackgroundColor(Color.parseColor("#1AD59575"))
            }
            container.addView(
                quoteView,
                LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                )
            )
            container.addView(TextView(this), LinearLayout.LayoutParams(0, dp(8f)))
        }
        val et = EditText(this).apply {
            setHint(R.string.dialog_input_hint)
            setSingleLine(false)
            minLines = 2
            maxLines = 8
            gravity = Gravity.TOP or Gravity.START
        }
        container.addView(
            et,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            )
        )
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_input_title)
            .setView(container)
            .setPositiveButton(R.string.dialog_input_send) { _, _ ->
                val zephyr = et.text.toString().trim()
                if (quote.isEmpty() && zephyr.isEmpty()) { finish(); return@setPositiveButton }
                val body = buildBody(quote, zephyr)
                val tag = when {
                    quote.isNotEmpty() && zephyr.isNotEmpty() -> "turn"
                    quote.isNotEmpty() -> "selection"
                    else -> "typed"
                }
                dispatch(body, tag)
            }
            .setNegativeButton(R.string.dialog_input_cancel) { _, _ -> finish() }
            .setOnCancelListener { finish() }
            .show()
    }

    private fun buildBody(quote: String, zephyr: String): String = when {
        quote.isNotEmpty() && zephyr.isNotEmpty() -> "[quote]\n$quote\n\n[zephyr]\n$zephyr"
        quote.isNotEmpty() -> "[quote]\n$quote"
        else -> "[zephyr]\n$zephyr"
    }

    private fun dispatch(text: String, tag: String) {
        val token = ingestToken
        if (token.isBlank()) {
            Toast.makeText(this, "Favilla: set token in app first", Toast.LENGTH_LONG).show()
            startActivity(Intent(this, MainActivity::class.java))
            finish(); return
        }
        Toast.makeText(this, "Favilla 🐦\u200D⬛ …", Toast.LENGTH_SHORT).show()
        val extraTags = mutableListOf("bubble", tag)
        FloatingService.sessionId?.let { extraTags += "session:$it" }
        lifecycleScope.launch {
            val r = CaptureClient.send(
                serverUrl = serverUrl,
                token = token,
                text = text,
                source = "$sourceTag:bubble",
                tags = extraTags,
            )
            Toast.makeText(
                applicationContext,
                if (r.ok) okToast(r) else "Favilla ✗ ${r.error ?: "failed"}",
                Toast.LENGTH_SHORT,
            ).show()
            if (r.ok && tag != "typed") FavillaAccessibilityService.clearCache()
            finish()
        }
    }

    private fun okToast(r: CaptureResult): String = when {
        r.id != null -> "Favilla ✓ ${r.id}"
        r.queued -> "Favilla ✓ queued"
        else -> "Favilla ✓"
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
