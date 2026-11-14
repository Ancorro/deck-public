//go:build !windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
)

// hide starts c in its own process group, so killTree reaches what it starts (uv's Python).
func hide(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}

func killTree(pid int) {
	syscall.Kill(-pid, syscall.SIGTERM)
}

func isThisProgram(pid int) bool {
	out, err := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", "comm=").Output()
	exe, _ := os.Executable()
	return err == nil && filepath.Base(strings.TrimSpace(string(out))) == filepath.Base(exe)
}

func autostart(bool) error {
	return errors.New("autostart is Windows-only; on Linux, deploy/install.sh sets up the deck and deck-iroh services")
}

// isPython: pid runs a Python (a chat bridge; deck/bridges.py starts them on the server's Python).
func isPython(pid int) bool {
	out, err := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", "comm=").Output()
	return err == nil && strings.HasPrefix(strings.TrimSpace(string(out)), "python")
}
