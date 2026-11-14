package dev.deck.app;

import android.net.Uri;
import com.getcapacitor.JSArray;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.HashSet;
import java.util.Set;

// Which pages may load in the app. Capacitor's bridge answers every allowNavigation origin (*.ts.net: anyone's tailnet,
// Funnel pages included), so only the user's saved hosts and open tunnel ports may load; see MainActivity.guard.
@CapacitorPlugin(name = "Hosts")
public class HostsPlugin extends Plugin {
    // sync({origins}): the host list's https hosts (https://<host>.ts.net). Only the list itself may call it.
    @PluginMethod
    public void sync(PluginCall call) {
        getActivity().runOnUiThread(() -> {
            String at = getBridge().getWebView().getUrl();
            if (at == null || !at.startsWith("https://localhost/")) {
                call.reject("only the app's host list changes the trusted hosts");
                return;
            }
            Set<String> origins = new HashSet<>();
            try {
                JSArray list = call.getArray("origins", new JSArray());
                for (int i = 0; i < list.length(); i++) origins.add(list.getString(i));
            } catch (Exception e) {
                call.reject("origins must be a list of strings");
                return;
            }
            ((MainActivity) getActivity()).trust(origins);
            call.resolve();
        });
    }

    @Override
    public Boolean shouldOverrideLoad(Uri url) {
        return ((MainActivity) getActivity()).guard(url);
    }
}
