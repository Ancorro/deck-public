package main

import "net/url"

// webURL: only http(s) pages go to the app window's Web view pane or the default browser (window_windows.go): rundll32's
// FileProtocolHandler (browse) would run a file: link or another scheme it is handed.
func webURL(u string) bool {
	p, err := url.Parse(u)
	return err == nil && (p.Scheme == "http" || p.Scheme == "https") && p.Host != ""
}
