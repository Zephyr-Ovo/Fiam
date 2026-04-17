package cc.fiet.favilla

import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import cc.fiet.favilla.Prefs.sourceTag
import cc.fiet.favilla.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        b.etUrl.setText(serverUrl)
        b.etToken.setText(ingestToken)
        b.etSource.setText(sourceTag)

        b.btnSave.setOnClickListener {
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
            toast("saved")
        }

        b.btnTest.setOnClickListener {
            serverUrl = b.etUrl.text.toString()
            ingestToken = b.etToken.text.toString()
            sourceTag = b.etSource.text.toString()
            b.btnTest.isEnabled = false
            b.tvStatus.text = "sending test…"
            lifecycleScope.launch {
                val r = CaptureClient.send(
                    serverUrl = serverUrl,
                    token = ingestToken,
                    text = "favilla self-test " + System.currentTimeMillis(),
                    source = sourceTag,
                    tags = listOf("self-test"),
                )
                b.tvStatus.text = if (r.ok) "✓ event id: ${r.id}" else "✗ ${r.error}"
                b.btnTest.isEnabled = true
            }
        }

        b.tvHint.visibility = View.VISIBLE
    }

    private fun toast(msg: String) =
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
}
