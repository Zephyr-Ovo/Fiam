package cc.fiet.favilla

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.graphics.PorterDuff
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import cc.fiet.favilla.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var b: ActivityMainBinding
    private val itemViews = mutableMapOf<Int, View>()
    private var selectedId: Int = 0

    private val requestNotif =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* user choice ok */ }

    private data class NavItem(val id: Int, val iconRes: Int, val labelRes: Int)

    private val navItems: List<NavItem> by lazy {
        listOf(
            NavItem(R.id.nav_home, R.drawable.ic_nav_home, R.string.nav_home),
            NavItem(R.id.nav_chat, R.drawable.ic_chat, R.string.nav_chat),
            NavItem(R.id.nav_reading, R.drawable.ic_reading, R.string.nav_reading),
            NavItem(R.id.nav_dashboard, R.drawable.ic_nav_dashboard, R.string.nav_dashboard),
            NavItem(R.id.nav_stroll, R.drawable.ic_stroll, R.string.nav_stroll),
            NavItem(R.id.nav_reminder, R.drawable.ic_reminder, R.string.nav_reminder),
            NavItem(R.id.nav_phone, R.drawable.ic_phone_control, R.string.nav_phone),
            NavItem(R.id.nav_history, R.drawable.ic_history, R.string.nav_history),
            NavItem(R.id.nav_settings, R.drawable.ic_settings, R.string.nav_settings),
        )
    }

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

        buildRail()

        if (savedInstanceState == null) {
            select(R.id.nav_home)
        }
    }

    private fun buildRail() {
        val inflater = layoutInflater
        for (item in navItems) {
            val view = inflater.inflate(R.layout.nav_rail_item, b.navRail, false)
            view.findViewById<ImageView>(R.id.itemIcon).setImageResource(item.iconRes)
            view.findViewById<TextView>(R.id.itemLabel).setText(item.labelRes)
            view.setOnClickListener { select(item.id) }
            b.navRail.addView(view)
            itemViews[item.id] = view
        }
    }

    private fun select(id: Int) {
        if (selectedId == id) return
        val accent = ContextCompat.getColor(this, R.color.peach)
        val muted = ContextCompat.getColor(this, R.color.ink_muted)
        for ((itemId, view) in itemViews) {
            val active = itemId == id
            val color = if (active) accent else muted
            view.findViewById<ImageView>(R.id.itemIcon).setColorFilter(color, PorterDuff.Mode.SRC_IN)
            view.findViewById<TextView>(R.id.itemLabel).setTextColor(color)
            view.isSelected = active
        }
        selectedId = id
        showFragment(id)
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
