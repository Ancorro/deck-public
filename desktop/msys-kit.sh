#!/usr/bin/env bash
# Build the MSYS2 kit the Windows PC app uses (msys/ in the server folder, a Deck checkout; git ignores it): tmux, bash
# and a few everyday tools, with only the DLLs they load. Deck runs every session in this tmux (deck/core.py), so
# Windows needs no WSL.
# Run in an MSYS2 shell (CI: windows-latest has MSYS2 at C:\msys64):  bash desktop/msys-kit.sh [out dir]
set -euo pipefail
OUT="$(realpath -m "${1:-$(dirname "$0")/../msys}")"
PKGS="msys2-runtime bash tmux coreutils grep sed gawk findutils less which ncurses diffutils"

pacman -S --noconfirm --needed $PKGS >/dev/null
rm -rf "$OUT"
mkdir -p "$OUT/usr/bin" "$OUT/etc" "$OUT/tmp"
touch "$OUT/tmp/keep"  # mount point for the user's temp folder (fstab); bash wants it to exist

# The packages' programs (no symlinks: MSYS2 ships real files), then every MSYS DLL they load, from /usr/bin (ldd
# sometimes misses one, so until a pass adds nothing).
exes=$(pacman -Ql $PKGS | awk '$2 ~ /^\/usr\/bin\/[^\/]+\.exe$/ {print $2}' | sort -u)
cp $exes "$OUT/usr/bin/"
# Windows programs in a pane get a real console through these (cygwin-console-helper.exe in /usr/bin, and Ctrl+C
# reaches them through getprocaddr64.exe); without them Claude Code sees no terminal.
install -D -t "$OUT/usr/libexec" /usr/libexec/getprocaddr64.exe
while :; do
  new=$(cd "$OUT/usr/bin" && ls | grep -E '\.(exe|dll)$' | sed 's|^|/usr/bin/|' | xargs ldd |
    awk '$1 ~ /^msys-.*\.dll$/ {print $1}' | sort -u | while read -r d; do [ -e "$OUT/usr/bin/$d" ] || echo "$d"; done)
  [ -n "$new" ] || break
  for d in $new; do cp "/usr/bin/$d" "$OUT/usr/bin/"; done
done

# Terminal descriptions tmux and its panes use.
for t in xterm-256color xterm tmux-256color tmux screen-256color screen; do
  f=$(find /usr/share/terminfo -name "$t" -type f | head -1)
  install -D "$f" "$OUT${f}"
done

# Drives at /c, /d, ...; /tmp is the user's temp folder (the kit itself may be read-only); no ACL games on Windows files.
printf '%s\n' 'none / cygdrive binary,posix=0,noacl,user 0 0' 'none /tmp usertemp binary,posix=0,noacl 0 0' > "$OUT/etc/fstab"
cp /etc/nsswitch.conf "$OUT/etc/"
# Login shells: keep Windows' PATH (where the agents are) with the kit's tools first, and stay in the session's folder.
cat > "$OUT/etc/profile" <<'EOF'
# Deck's MSYS2 kit (desktop/msys-kit.sh).
export PATH="/usr/bin:$PATH"
PS1='\[\e[32m\]\u@\h \[\e[33m\]\w\[\e[0m\]\n\$ '
EOF
echo "kit: $OUT ($(du -sh "$OUT" | cut -f1), $(find "$OUT" -type f | wc -l) files)"
