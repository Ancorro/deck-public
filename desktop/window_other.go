//go:build !windows

package main

import "errors"

// The Deck window is Windows only (WebView2); elsewhere `deck open` uses the browser.
func openWindow(string) error { return errors.New("the Deck window is Windows only") }
