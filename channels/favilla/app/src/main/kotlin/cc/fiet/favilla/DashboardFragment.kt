package cc.fiet.favilla

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import java.util.Calendar

/**
 * Dashboard v2 — purple-led wellbeing summary.
 * 静态占位数据；后续接入 daemon stats / health connect。
 */
class DashboardFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View = inflater.inflate(R.layout.fragment_dashboard, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        bindHeader(view)
        buildWeekStrip(view.findViewById(R.id.dashWeekStrip))
        bindEvent(view, R.id.dashEv1, R.string.dash_event_1_t, R.string.dash_event_1_h)
        bindEvent(view, R.id.dashEv2, R.string.dash_event_2_t, R.string.dash_event_2_h)
    }

    private fun bindHeader(root: View) {
        val cal = Calendar.getInstance()
        val months = arrayOf(
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        )
        val days = arrayOf("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        val month = months[cal.get(Calendar.MONTH)]
        val d = cal.get(Calendar.DAY_OF_MONTH)
        val w = days[cal.get(Calendar.DAY_OF_WEEK) - 1]
        root.findViewById<TextView>(R.id.dashTodayDate).text = "$month $d, $w"
    }

    private fun buildWeekStrip(strip: LinearLayout) {
        val ctx = requireContext()
        val today = Calendar.getInstance()
        // anchor strip to current week, Mon -> Sun
        val mondayOffset = ((today.get(Calendar.DAY_OF_WEEK) + 5) % 7)
        val mon = today.clone() as Calendar
        mon.add(Calendar.DAY_OF_MONTH, -mondayOffset)
        val labels = arrayOf("M", "T", "W", "T", "F", "S", "S")
        // Random-feel emojis seeded by day so order looks intentional.
        val emojis = arrayOf("🙂", "💪", "✨", "🌿", "☕", "🌙", "🎈")
        val plum = ContextCompat.getColor(ctx, R.color.plum)
        val plumWash = ContextCompat.getColor(ctx, R.color.plum_wash)
        val ink = ContextCompat.getColor(ctx, R.color.ink_strong)
        val faint = ContextCompat.getColor(ctx, R.color.ink_faint)

        for (i in 0..6) {
            val day = mon.clone() as Calendar
            day.add(Calendar.DAY_OF_MONTH, i)
            val isToday = day.get(Calendar.DAY_OF_YEAR) == today.get(Calendar.DAY_OF_YEAR) &&
                day.get(Calendar.YEAR) == today.get(Calendar.YEAR)

            val cell = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                gravity = android.view.Gravity.CENTER_HORIZONTAL
                setPadding(0, dp(10), 0, dp(10))
                if (isToday) {
                    setBackgroundResource(R.drawable.bg_tile_plum)
                }
            }
            val lp = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f).apply {
                if (i > 0) marginStart = dp(4)
            }

            val emoji = TextView(ctx).apply {
                text = emojis[i]
                textSize = 18f
            }
            val dayNum = TextView(ctx).apply {
                text = day.get(Calendar.DAY_OF_MONTH).toString()
                textSize = 14f
                setTextColor(if (isToday) plum else ink)
                setPadding(0, dp(4), 0, 0)
            }
            val dayLetter = TextView(ctx).apply {
                text = labels[i]
                textSize = 10f
                setTextColor(if (isToday) plum else faint)
            }
            cell.addView(emoji)
            cell.addView(dayNum)
            cell.addView(dayLetter)
            strip.addView(cell, lp)
        }
    }

    private fun bindEvent(root: View, includeId: Int, titleRes: Int, hourRes: Int) {
        val included = root.findViewById<View>(includeId)
        included.findViewById<TextView>(R.id.evTitle).setText(titleRes)
        included.findViewById<TextView>(R.id.evHour).setText(hourRes)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
