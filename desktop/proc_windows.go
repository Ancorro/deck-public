package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// runValue names the HKCU Run value `install` writes (the Tauri app used the same one).
var runValue = "Deck"

const runKey = `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`

// hide starts c without a console window.
func hide(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x0800_0000} // CREATE_NO_WINDOW
}

func killTree(pid int) {
	c := exec.Command("taskkill.exe", "/T", "/F", "/PID", strconv.Itoa(pid))
	hide(c)
	c.Run()
}

func isThisProgram(pid int) bool {
	c := exec.Command("tasklist.exe", "/FI", fmt.Sprintf("PID eq %d", pid), "/FO", "CSV", "/NH")
	hide(c)
	out, err := c.Output()
	exe, _ := os.Executable()
	return err == nil && strings.HasPrefix(strings.ToLower(string(out)), strings.ToLower(`"`+filepath.Base(exe)+`"`))
}

// autostart: `deck run` at logon, in a headless console (no window; the agent browser's logon task does the same).
func autostart(on bool) error {
	args := []string{"delete", runKey, "/v", runValue, "/f"}
	if on {
		exe, err := os.Executable()
		if err != nil {
			return err
		}
		conhost := filepath.Join(os.Getenv("WINDIR"), "System32", "conhost.exe")
		args = []string{"add", runKey, "/v", runValue, "/t", "REG_SZ", "/d",
			fmt.Sprintf(`"%s" --headless "%s" run`, conhost, exe), "/f"}
	}
	c := exec.Command("reg.exe", args...)
	hide(c)
	if out, err := c.CombinedOutput(); err != nil {
		return fmt.Errorf("reg %s: %s", args[0], strings.TrimSpace(string(out)))
	}
	return nil
}
