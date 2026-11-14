//go:build windows

package main

// The Deck app window (user 2026-11-14: "make a deck native app again"): Deck's page in a WebView2 window (Edge's
// engine, part of Windows) instead of a browser tab, with its own taskbar button, icon (rsrc_windows_amd64.syso, from
// winres/) and Start menu entry (deck-app.exe, `deck shortcut`). Its Web view's page is a second WebView2 laid over the
// page's Web pane: a browser, not a frame, so
// sites that refuse to be framed (claude.ai artifacts, GitHub) load there, and their logins stay in the window's
// profile. The page drives it through window.deckHost(json) (deck/static/app/11-review.js "Web"), with the pane's box
// in window pixels (CSS px × devicePixelRatio):
//
//	{"t":"web","url":U,"x":..,"y":..,"w":..,"h":..}  show U over the pane
//	{"t":"bounds","x":..,"y":..,"w":..,"h":..}       the pane moved or changed size
//	{"t":"hide"}                                      the Web view closed, or a dialog covers the pane
//	{"t":"open","url":U}                              open U in the default browser (a link the page would open in a
//	                                                  new tab; http(s) only: the OS opens whatever else it is given)
//	{"t":"theme","dark":true}                         the page's theme: the title bar follows it

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"syscall"
	"unsafe"

	webview2 "github.com/jchv/go-webview2"
	"github.com/jchv/go-webview2/pkg/edge"
)

var (
	user32         = syscall.NewLazyDLL("user32.dll")
	dwmapi         = syscall.NewLazyDLL("dwmapi.dll")
	createWindowEx = user32.NewProc("CreateWindowExW")
	setWindowPos   = user32.NewProc("SetWindowPos")
	showWindow     = user32.NewProc("ShowWindow")
	setDpiContext  = user32.NewProc("SetProcessDpiAwarenessContext")
	loadImage      = user32.NewProc("LoadImageW")
	sendMessage    = user32.NewProc("SendMessageW")
	getModule      = syscall.NewLazyDLL("kernel32.dll").NewProc("GetModuleHandleW")
	setDwmAttr     = dwmapi.NewProc("DwmSetWindowAttribute")
)

const (
	wsChild        = 0x4000_0000
	wsClipSiblings = 0x0400_0000
	swHide         = 0
	swShowNA       = 8 // shown, not activated
	swpNoActivate  = 0x0010
	swpShowWindow  = 0x0040
	wmSetIcon      = 0x0080
	imageIcon      = 1
	lrDefaultSize  = 0x0040
	lrShared       = 0x8000
	dwmaDarkMode   = 20 // DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 20H1 and later)
	appIcon        = 1  // the icon resource (rsrc_windows_amd64.syso, built from winres/deck.ico)
)

type hostMsg struct {
	T, URL     string
	X, Y, W, H float64
	Dark       bool
}

// setIcon gives the window the app's icon, when the program carries one (deck-app.exe does; deck.exe doesn't).
func setIcon(hwnd uintptr) {
	mod, _, _ := getModule.Call(0)
	big, _, _ := loadImage.Call(mod, appIcon, imageIcon, 0, 0, lrDefaultSize|lrShared)
	if big == 0 {
		return
	}
	small, _, _ := loadImage.Call(mod, appIcon, imageIcon, 16, 16, lrShared)
	sendMessage.Call(hwnd, wmSetIcon, 1, big)
	if small != 0 {
		sendMessage.Call(hwnd, wmSetIcon, 0, small)
	}
}

// darkTitle colours the title bar to match the page (dark or light), so the window doesn't wear a white bar over a
// dark Deck. Older Windows without the attribute keep the default bar.
func darkTitle(hwnd uintptr, dark bool) {
	v := int32(0)
	if dark {
		v = 1
	}
	setDwmAttr.Call(hwnd, dwmaDarkMode, uintptr(unsafe.Pointer(&v)), unsafe.Sizeof(v))
}

