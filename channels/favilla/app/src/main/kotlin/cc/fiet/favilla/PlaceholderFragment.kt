package cc.fiet.favilla

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.FrameLayout
import androidx.fragment.app.Fragment

/**
 * Generic placeholder for nav destinations whose content has not been implemented yet.
 * Shows the section title plus a "coming soon" line. Lets us reserve nav slots without
 * shipping half-finished UI. Pass the title via [newInstance].
 */
class PlaceholderFragment : Fragment() {

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        val ctx = requireContext()
        val title = arguments?.getString(ARG_TITLE) ?: ""

        val root = FrameLayout(ctx).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setBackgroundResource(R.color.paper)
        }
        val column = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
            ).apply { gravity = android.view.Gravity.CENTER }
        }
        val titleView = TextView(ctx).apply {
            text = title
            setTextAppearance(R.style.TextAppearance_Favilla_Title)
            gravity = android.view.Gravity.CENTER
        }
        val statusView = TextView(ctx).apply {
            text = getString(R.string.placeholder_coming_soon)
            setTextAppearance(R.style.TextAppearance_Favilla_Meta)
            gravity = android.view.Gravity.CENTER
            setPadding(0, dp(8), 0, dp(16))
        }
        val hintView = TextView(ctx).apply {
            text = getString(R.string.placeholder_hint)
            setTextAppearance(R.style.TextAppearance_Favilla_Body)
            gravity = android.view.Gravity.CENTER
            setPadding(dp(24), 0, dp(24), 0)
        }
        column.addView(titleView)
        column.addView(statusView)
        column.addView(hintView)
        root.addView(column)
        return root
    }

    private fun dp(v: Int): Int =
        (v * resources.displayMetrics.density).toInt()

    companion object {
        private const val ARG_TITLE = "title"
        fun newInstance(title: String): PlaceholderFragment = PlaceholderFragment().apply {
            arguments = Bundle().apply { putString(ARG_TITLE, title) }
        }
    }
}
