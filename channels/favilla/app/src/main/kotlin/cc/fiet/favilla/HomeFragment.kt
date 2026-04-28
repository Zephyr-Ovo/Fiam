package cc.fiet.favilla

import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import com.google.android.material.button.MaterialButton
import org.json.JSONArray
import java.util.Calendar

/**
 * Home v2 — minimalist hero + editable Quick Access tiles.
 * 长按任意磁贴 -> 进入编辑模式（顶部按钮变 Done，磁贴可移除）。
 * "+ Add tile" 弹出选择器加回隐藏的项。
 * 选中项持久化到 SharedPreferences（fav_home_tiles）。
 */
class HomeFragment : Fragment() {

    private val allTiles: List<TileSpec> by lazy {
        listOf(
            TileSpec("chat", R.string.home_tile_chat, R.drawable.ic_chat, R.id.nav_chat),
            TileSpec("studio", R.string.home_tile_studio, R.drawable.ic_book, R.id.nav_reading),
            TileSpec("dashboard", R.string.home_tile_dashboard, R.drawable.ic_nav_dashboard, R.id.nav_dashboard),
            TileSpec("walk", R.string.home_tile_walk, R.drawable.ic_stroll, R.id.nav_stroll),
            TileSpec("reading", R.string.home_tile_reading, R.drawable.ic_reading, R.id.nav_reading),
            TileSpec("journal", R.string.home_tile_journal, R.drawable.ic_book, R.id.nav_history),
            TileSpec("notes", R.string.home_tile_notes, R.drawable.ic_recall, R.id.nav_history),
            TileSpec("settings", R.string.home_tile_settings, R.drawable.ic_settings, R.id.nav_settings),
        )
    }

    private val activeKeys = mutableListOf<String>()
    private var editMode = false

    private lateinit var quickGrid: LinearLayout
    private lateinit var editToggle: TextView
    private lateinit var greeting: TextView
    private lateinit var todayNote: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_home, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        quickGrid = view.findViewById(R.id.quickGrid)
        editToggle = view.findViewById(R.id.homeEditToggle)
        greeting = view.findViewById(R.id.homeGreeting)
        todayNote = view.findViewById(R.id.homeTodayNote)

        greeting.setText(greetingRes())
        todayNote.text = todayLine()

        view.findViewById<MaterialButton>(R.id.heroCta).setOnClickListener {
            navigateTo(R.id.nav_stroll)
        }

        loadActive()
        editToggle.setOnClickListener { setEditMode(!editMode) }
        renderTiles()
    }

    private fun greetingRes(): Int {
        val h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY)
        return when {
            h in 5..10 -> R.string.home_greet_morning
            h in 11..16 -> R.string.home_greet_afternoon
            h in 17..21 -> R.string.home_greet_evening
            else -> R.string.home_greet_night
        }
    }

    private fun todayLine(): String {
        val cal = Calendar.getInstance()
        val days = arrayOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        val months = arrayOf(
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )
        val w = days[cal.get(Calendar.DAY_OF_WEEK) - 1]
        val m = months[cal.get(Calendar.MONTH)]
        val d = cal.get(Calendar.DAY_OF_MONTH)
        return "$w · $m $d"
    }

    private fun loadActive() {
        val prefs = requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val raw = prefs.getString(KEY_TILES, null)
        activeKeys.clear()
        if (raw == null) {
            activeKeys.addAll(DEFAULT_KEYS)
        } else {
            try {
                val arr = JSONArray(raw)
                for (i in 0 until arr.length()) activeKeys.add(arr.getString(i))
            } catch (_: Throwable) {
                activeKeys.addAll(DEFAULT_KEYS)
            }
        }
    }

    private fun saveActive() {
        val arr = JSONArray()
        activeKeys.forEach { arr.put(it) }
        requireContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_TILES, arr.toString()).apply()
    }

    private fun setEditMode(on: Boolean) {
        editMode = on
        editToggle.setText(if (on) R.string.home_done else R.string.home_edit)
        renderTiles()
    }

    private fun renderTiles() {
        quickGrid.removeAllViews()
        val ctx = requireContext()
        val tiles = activeKeys.mapNotNull { k -> allTiles.firstOrNull { it.key == k } }
        // Always pad to even rows; append "+ Add" tile at end.
        val withAdd = tiles + listOf(ADD_TILE)
        var i = 0
        while (i < withAdd.size) {
            val row = LinearLayout(ctx).apply {
                orientation = LinearLayout.HORIZONTAL
                layoutParams = LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT,
                ).apply { topMargin = dp(12) }
            }
            for (col in 0..1) {
                if (i + col >= withAdd.size) {
                    // empty spacer to keep alignment
                    row.addView(View(ctx), tileLp(col))
                } else {
                    val spec = withAdd[i + col]
                    row.addView(buildTile(spec), tileLp(col))
                }
            }
            quickGrid.addView(row)
            i += 2
        }
    }

    private fun tileLp(col: Int): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
            if (col == 0) marginEnd = dp(6) else marginStart = dp(6)
        }

    private fun buildTile(spec: TileSpec): View {
        val v = layoutInflater.inflate(R.layout.item_home_tile, quickGrid, false)
        val icon = v.findViewById<ImageView>(R.id.tileIcon)
        val label = v.findViewById<TextView>(R.id.tileLabel)
        val sub = v.findViewById<TextView>(R.id.tileSub)

        if (spec === ADD_TILE) {
            v.setBackgroundResource(R.drawable.bg_tile_dashed)
            icon.visibility = View.GONE
            label.setText(R.string.home_add_tile)
            label.setTextColor(ContextCompat.getColor(requireContext(), R.color.plum))
            v.setOnClickListener { showAddPicker() }
            v.setOnLongClickListener(null)
            return v
        }

        // Alternate cream / plum mist for visual rhythm
        val idx = activeKeys.indexOf(spec.key)
        v.setBackgroundResource(
            if (idx % 2 == 0) R.drawable.bg_tile_plum else R.drawable.bg_tile_cream,
        )
        icon.setImageResource(spec.iconRes)
        label.setText(spec.labelRes)
        if (editMode) {
            sub.visibility = View.VISIBLE
            sub.text = "tap to remove"
            v.setOnClickListener {
                activeKeys.remove(spec.key)
                saveActive()
                renderTiles()
            }
        } else {
            sub.visibility = View.GONE
            v.setOnClickListener { navigateTo(spec.navId) }
            v.setOnLongClickListener {
                setEditMode(true); true
            }
        }
        return v
    }

    private fun showAddPicker() {
        val candidates = allTiles.filter { it.key !in activeKeys }
        if (candidates.isEmpty()) {
            Toast.makeText(requireContext(), "All tiles already on home.", Toast.LENGTH_SHORT).show()
            return
        }
        val labels = candidates.map { getString(it.labelRes) }.toTypedArray()
        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle(R.string.home_add_tile)
            .setItems(labels) { _, which ->
                activeKeys.add(candidates[which].key)
                saveActive()
                renderTiles()
            }
            .show()
    }

    private fun navigateTo(navId: Int) {
        (activity as? MainActivity)?.selectNav(navId)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()

    private data class TileSpec(
        val key: String,
        val labelRes: Int,
        val iconRes: Int,
        val navId: Int,
    )

    companion object {
        private const val PREFS = "fav_home"
        private const val KEY_TILES = "tiles_v1"
        private val DEFAULT_KEYS = listOf("chat", "studio", "walk", "dashboard")
        private val ADD_TILE = TileSpec("__add__", 0, 0, 0)
    }
}
