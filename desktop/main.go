// Deck PC app: keeps this PC's Deck and its iroh tunnel running in the background. No window or tray: Deck's own page
// in the browser (http://127.0.0.1:8787/?pc) is the UI. (`deck window`, Windows: an experimental Deck window whose
// Web view is a real browser.)
//
//	deck [open [#fragment]]  open Deck in the default browser (starting `deck run` first if Deck isn't up)
//	deck window [#fragment]  the same in the Deck window (Windows, experimental: window_windows.go)
//	deck run                 start Deck and the tunnel, and restart them when they exit (what logon autostart runs)
//	deck stop                stop them, then (Windows) tmux and the agents in it
//	deck install|uninstall   start `deck run` at logon, or not (Windows: HKCU Run value "Deck")
//
// How `run` runs Deck:
//   - Deck already answering (e.g. the `deck` service from install.sh): it leaves that one alone, and takes over if it
//     stops.
//   - Otherwise it starts the Deck server in server/ next to this program (a git checkout: /api/update pulls it and, as
//     DECK_SUPERVISED tells it, just stops; this app starts it again) with `uv`, which brings its own Python and the
//     server's packages, and runs `deck-iroh serve` (tunnel/, same key files).
//   - Windows: no WSL. Sessions run in the MSYS2 tmux in server/msys (msys-kit.sh), and deploy/windows-setup.py sets
//     this user up (Git, Claude Code, Codex) on first run and after app updates.
package main

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// version stamps ~/.config/deck/app-version: deploy/windows-setup.py runs again when it changes.
const version = "0.3.0"

// Deck's port (DECK_PORT overrides it, e.g. to test next to a running Deck); the tunnel port is the next one.
func port() int {
	if p, err := strconv.Atoi(os.Getenv("DECK_PORT")); err == nil {
		return p
	}
	return 8787
}

func deckUp() bool {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port()), 300*time.Millisecond)
	if err == nil {
		c.Close()
	}
	return err == nil
}

// The user's home: %USERPROFILE% on Windows, like the server's Path.home() (a shell's HOME may be an MSYS path).
func home() string {
	h, _ := os.UserHomeDir()
	return h
}

// Deck's settings and keys live in ~/.config/deck on every OS (the same place the server and deck-iroh use).
func configDir() string { return filepath.Join(home(), ".config", "deck") }

// This app's log and pid file: %LOCALAPPDATA%\deck on Windows, beside tmux's sockets (deck/core.py).
func stateDir() string {
	d, err := os.UserCacheDir()
	if err != nil {
		d = home()
	}
	return filepath.Join(d, "deck")
}

func exeDir() string {
	exe, _ := os.Executable()
	return filepath.Dir(exe)
}

// The Deck server code: server/ next to this program, or this repo when run from desktop/.
func serverRoot() (string, error) {
	for _, d := range []string{filepath.Join(exeDir(), "server"), filepath.Dir(exeDir())} {
		if _, err := os.Stat(filepath.Join(d, "deck", "server.py")); err == nil {
			return d, nil
		}
	}
	return "", fmt.Errorf("Deck server files not found (%s)", filepath.Join(exeDir(), "server"))
}

