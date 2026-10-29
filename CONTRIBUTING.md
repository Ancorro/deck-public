# Contributing

Deck is small on purpose; changes that keep it small are the easiest to accept.

## Issues

| Kind | Where |
|---|---|
| Bug | Issue with the bug template: Deck commit, host operating system, how you reached Deck, phone or browser, agent and version, logs. |
| Security problem | Not an issue; see [SECURITY.md](SECURITY.md). |
| Feature | Open an issue first, to agree it fits before you write it. |

## Ground rules

- **Dependencies.** Backend: Python standard library plus `aiohttp` and `qrcode` (and `pywinpty` on Windows). The page is static files with no build step. Compiled parts: the tunnel (`tunnel/`, Rust), the Android app (`app/`) and the PC app (`desktop/`, Go). A new dependency needs a reason in the pull request.
- **Tests.** Run the relevant `bash tests/run.sh frontend|bridge|server|core|lint` group or explicit pytest paths; `all` for shared changes ([groups](docs/development.md#tests)). Add regression tests for features when practical. Fix or remove broken or skipped tests; never keep them.
- **Live check.** Check user interface (UI) and agent changes on a running Deck, and say in the pull request how you checked.
- **Content delivery network (CDN) libraries.** Pin every jsDelivr or cdnjs file with Subresource Integrity (SRI) hashes from `deploy/cdn-sri` ([details](docs/development.md#content-delivery-network-cdn-libraries-and-subresource-integrity-sri)).
- **Docs.** A change to a screen, setting or route updates `docs/guide.md`, and `docs/security.md` if it changes who gets in.
- **No machine facts.** No addresses, host names or who-runs-what in the repo; those belong in your own machine notes.

## Running a test Deck

Run it next to your own with `DECK_PORT=8790`, `TMUX` and `TMUX_PANE` unset, and a temp `HOME` and `DECK_DATA`, so your real sessions are never touched ([details](docs/install.md#testing-next-to-a-real-install)). Layout, tests, CDN pinning and releases: [docs/development.md](docs/development.md).

## License

By contributing you agree your work is licensed under the MIT License (`LICENSE`).
