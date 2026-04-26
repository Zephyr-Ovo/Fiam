package cc.fiet.favilla

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import cc.fiet.favilla.Prefs.ingestToken
import cc.fiet.favilla.Prefs.serverUrl
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

class SplashActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_splash)

        val root: View = findViewById(R.id.splashRoot)
        val line: TextView = findViewById(R.id.tvSplashLine)

        root.alpha = 0f
        root.animate()
            .alpha(1f)
            .setDuration(520)
            .setInterpolator(AccelerateDecelerateInterpolator())
            .start()

        lifecycleScope.launch {
            runCatching {
                if (ingestToken.isNotBlank()) {
                    withTimeoutOrNull(900) {
                        val splash = CaptureClient.appSplash(serverUrl, ingestToken)
                        if (splash.ok && splash.line.isNotBlank()) {
                            line.text = splash.line
                        }
                    }
                }
            }
            delay(1050)
            startActivity(Intent(this@SplashActivity, MainActivity::class.java))
            finish()
            overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
        }
    }
}
