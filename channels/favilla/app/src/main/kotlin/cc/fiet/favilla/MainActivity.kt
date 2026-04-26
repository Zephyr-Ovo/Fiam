package cc.fiet.favilla

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import cc.fiet.favilla.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding

    private val requestNotif =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* user choice ok */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)

        ensureReplyChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            requestNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        if (savedInstanceState == null) {
            showFragment(R.id.nav_chat)
            b.bottomNav.selectedItemId = R.id.nav_chat
        }

        b.bottomNav.setOnItemSelectedListener { item ->
            showFragment(item.itemId); true
        }
        b.bottomNav.setOnItemReselectedListener { /* no-op */ }
    }

    private fun showFragment(id: Int) {
        val tag = "tab-$id"
        val fm = supportFragmentManager
        val current = fm.findFragmentById(R.id.navHost)
        if (current?.tag == tag) return
        val next = fm.findFragmentByTag(tag) ?: when (id) {
            R.id.nav_chat -> ChatFragment()
            R.id.nav_hub -> HubFragment()
            R.id.nav_stats -> StatsFragment()
            R.id.nav_more -> MoreFragment()
            else -> ChatFragment()
        }
        fm.beginTransaction()
            .setReorderingAllowed(true)
            .replace(R.id.navHost, next, tag)
            .commit()
    }

    private fun ensureReplyChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(REPLY_CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                NotificationChannel(
                    REPLY_CHANNEL_ID,
                    getString(R.string.notif_channel_replies),
                    NotificationManager.IMPORTANCE_DEFAULT,
                ).apply { description = "Fiet's replies when chat is in background." },
            )
        }
    }

    companion object {
        const val REPLY_CHANNEL_ID = "favilla_replies"
        const val REPLY_NOTIF_ID = 4202
    }
}
