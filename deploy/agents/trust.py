"""Mark folders as trusted for Claude Code and Codex, so agents start without the trust prompt.
Usage: python3 trust.py DIR [DIR ...]"""
import json
import os
import sys

dirs = [os.path.abspath(os.path.expanduser(d)) for d in sys.argv[1:]]

p = os.path.expanduser("~/.claude.json")
data = json.load(open(p)) if os.path.exists(p) else {}
for d in dirs:
    data.setdefault("projects", {}).setdefault(d, {})["hasTrustDialogAccepted"] = True
tmp = p + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, p)

c = os.path.expanduser("~/.codex/config.toml")
os.makedirs(os.path.dirname(c), exist_ok=True)
toml = open(c).read() if os.path.exists(c) else ""
for d in dirs:
    # A TOML literal string ('...') as Codex writes them, lowercased on Windows like Codex does: in "..." Windows
    # backslashes are escapes, and Codex then can't load its config at all.
    key = "'" + (d.lower() if os.name == "nt" else d) + "'" if "'" not in d else json.dumps(d)
    if f"[projects.{key}]" not in toml and f'[projects."{d}"]' not in toml:
        toml = toml.rstrip() + f'\n\n[projects.{key}]\ntrust_level = "trusted"\n'
open(c, "w").write(toml.lstrip())
print("trusted:", *dirs)
