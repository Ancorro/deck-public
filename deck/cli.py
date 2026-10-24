"""Manage Deck keys from a shell (used by the key-builder agent).

  deck-key list
  deck-key add --label L --type shell|claude|codex|opencode|pi --text T [--cwd DIR] [--id ID]
  deck-key remove ID
"""
import argparse
import json
import sys

from . import core


def main(argv=None):
    p = argparse.ArgumentParser(prog="deck-key", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add", help="add a key, or update it when --id matches")
    a.add_argument("--label", required=True)
    a.add_argument("--type", required=True, choices=core.TYPES)
    a.add_argument("--text", default="")
    a.add_argument("--cwd", default="")
    a.add_argument("--id", default="")
    r = sub.add_parser("remove")
    r.add_argument("id")
    args = p.parse_args(argv)
    if args.cmd == "list":
        print(json.dumps(core.load_buttons(), indent=2))
    elif args.cmd == "add":
        try:
            b = core.upsert_button({k: getattr(args, k) for k in ("label", "type", "text", "cwd", "id")})
        except ValueError as e:
            sys.exit(f"error: {e}")
        print(f"saved key {b['id']}: {b['label']}")
    else:
        core.delete_button(args.id)
        print(f"removed {args.id}")


if __name__ == "__main__":
    main()
