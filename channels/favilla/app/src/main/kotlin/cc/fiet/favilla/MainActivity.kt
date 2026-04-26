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
            showFragment(R.id.nav_home)
            b.navRail.selectedItemId = R.id.nav_home
        }

        b.navRail.setOnItemSelectedListener { item ->
            showFragment(item.itemId); true
        }
        b.navRail.setOnItemReselectedListener { /* no-op */ }
    }

    private fun showFragment(id: Int) {
        val tag = "tab-$id"
        val fm = supportFragmentManager
        val current = fm.findFragmentById(R.id.navHost)
        if (current?.tag == tag) return
        val next = fm.findFragmentByTag(tag) ?: when (id) {
            R.id.nav_chat -> ChatFragment()
            R.id.nav_phone -> HubFragment()              // Phone control reuses Hub for now
            R.id.nav_settings -> MoreFragment()          // Settings == old More
            R.id.nav_home -> PlaceholderFragment.newInstance(getString(R.string.nav_home))
            R.id.nav_reading -> PlaceholderFragment.newInstance(getString(R.string.nav_reading))
            R.id.nav_dashboard -> PlaceholderFragment.newInstance(getString(R.string.nav_dashboard))
            R.id.nav_stroll -> PlaceholderFragment.newInstance(getString(R.string.nav_stroll))
            R.id.nav_reminder -> PlaceholderFragment.newInstance(getString(R.string.nav_reminder))
            R.id.nav_history -> PlaceholderFragment.newInstance(getString(R.string.nav_history))
            else -> PlaceholderFragment.newInstance(getString(R.string.nav_home))
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
                ).apply { description = "Assistant replies when chat is in background." },
            )
        }
    }

    companion object {
        const val REPLY_CHANNEL_ID = "favilla_replies"
        const val REPLY_NOTIF_ID = 4202
    }
}
