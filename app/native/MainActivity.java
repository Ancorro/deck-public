package dev.deck.app;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.WebView;
import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.Logger;
import java.lang.reflect.Method;
import java.util.HashSet;
import java.util.Set;

// Copied over the generated MainActivity by build.sh (android/ is generated).
public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        registerPlugin(DictationPlugin.class); // our own plugins register before the bridge starts
        registerPlugin(TunnelPlugin.class);
        registerPlugin(HostsPlugin.class);
        super.onCreate(savedInstanceState);
        trusted.addAll(getSharedPreferences("deck", Context.MODE_PRIVATE).getStringSet("hosts", new HashSet<>()));
        if (!trusted.isEmpty()) exposePluginsTo(new HashSet<>(trusted));
        exposed.addAll(trusted);
        openHostLink(getIntent());
        // Back on a host's page: close an open dialog, else return to the host list; on the list it leaves the app.
        // Decided here from the URL: WebView history can't be trusted (Chromium skips entries a page adds without a
        // tap, so canGoBack() is false on a freshly opened host), and Capacitor alone always closes the app.
        getOnBackPressedDispatcher().addCallback(this, new androidx.activity.OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                WebView web = getBridge().getWebView();
                String url = web.getUrl();
                if (url == null || url.startsWith("https://localhost")) {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                    setEnabled(true);
                    return;
                }
                web.evaluateJavascript("(() => { const d = document.querySelector('dialog[open]:not(#pairgate)');"
                    + " if (d) { d.close(); return 1; } return 0; })()", (closed) -> {
                    if (!"1".equals(closed)) web.loadUrl("https://localhost/");
                });
            }
        });
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        openHostLink(intent);
    }

    // A Deck link handed to the app (pairing: the browser's "Pair the Deck app instead", or "Open supported links"):
    // open that host's page in the app. ?pair=CODE becomes #pair=CODE, which the page turns into its pairing screen.
    // deck://pair?key=K&name=N&code=C (a host's pairing QR when it runs deck-iroh): the launcher adds the host by its
    // iroh key and opens it through the tunnel.
    private void openHostLink(Intent intent) {
        Uri uri = intent == null ? null : intent.getData();
        if (uri != null && "deck".equals(uri.getScheme())) {
            String launcher = "https://localhost/#add=" + Uri.encode(uri.getQueryParameter("key"))
                + "&name=" + Uri.encode(uri.getQueryParameter("name")) + "&pair=" + Uri.encode(uri.getQueryParameter("code"));
            WebView web = getBridge().getWebView();
            web.post(() -> web.loadUrl(launcher));
            return;
        }
        if (uri == null || !"https".equals(uri.getScheme()) || uri.getHost() == null || !uri.getHost().endsWith(".ts.net")) return;
        String code = uri.getQueryParameter("pair");
        toList("https://" + uri.getHost(), code);
    }

    // The host list (the app's own page) with #open=ORIGIN[&pair=CODE]: it opens a saved host, and asks before adding
    // one it doesn't know.
    private void toList(String origin, String code) {
        String url = "https://localhost/#open=" + Uri.encode(origin) + (code != null ? "&pair=" + Uri.encode(code) : "");
        WebView web = getBridge().getWebView();
        web.post(() -> web.loadUrl(url));
    }

    // Saved hosts (https origins, kept across restarts) and this run's tunnel ports: the only pages that load in the app
    // and get its plugins. Capacitor's own bridge answers any allowNavigation origin, so untrusted pages must not load.
    private final Set<String> trusted = new HashSet<>();
    private final Set<String> exposed = new HashSet<>();

    void trust(Set<String> origins) {
        trusted.retainAll(tunnelOrigins);
        trusted.addAll(origins);
        getSharedPreferences("deck", Context.MODE_PRIVATE).edit().putStringSet("hosts", new HashSet<>(origins)).apply();
        Set<String> fresh = new HashSet<>(origins);
        fresh.removeAll(exposed);
        if (!fresh.isEmpty()) {
            exposed.addAll(fresh);
            exposePluginsTo(fresh); // a removed host keeps its script until the app restarts, but can no longer load
        }
    }

    // Plugin.shouldOverrideLoad for every navigation: null = Capacitor decides (app page, trusted host, or the
    // browser for other sites); true = blocked here.
    Boolean guard(Uri url) {
        String scheme = url.getScheme(), host = url.getHost();
        if (host == null || !("https".equals(scheme) || "http".equals(scheme))) return null;
        String origin = scheme + "://" + host + (url.getPort() != -1 ? ":" + url.getPort() : "");
        if (origin.equals("https://localhost") || trusted.contains(origin)) return null;
        if (host.equals("127.0.0.1") || host.equals("localhost")) return true; // not one of our tunnel ports
        if (host.endsWith(".ts.net")) { // in allowNavigation, so it would load here: ask first
            toList("https://" + host, null);
            return true;
        }
        return null;
    }

    // Capacitor injects its JS (window.Capacitor + plugins) only into the app's own page. Host pages (each PC's
    // Deck) need it too: notes storage, native mic. Only trusted origins get it (saved hosts, tunnel ports).
    // Reflection: the injector is internal to Capacitor, and androidx.webkit is only on Capacitor's classpath.
    // Tunnel pages (http://127.0.0.1:<port>), once their port is known.
    private final Set<String> tunnelOrigins = new HashSet<>();
    void exposePluginsTo(String origin) {
        runOnUiThread(() -> {
            if (tunnelOrigins.add(origin)) {
                trusted.add(origin);
                exposePluginsTo(java.util.Collections.singleton(origin));
            }
        });
    }

    private void exposePluginsTo(Set<String> origins) {
        try {
            Method get = Bridge.class.getDeclaredMethod("getJSInjector");
            get.setAccessible(true);
            Object injector = get.invoke(getBridge());
            Method script = injector.getClass().getDeclaredMethod("getScriptString");
            script.setAccessible(true);
            Class<?> compat = Class.forName("androidx.webkit.WebViewCompat");
            compat.getMethod("addDocumentStartJavaScript", WebView.class, String.class, Set.class)
                .invoke(null, getBridge().getWebView(), script.invoke(injector), origins);
        } catch (Exception e) {
            Logger.error("Deck: plugins not exposed to host pages", e);
        }
    }
}
