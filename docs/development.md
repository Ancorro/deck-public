# Developing Deck

Design and build status: [ARCHITECTURE.md](../ARCHITECTURE.md). Contribution rules: [CONTRIBUTING.md](../CONTRIBUTING.md).

## Repository layout

| Path | Role |
|---|---|
| `deck/` | Python backend. `server.py` starts the web server; the API is the package `deck/api/` (one module per area, each with a `routes()` list: `access.py` guards, `pages.py` the page and joined script, `sessions.py`, `agents.py`, `keys.py`, `chat.py`, `terminal.py`, `shelf.py`, `saves.py`, `notes.py`, `links.py`, `peers.py`, `pairing.py`, `screen.py`, `site.py`, `host.py`). Host modules: `config.py` (paths, environment, platform), `settings.py` (settings and their defaults), `agents.py` (agent command lines and the Deck context), `buttons.py` (script keys), `sessions.py` (tmux), `status.py` (pane analysis), `projects.py`, `screen.py`, pairing (`auth.py`), the site (`site.py`), machine notes (`notes.py`), linked PCs (`links.py`), session moves (`session_move.py`), saves, shelf, trash, alerts, descriptions, and the `deck-key` command line interface (CLI, `cli.py`). |
| `deck/bridge/` | Chat sessions: the bridge between an agent in a tmux pane and the chat (`bridge.py` the event log and commands, one adapter per agent: `claude.py`, `codex.py`, `opencode.py`, `pi.py`, `agy.py`, `openclaw.py`; `hooks.py` for Claude's hooks and status line). `deck/chat_bridge.py` is the script a pane runs; it puts the checkout on `sys.path` and calls `bridge.main`. |
| `deck/static/` | The web page, served as static files with no build step: `index.html`, `style.css` (the structure: layout and behaviour, every colour, font, radius and shadow a token), `sw.js` (service worker), `screen.js` (Screen tab), `manifest.webmanifest`. |
| `deck/static/looks/` | One stylesheet per look (Settings → Appearance → Look: `pad`, `rack`, `blueprint`, `zine`, `plain`, `message`): the tokens for light and dark, and what the look restyles. `index.html`'s head script loads the saved one before the first paint; `02-prefs.js applyLook` swaps it. A look's fonts are listed in that script's `LOOKS` map. |
| `deck/static/app/` | The page script in parts by area, joined by the server in `api/pages.py APP_ORDER` order and served as `/static/app.js`, with the assets version as its entity tag (ETag): `01-core` (helpers, shared state, the API, polling), `02-prefs` (settings that shape the page, look, views, the PC's columns), `03-pairing` (devices and linked PCs), `04-pad` (script keys, the ask sheet, the key editor), `05-tabs` (the tab column, new sessions, the session trash), `06-terminal` (xterm.js, input, touch scrolling, the agent footer), `07-chat` (chat sessions and their events), `08-markdown`, `09-composer` (the message box: tool row, Harness, Brain, Send, dictation, drafts, attachments), `10-shelf` (the shelf, documents onto it, the project bar), `11-review` (Review and Web panes, the PDF reader), `12-editor` (the rich and plain editors), `13-settings` (hosts, Settings, notes), `14-start` (boot: what runs once everything is declared). |
| `deck/win/` | Phone screen for the Screen tab on WSL hosts ([screen.md](screen.md#phone-screen-wsl-hosts)): `phonescreen.ps1` turns a Windows virtual monitor on, off and to the phone's size, compiling `DeckDisplay.cs` at run time. |
| `tunnel/` | iroh tunnel, Rust crate `deck-tunnel`: the `deck-iroh` binary for hosts, and a `cdylib` (`libdeck_tunnel.so`) that the Android app calls through the Java Native Interface (JNI). |
| `app/` | Android app (Capacitor): `www/` (host list page), `native/` (`MainActivity`, Dictation, Tunnel and Hosts plugins, copied into the generated `android/` by `build.sh`), `build.sh`, `push.sh` (build and install over Android Debug Bridge (adb)). |
| `desktop/` | PC app, Go, standard library only: `deck run`/`open`/`stop`/`install`; `msys-kit.sh` builds the MSYS2 tmux kit for Windows. |
| `deploy/` | `install.sh` (Linux and Windows Subsystem for Linux (WSL)), `windows-setup.py`, systemd units `deck.service` and `deck-iroh.service`, the `deck-key`, `deck-notes` and `deck-pair` commands, `update-all`, `cdn-sri`. |
| `deploy/agents/` | What Deck adds to agents: status lines (`agent-statusline.py`), agent context (`agent_context.py`), folder trust (`trust.py`), the agent browser (`agent-browser.sh`/`.ps1`), the OpenClaw bridge, Durable Pi (`pi-durable/`), Pi session naming (`pi-name.ts`), OpenCode sampling plugin, and the key builder's rules (`deck-keys/`). |
| `tests/` | Python tests (`test_*.py`), browser behavior tests (`*.test.cjs`, `node --test`), and the runner `run.sh`. |
| `docs/` | User docs: `user-guide.md`, `optional-features.md`, `guide.md` (reference), `install.md`, `security.md`, `screen.md`, this file. |
| `.github/` | Workflows `smoke.yml` and `release.yml`, issue and pull request templates. |
| `pyproject.toml` | pytest settings only (`asyncio_mode = "auto"`). |

## Tests

Run the relevant group for each patch:

```sh
bash tests/run.sh [GROUP] [pytest args]
```

| Group | Runs |
|---|---|
| `frontend` | `node --test tests/*.test.cjs` only. |
| `bridge` | `test_chat_bridge.py`, `test_agent_errors.py`, `test_agy.py`, `test_claude*.py` |
| `server` | `test_server*.py` |
| `core` | `test_core.py`, `test_saves.py`, `test_session_move.py` |
| `lint` | `ruff check deck deploy tests` (settings in `ruff.toml`) and `node --check` on every page script. |
| `all` (default with no arguments) | `frontend`, then all of `tests/`. |
| none, flags only (e.g. `-k stop`) | All Python tests in `tests/`, filtered; no frontend. |
| none, with a pytest path | Only the given Python tests, e.g. `bash tests/run.sh tests/test_claude_stop.py` or `tests/test_core.py::test_name`. |

- `test_site.py` (the site's journal, roles, snapshots, takeovers) is in no named group; only `all` or an explicit path runs it.
- Flags filter the group: `bash tests/run.sh bridge -k stop`. An explicit path replaces the group's default paths.
- A frontend-only patch does not need the Python suite. Use `all` for shared changes.
- Requirements: `uv` (fetches Python 3.12, `aiohttp`, `qrcode`, `pytest`, `pytest-aiohttp`, `pytest-asyncio`) and Node.js for `frontend`. On Windows (`OS=Windows_NT`, Git Bash or MSYS2 bash) it adds `pywinpty` and falls back to `uv.exe` in the checkout's parent folder.
- Isolation: the runner unsets `TMUX` and `TMUX_PANE` and points `TMUX_TMPDIR`, `LOCALAPPDATA`, `HOME` and `DECK_DATA` at a temp folder, so tests never touch a real Deck's tmux server or data. Running `python -m pytest` directly skips this; set the same variables yourself.
- The chat bridge tests look up `claude` on `PATH` (they never run it).
- Add regression tests for features when practical. Fix or remove broken or skipped tests; never keep them. Check user interface (UI) and agent changes live too.

Running a test Deck next to a real install (`DECK_PORT=8790`, temp `HOME`, `USERPROFILE`, `DECK_DATA`, `LOCALAPPDATA`): [install.md, Testing next to a real install](install.md#testing-next-to-a-real-install).

### Continuous integration (CI)

`.github/workflows/smoke.yml` runs on pull requests and by hand (Actions → smoke → Run workflow), on `ubuntu-latest` and `macos-latest`:

1. Browser behavior tests (`node --test`).
2. All of `tests/` with pytest, with a stand-in `claude` on `PATH`.
3. Starts `python -m deck.server`, creates a shell button through `/api/buttons`, runs it, and checks that the session appears in `/api/sessions`.
4. Checks that the tunnel port (8788) returns 401 for `/api/sessions`, `/api/settings` and `/api/devices`.

## Content delivery network (CDN) libraries and Subresource Integrity (SRI)

The page loads libraries from jsDelivr and cdnjs, each pinned to a `sha384` hash. A file whose hash doesn't match won't load.

| Where | What is pinned |
|---|---|
| `deck/static/index.html` | `integrity` on `<script>` and stylesheet tags (xterm.js, addon-fit, KaTeX); the import map's `integrity` block for modules (noVNC `rfb.js` and every module it imports, pdf.js). |
| `deck/static/app/10-shelf.js` | mammoth (`integrity` on its script) and the pdf.js worker (`PDFJS_WORKER_SRI`, fetched with its hash and run from a blob). |

After changing a library version, run `deploy/cdn-sri` and paste its output over the old hashes. It prints:

- Classic files: one hash per CDN `.js`/`.css` URL found in `index.html` and `app/*.js`.
- Workers: the pdf.js worker.
- Import map: the module list in `MODULES` (noVNC `rfb.js`, pdf.js), followed through relative imports, as a JavaScript Object Notation (JSON) `integrity` object.

A new module or worker URL must be added to `MODULES` or `WORKERS` in `deploy/cdn-sri`. TipTap, loaded from esm.sh in `7-editor.js`, is not pinned: esm.sh builds per browser, so a fixed hash would break.

## Releases

`.github/workflows/release.yml`. Plain pushes never build.

| Trigger | Builds |
|---|---|
| Run by hand (Actions → release → Run workflow) | `app` always; tunnel and Android package (APK) when the `tunnel` and `apk` inputs are ticked. Results are run artifacts only. |
| Push a tag matching `v*` (e.g. `v0.1.0`) | All jobs, then a draft GitHub release of that tag titled `Deck <tag>`; publish it on GitHub. |

| Job | Runner | Output |
|---|---|---|
| `app` | `windows-latest` | `go vet` and `go test` on `desktop/`; `deck.exe`, `uv.exe` and the MSYS2 kit in `server/msys` (artifact `deck-windows`). Also cross-compiles `desktop/` for Linux and macOS arm64 as a build check (not uploaded). |
| `tunnel` | `windows-latest` | `deck-iroh.exe` (artifact `deck-iroh-windows`). |
| `tunnel-linux` | `ubuntu-22.04` (x86_64), `ubuntu-22.04-arm` (aarch64) | `deck-iroh-linux-<arch>`, built on 22.04 for older glibc; `install.sh` downloads these. |
| `apk` | `ubuntu-latest` | `app/build.sh` with `DECK_APP_VERSION` set: no host list, release-signed when the secrets `DECK_KEYSTORE_B64` and `DECK_KEYSTORE_PASS` exist (key alias `deck`), otherwise debug-signed. |
| `release` | `ubuntu-latest` | Needs all four jobs. Attaches `deck-<tag>-windows.zip` (app, `deck-iroh.exe`, `NOTICE`, `LICENSE`), the Linux tunnels, the APK only if release-signed, and `SHA256SUMS`. |

Local builds: [install.md, PC app](install.md#pc-app) and [Phone app](install.md#phone-app).

## Roadmap

Ideas, not done and not promised:

- A Windows installer and a code-signed `deck.exe`.
- The in-page host switcher lists the inventory's uniform resource locators (URLs) and linked PCs; add hosts the phone app reaches over iroh that aren't linked PCs.
- Optional features stay off until set up: a feature this host or connection lacks is hidden or says why, never an empty panel. Done for Screen; other features not yet checked.
- Failover without Tailscale: the phone app learns a host's iroh key only after opening that host or one linked to it, so failover reaches only those.
- Notifications while the app is closed: Firebase Cloud Messaging (FCM), or a foreground service holding the iroh connection. Today alerts go through ntfy ([Phone alerts](guide.md#phone-alerts)).
