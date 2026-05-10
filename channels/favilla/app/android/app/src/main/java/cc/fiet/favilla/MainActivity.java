package cc.fiet.favilla;

import android.os.Bundle;
import androidx.core.view.WindowCompat;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Edge-to-edge: WebView paints behind status + nav bars so the
        // Home collage (and composer) extends under translucent system bars.
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
    }
}
