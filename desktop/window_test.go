package main

import "testing"

// The app window hands only web pages to the default browser or the Web view's pane: rundll32's FileProtocolHandler
// (browse) would run a file: link or another scheme it was given.
func TestWebURL(t *testing.T) {
	for u, want := range map[string]bool{
		"https://example.com/page":          true,
		"http://127.0.0.1:8787/?pc":         true,
		"HTTPS://EXAMPLE.COM/upper-is-fine": true,
		"file:///C:/Windows/notepad.exe":    false,
		"javascript:alert(1)":               false,
		"ms-settings:":                      false,
		"mailto:someone@example.com":        false,
		"https:no-host":                     false,
		`C:\Windows\System32\calc.exe`:      false,
		`\\server\share\run.exe`:            false,
		"":                                  false,
	} {
		if got := webURL(u); got != want {
			t.Errorf("webURL(%q) = %v, want %v", u, got, want)
		}
	}
}
