# Security policy

Deck gives whoever gets in a shell on your computer. Security reports are welcome.

## Reporting a vulnerability

1. Open the repo's **Security** tab on GitHub → **Report a vulnerability**. Don't open a public issue.
2. Include what you can:
   - Deck commit (`git rev-parse --short HEAD` in the checkout) or release tag.
   - Host OS (Linux, Windows Subsystem for Linux (WSL), Windows PC app).
   - How Deck was reached: Tailscale, the iroh tunnel, or a reverse proxy.
   - Steps to reproduce.

Expect a first reply within a week. Deck is maintained by one person in their spare time. There is no bug bounty.

## Supported versions

Only the latest `main`. Hosts update by pulling it ([docs/install.md](docs/install.md#update)), so fixes land there and are not backported to release tags.

## In scope

Getting into Deck, or into a host's sessions, without a paired device or the host key:

- Pairing (codes, claims, approval, device cookies, device sync between hosts).
- The tunnel port (8788) and `deck-iroh`.
- Origin and Host header checks.
- Host linking (Settings → More → PCs) and host key rotation.
- The phone app's host list and plugin guard.

## Known and by design

[docs/security.md](docs/security.md) describes what Deck trusts. These are not vulnerabilities on their own:

- Any process or user account on the host reaches `127.0.0.1:8787` as the host's own browser, without pairing.
- `DECK_AUTH=off` or `auto` lets anyone who reaches port 8787 (for example your tailnet) in without pairing.
- A reverse proxy pointed at 8787 instead of the tunnel port 8788.
- Whoever holds the host key, or a paired device, has full access.
- Libraries the page loads from esm.sh (the Markdown editor) and Google Fonts aren't hash-pinned.
