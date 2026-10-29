package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

func TestChildEnv(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("USERPROFILE", os.Getenv("HOME"))
	t.Setenv("TMUX", "/tmp/tmux-1/default,1,0") // started from a Deck pane
	os.MkdirAll(configDir(), 0o755)
	file := "# note\nDECK_HOST_NAME = pc \r\n=x\nDECK_ORIGINS=a=b\n"
	os.WriteFile(filepath.Join(configDir(), "env"), []byte(file), 0o644)
	env := childEnv()
	want := []string{"PYTHONUTF8=1", "DECK_HOST_NAME=pc", "DECK_ORIGINS=a=b"}
	if got := env[len(env)-3:]; !slices.Equal(got, want) {
		t.Fatalf("env tail = %q, want %q", got, want)
	}
	if slices.ContainsFunc(env, func(kv string) bool { return strings.HasPrefix(kv, "TMUX=") }) {
		t.Fatal("TMUX leaked into the server's environment")
	}
}