// A program next to this one, else in its default install folders or on PATH; "" if none.
func findExe(name string) string {
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	for _, p := range []string{filepath.Join(exeDir(), name), filepath.Join(home(), ".local", "bin", name),
		filepath.Join(home(), ".cargo", "bin", name)} {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	p, _ := exec.LookPath(name)
	return p
}

// KEY=VALUE lines of ~/.config/deck/env (what the systemd unit loads).
func deckEnv() []string {
	text, _ := os.ReadFile(filepath.Join(configDir(), "env"))
	var env []string
	for _, l := range strings.Split(string(text), "\n") {
		k, v, ok := strings.Cut(l, "=")
		if k = strings.TrimSpace(k); ok && k != "" && !strings.HasPrefix(k, "#") {
			env = append(env, k+"="+strings.TrimSpace(v))
		}
	}
	return env
}

// This environment for the programs the app starts, minus a Deck pane's markers (started from a pane, they would
// point tmux at that pane's server and leak into new sessions), plus ~/.config/deck/env.
func childEnv() []string {
	var env []string
	for _, kv := range os.Environ() {
		k, _, _ := strings.Cut(kv, "=")
		switch strings.ToUpper(k) {
		case "TMUX", "TMUX_PANE", "CLAUDECODE":
			continue
		}
		env = append(env, kv)
	}
	return append(append(env, "PYTHONUTF8=1"), deckEnv()...) // UTF-8 files, not the Windows code page
}

// `python` with the server's packages, run by uv in the server folder.
func python(root string, args ...string) *exec.Cmd {
	uv := findExe("uv")
	if uv == "" {
		uv = "uv"
	}
	a := []string{"run", "--no-project", "--python", "3.12", "--with", "aiohttp", "--with", "qrcode"}
	if runtime.GOOS == "windows" {
		a = append(a, "--with", "pywinpty")
	}
	c := exec.Command(uv, append(append(a, "python"), args...)...)
	c.Dir, c.Env = root, childEnv()
	hide(c)
	return c
}

// Windows: set this user up for Deck (deploy/windows-setup.py) once per app version. On failure Deck starts anyway,
// and the next start tries again.
func setup(root string, out io.Writer) {
	stamp := filepath.Join(configDir(), "app-version")
	if b, _ := os.ReadFile(stamp); runtime.GOOS != "windows" || strings.TrimSpace(string(b)) == version {
		return
	}
	log.Print("setting up Deck (deploy/windows-setup.py; first run: a few minutes)")
	host := strings.ToLower(os.Getenv("COMPUTERNAME"))
	if host == "" {
		host = "pc"
	}
	c := python(root, "deploy/windows-setup.py", host)
	c.Stdout, c.Stderr = out, out
	if err := c.Run(); err != nil {
		log.Printf("setting up Deck failed: %v", err)
		return
	}
	os.MkdirAll(configDir(), 0o755)
	os.WriteFile(stamp, []byte(version), 0o644)
}

func pidFile() string { return filepath.Join(stateDir(), "deck.pid") }

// The running `deck run` (0 if none): the pid file's process, if it is still this program.
func supervisor() int {
	b, _ := os.ReadFile(pidFile())
	pid, _ := strconv.Atoi(strings.TrimSpace(string(b)))
	if pid <= 0 || pid == os.Getpid() || !isThisProgram(pid) {
		return 0
	}
	return pid
}

// Programs `run` keeps going, so a stop signal can take them down too.
var children sync.Map // pid -> struct{}

// keep runs what start makes, again whenever it exits: after a second if it ran a while, backing off to a minute if
// it keeps failing fast.
func keep(name string, out io.Writer, start func() *exec.Cmd) {
	wait := time.Second
	for {
		c := start()
		c.Stdin, c.Stdout, c.Stderr = nil, out, out
		t := time.Now()
		err := c.Start()
		if err == nil {
			children.Store(c.Process.Pid, struct{}{})
			err = c.Wait()
			children.Delete(c.Process.Pid)
		}
		if time.Since(t) > time.Minute {
			wait = time.Second
		} else {
			wait = min(2*wait, time.Minute)
		}
		log.Printf("%s exited (%v); starting it again in %v", name, err, wait)
		time.Sleep(wait)
	}
}

// waitForPort returns at once if nothing answers on Deck's port, else once nothing has for five seconds in a row (a
// Deck restarting itself is back sooner than that).
func waitForPort() {
	if !deckUp() {
		return
	}
	for free := 0; free < 5; time.Sleep(time.Second) {
		if free++; deckUp() {
			free = 0
		}
	}
}

func run() error {
	if pid := supervisor(); pid != 0 {
		fmt.Printf("Deck is already running (deck run, pid %d)\n", pid)
		return nil
	}
	root, err := serverRoot()
	if err != nil {
		return err
	}
	os.MkdirAll(stateDir(), 0o755)
	logPath := filepath.Join(stateDir(), "server.log")
	if fi, err := os.Stat(logPath); err == nil && fi.Size() > 10<<20 {
		os.Rename(logPath, logPath+".old")
	}
	out, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	log.SetOutput(io.MultiWriter(os.Stderr, out))
	if err := os.WriteFile(pidFile(), []byte(strconv.Itoa(os.Getpid())), 0o644); err != nil {
		return err
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		log.Printf("%v: stopping", <-stop)
		children.Range(func(pid, _ any) bool { killTree(pid.(int)); return true })
		os.Remove(pidFile())
		os.Exit(0)
	}()

	log.Printf("deck %s: Deck on 127.0.0.1:%d from %s", version, port(), root)
	if deckUp() {
		log.Print("Deck is already running; starting ours when it stops")
	}
	waitForPort()
	setup(root, out)
	if iroh := findExe("deck-iroh"); iroh != "" {
		// The iroh tunnel: phones reach this Deck by its key on any network (see tunnel/ and docs/install.md).
		go keep("deck-iroh", out, func() *exec.Cmd {
			c := exec.Command(iroh, "serve", "--key", filepath.Join(configDir(), "iroh.key"),
				"--to", fmt.Sprintf("127.0.0.1:%d", port()+1), "--id-file", filepath.Join(configDir(), "iroh.id"))
			c.Env = append(childEnv(), "HOME="+home()) // deck-iroh reads HOME, which Windows doesn't set
			hide(c)
			return c
		})
	} else {
		log.Print("deck-iroh not found: no iroh tunnel")
	}
	keep("Deck server", out, func() *exec.Cmd {
		waitForPort() // another Deck took the port: let it be until it stops
		c := python(root, "-m", "deck.server")
		c.Env = append(c.Env, fmt.Sprintf("DECK_PORT=%d", port()), fmt.Sprintf("DECK_TUNNEL_PORT=%d", port()+1),
			"DECK_SUPERVISED=1")
		return c
	})
	return nil
}

// Open Deck's page (`?pc`: no handset frame), starting `deck run` first if Deck isn't up: in the default browser, or
// the Deck window (inWindow: a process of its own, `deck window-run URL`).
func openDeck(fragment string, inWindow bool) error {
	if !deckUp() && supervisor() == 0 {
		exe, _ := os.Executable()
		c := exec.Command(exe, "run")
		hide(c)
		if err := c.Start(); err != nil {
			return err
		}
		c.Process.Release()
	}
	for t := time.Now(); !deckUp(); time.Sleep(300 * time.Millisecond) {
		if time.Since(t) > 90*time.Second {
			return fmt.Errorf("Deck isn't up yet (first-run setup takes minutes); log: %s",
				filepath.Join(stateDir(), "server.log"))
		}
	}
	url := fmt.Sprintf("http://127.0.0.1:%d/?pc", port())
	if fragment = strings.TrimPrefix(fragment, "#"); fragment != "" {
		url += "#" + fragment // e.g. devices-pair: straight to a new pairing code
	}
	if runtime.GOOS == "windows" && inWindow {
		exe, _ := os.Executable()
		c := exec.Command(exe, "window-run", url)
		hide(c)
		if err := c.Start(); err == nil {
			c.Process.Release()
			return nil
		}
	}
	return browse(url)
}

func browse(url string) error {
	switch runtime.GOOS {
	case "windows":
		return exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", url).Start()
	case "darwin":
		return exec.Command("open", url).Start()
	}
	return exec.Command("xdg-open", url).Start()
}

// Stop `deck run`, the server and the tunnel. Windows: then tmux and the agents in it, which aren't in the server's
// process tree (and a running tmux.exe would keep an update from replacing the kit).
func stopDeck() error {
	pid := supervisor()
	if pid == 0 {
		return errors.New("Deck isn't running here (no `deck run`)")
	}
	if runtime.GOOS == "windows" {
		killTree(pid)
	} else if p, err := os.FindProcess(pid); err == nil {
		p.Signal(syscall.SIGTERM) // it stops its children
	}
	os.Remove(pidFile())
	if runtime.GOOS != "windows" {
		return nil
	}
	root, err := serverRoot()
	if err != nil {
		return err
	}
	msys := filepath.Join(root, "msys")
	for _, kv := range childEnv() {
		if k, v, _ := strings.Cut(kv, "="); strings.EqualFold(k, "DECK_MSYS") {
			msys = v
		}
	}
	c := exec.Command(filepath.Join(msys, "usr", "bin", "tmux.exe"), "kill-server")
	c.Env = append(childEnv(), "TMUX_TMPDIR="+filepath.Join(stateDir(), "tmux")) // as deck/core.py sets it
	hide(c)
	c.Run()
	return nil
}

func main() {
	args := append(os.Args[1:], "", "")
	if args[0] == "" {
		args[0] = "open"
	}
	var err error
	switch args[0] {
	case "open", "window":
		err = openDeck(args[1], args[0] == "window")
	case "window-run": // what `window` starts; the browser if the window can't open (no WebView2 runtime)
		if err = openWindow(args[1]); err != nil {
			err = browse(args[1])
		}
	case "run":
		err = run()
	case "stop":
		err = stopDeck()
	case "install", "uninstall":
		err = autostart(args[0] == "install")
	default:
		fmt.Fprintln(os.Stderr, "usage: deck [open [#fragment] | window [#fragment] | run | stop | install | uninstall]")
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "deck:", err)
		os.Exit(1)
	}
}
