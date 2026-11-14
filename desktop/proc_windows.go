package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unsafe"
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

// isThisProgram: pid runs deck.exe or deck-app.exe (the same program, the console and the GUI build), so either finds
// the `deck run` the other started.
func isThisProgram(pid int) bool {
	c := exec.Command("tasklist.exe", "/FI", fmt.Sprintf("PID eq %d", pid), "/FO", "CSV", "/NH")
	hide(c)
	out, err := c.Output()
	if err != nil {
		return false
	}
	exe, _ := os.Executable()
	line := strings.ToLower(string(out))
	for _, name := range []string{filepath.Base(exe), "deck.exe", "deck-app.exe"} {
		if strings.HasPrefix(line, strings.ToLower(`"`+name+`"`)) {
			return true
		}
	}
	return false
}

// shortcut puts "Deck" in the Start menu (this user's Programs folder), starting the app window (deck-app.exe) with its
// icon. An existing Deck.lnk is replaced only if it is one this program made or points at a Deck program; any other
// (a user's own link to a Deck page in the browser) is left alone, and the new entry is named "Deck app".
func shortcut() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	app := filepath.Join(filepath.Dir(exe), "deck-app.exe")
	if _, err := os.Stat(app); err != nil {
		return fmt.Errorf("deck-app.exe is not next to %s (build it: docs/install.md, PC app)", filepath.Base(exe))
	}
	dir := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs")
	name := "Deck"
	if t := linkTarget(filepath.Join(dir, "Deck.lnk")); t != "" && !strings.EqualFold(t, app) && !strings.EqualFold(t, exe) {
		name = "Deck app"
	}
	script := fmt.Sprintf(`$s = (New-Object -ComObject WScript.Shell).CreateShortcut(%s); $s.TargetPath = %s; `+
		`$s.WorkingDirectory = %s; $s.IconLocation = %s; $s.Description = 'Deck: your agents and terminals'; $s.Save()`,
		psQuote(filepath.Join(dir, name+".lnk")), psQuote(app), psQuote(filepath.Dir(app)), psQuote(app+",0"))
	c := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script)
	hide(c)
	if out, err := c.CombinedOutput(); err != nil {
		return fmt.Errorf("shortcut: %s", strings.TrimSpace(string(out)))
	}
	fmt.Printf("Start menu: %s (opens %s)\n", name, app)
	return nil
}

// linkTarget: where a .lnk points ("" if there is none, or it can't be read).
func linkTarget(lnk string) string {
	if _, err := os.Stat(lnk); err != nil {
		return ""
	}
	c := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
		"(New-Object -ComObject WScript.Shell).CreateShortcut("+psQuote(lnk)+").TargetPath")
	hide(c)
	out, _ := c.Output()
	return strings.TrimSpace(string(out))
}

func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

// alert shows an error in a message box: deck-app.exe (the GUI build) has no console to print to.
func alert(text string) {
	t, _ := syscall.UTF16PtrFromString(text)
	title, _ := syscall.UTF16PtrFromString("Deck")
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(title)), 0x10)
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

// isPython: pid runs a Python (a chat bridge; deck/bridges.py starts them on the server's Python).
func isPython(pid int) bool {
	c := exec.Command("tasklist.exe", "/FI", fmt.Sprintf("PID eq %d", pid), "/FO", "CSV", "/NH")
	hide(c)
	out, err := c.Output()
	return err == nil && strings.HasPrefix(strings.ToLower(string(out)), `"python`)
}
