package dev.deck.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

// Deck over iroh (tunnel/ in the repo, built into libdeck_tunnel.so): open({key}) -> {port}. From then on
// http://127.0.0.1:<port>/ is that host's Deck, reached by its key with no Tailscale. Host pages there get our plugins.
@CapacitorPlugin(name = "Tunnel")
public class TunnelPlugin extends Plugin {
    @PluginMethod
    public void open(PluginCall call) {
        String key = call.getString("key", "");
        new Thread(() -> {
            try {
                int port = Tunnel.open(key);
                ((MainActivity) getActivity()).exposePluginsTo("http://127.0.0.1:" + port);
                JSObject res = new JSObject();
                res.put("port", port);
                call.resolve(res);
            } catch (Exception e) {
                call.reject(e.getMessage());
            }
        }).start();
    }
}