// What the page needs to know it runs in the app window: links that would open a new tab go to the default browser,
// and the page's theme reaches the title bar. Runs in every page the main view loads (Deck's own, on any host).
const pageScript = `(() => {
  if (window.top !== window) return;
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || e.defaultPrevented || e.button !== 0) return;
    const url = new URL(a.href, location.href);
    const external = url.origin !== location.origin && /^https?:$/.test(url.protocol);
    if (external && (a.target === "_blank" || e.ctrlKey || e.shiftKey || e.metaKey)) {
      e.preventDefault();
      window.deckHost(JSON.stringify({ t: "open", url: url.href })).catch(() => {});
    }
  });
  const open = window.open;
  window.open = function (u, name, features) {
    try {
      const url = new URL(u, location.href);
      if (url.origin !== location.origin && /^https?:$/.test(url.protocol) && !/popup/.test(features || "")) {
        window.deckHost(JSON.stringify({ t: "open", url: url.href })).catch(() => {});
        return null;
      }
    } catch (_) { /* not a URL: the page's own */ }
    return open.apply(window, arguments);
  };
  const theme = () => window.deckHost(JSON.stringify({ t: "theme", dark: document.documentElement.dataset.theme === "dark" })).catch(() => {});
  new MutationObserver(theme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  document.addEventListener("DOMContentLoaded", theme);
})();`

func openWindow(url string) error {
	// Per-monitor DPI aware: sharp text, and window pixels are the page's CSS px times devicePixelRatio
	setDpiContext.Call(^uintptr(3))              // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4)
	data := filepath.Join(stateDir(), "webview") // Deck's page and the Web view's sites share it: logins persist
	w := webview2.NewWithOptions(webview2.WebViewOptions{DataPath: data, AutoFocus: true,
		WindowOptions: webview2.WindowOptions{Title: "Deck", Width: 1500, Height: 950, Center: true, IconId: appIcon}})
	if w == nil {
		return errors.New("couldn't open the Deck window (is the WebView2 runtime installed?)")
	}
	defer w.Destroy()
	hwnd := uintptr(w.Window())
	setIcon(hwnd)
	darkTitle(hwnd, true) // Deck opens dark until the page says otherwise

	var pane *edge.Chromium
	var child uintptr
	var embedding bool
	var pending *hostMsg  // the last message that came while the pane was starting (Embed runs the message loop)
	var pendingURL string // and the last page any of them named
	var apply func(m hostMsg)
	apply = func(m hostMsg) {
		switch m.T {
		case "open":
			if webURL(m.URL) {
				browse(m.URL)
			}
			return
		case "theme":
			darkTitle(hwnd, m.Dark)
			return
		}
		if m.URL != "" && !webURL(m.URL) {
			return
		}
		if embedding { // the pane is starting: its message loop runs, and so can more of these; the last one waits
			pending = &m
			if m.URL != "" {
				pendingURL = m.URL
			}
			return
		}
		if m.T == "hide" {
			if child != 0 {
				showWindow.Call(child, swHide)
			}
			return
		}
		if child == 0 {
			embedding = true
			static, _ := syscall.UTF16PtrFromString("STATIC")
			child, _, _ = createWindowEx.Call(0, uintptr(unsafe.Pointer(static)), 0, wsChild|wsClipSiblings,
				uintptr(int32(m.X)), uintptr(int32(m.Y)), uintptr(int32(m.W)), uintptr(int32(m.H)), hwnd, 0, 0, 0)
			pane = edge.NewChromium()
			pane.DataPath = data
			ok := child != 0 && pane.Embed(child)
			embedding = false
			if !ok {
				child, pane = 0, nil
				return
			}
			if pendingURL != "" {
				m.T, m.URL = "web", pendingURL
			}
			if p := pending; p != nil { // what came meanwhile: the latest box, or hidden
				m.X, m.Y, m.W, m.H = p.X, p.Y, p.W, p.H
				if p.T == "hide" {
					showWindow.Call(child, swHide)
					if m.URL != "" {
						pane.Navigate(m.URL)
					}
				}
			}
			hidden := pending != nil && pending.T == "hide"
			pending, pendingURL = nil, ""
			if hidden {
				return
			}
		}
		// on top of Deck's own page, over the pane's box
		setWindowPos.Call(child, 0, uintptr(int32(m.X)), uintptr(int32(m.Y)), uintptr(int32(m.W)), uintptr(int32(m.H)),
			swpNoActivate|swpShowWindow)
		pane.Resize()
		if m.T == "web" && m.URL != "" {
			pane.Navigate(m.URL)
		}
	}
	if err := w.Bind("deckHost", func(raw string) error {
		var m hostMsg
		if err := json.Unmarshal([]byte(raw), &m); err != nil {
			return err
		}
		// after this returns: WebView2 holds back the pane's start-up callbacks while one of its event handlers (this
		// call) runs, so starting the pane in here would wait forever
		w.Dispatch(func() { apply(m) })
		return nil
	}); err != nil {
		return err
	}
	w.Init(pageScript)
	w.Navigate(url)
	w.Run()
	return nil
}
