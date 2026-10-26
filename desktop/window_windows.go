//go:build windows

package main

// The Deck window: Deck's page in a WebView2 window (Edge's engine, part of Windows), and the Web view's page in a
// second WebView2 laid over the page's Web pane. That one is a browser, not a frame, so sites that refuse to be framed
// (claude.ai artifacts, GitHub) load there, and their logins stay in the window's profile. The page drives it through
// window.deckHost(json) (deck/static/app/6-review.js "Web"), with the pane's box in window pixels (CSS px × devicePixelRatio):
//
//	{"t":"web","url":U,"x":..,"y":..,"w":..,"h":..}  show U over the pane
//	{"t":"bounds","x":..,"y":..,"w":..,"h":..}       the pane moved or changed size
//	{"t":"hide"}                                      the Web view closed, or a dialog covers the pane

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
	createWindowEx = user32.NewProc("CreateWindowExW")
	setWindowPos   = user32.NewProc("SetWindowPos")
	showWindow     = user32.NewProc("ShowWindow")
	setDpiContext  = user32.NewProc("SetProcessDpiAwarenessContext")
)

const (
	wsChild        = 0x4000_0000
	wsClipSiblings = 0x0400_0000
	swHide         = 0
	swShowNA       = 8 // shown, not activated
	swpNoActivate  = 0x0010
	swpShowWindow  = 0x0040
)

type hostMsg struct {
	T, URL     string
	X, Y, W, H float64
}

func openWindow(url string) error {
	// Per-monitor DPI aware: sharp text, and window pixels are the page's CSS px times devicePixelRatio
	setDpiContext.Call(^uintptr(3)) // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4)
	data := filepath.Join(stateDir(), "webview") // Deck's page and the Web view's sites share it: logins persist
	w := webview2.NewWithOptions(webview2.WebViewOptions{DataPath: data, AutoFocus: true,
		WindowOptions: webview2.WindowOptions{Title: "Deck", Width: 1500, Height: 950, Center: true}})
	if w == nil {
		return errors.New("couldn't open the Deck window (is the WebView2 runtime installed?)")
	}
	defer w.Destroy()

	var pane *edge.Chromium
	var child uintptr
	var embedding bool
	var pending *hostMsg // the last message that came while the pane was starting (Embed runs the message loop)
	var pendingURL string // and the last page any of them named
	var apply func(m hostMsg)
	apply = func(m hostMsg) {
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
				uintptr(int32(m.X)), uintptr(int32(m.Y)), uintptr(int32(m.W)), uintptr(int32(m.H)), uintptr(w.Window()), 0, 0, 0)
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
	w.Navigate(url)
	w.Run()
	return nil
}
