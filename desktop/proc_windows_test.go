package main

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

// install writes the Run value under a throwaway name (the real one, "Deck", is left alone) and uninstall removes it.
func TestAutostart(t *testing.T) {
	runValue = "DeckAutostartTest"
	t.Cleanup(func() { autostart(false) })
	if err := autostart(true); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("reg.exe", "query", runKey, "/v", runValue).Output()
	exe, _ := os.Executable()
	if err != nil || !strings.Contains(string(out), `\conhost.exe" --headless "`+exe+`" run`) {
		t.Fatalf("Run value: %v %s", err, out)
	}
	if err := autostart(false); err != nil {
		t.Fatal(err)
	}
	if exec.Command("reg.exe", "query", runKey, "/v", runValue).Run() == nil {
		t.Fatal("Run value still there after uninstall")
	}
}
