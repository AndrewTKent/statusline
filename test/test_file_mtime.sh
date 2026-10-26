#!/usr/bin/env bash
# Regression tests for file_mtime. Pins the probe order: GNU stat's -f means
# --file-system, so a BSD-first probe exits 0 with a filesystem blob and never
# falls through, poisoning every cache-age check on Linux.
set -uo pipefail
cd "$(dirname "$0")/.."

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# statusline.sh reads stdin at top level, so extract just the function.
sed -n '/^file_mtime()/,/^}/p; /^file_size()/,/^}/p' bin/statusline.sh > "$tmp/fns.sh"
grep -q 'file_mtime()' "$tmp/fns.sh" || { echo "FAIL: extraction broke"; exit 1; }
grep -q 'file_size()' "$tmp/fns.sh" || { echo "FAIL: file_size extraction broke"; exit 1; }
# shellcheck disable=SC1091
source "$tmp/fns.sh"

fail() { echo "FAIL: $1"; exit 1; }

target="$tmp/cache.json"
echo '{}' > "$target"
expected=$(python3 -c 'import os,sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$target")

# Real stat on the host platform, whichever spelling that is.
[ "$(file_mtime "$target")" = "$expected" ] || fail "host mtime, got $(file_mtime "$target")"
file_mtime "$tmp/absent.json" >/dev/null 2>&1 && fail "missing file should return nonzero"

# Fake stat shims, one per platform flavor. PATH-first so file_mtime picks them up.
shim=$tmp/bin
mkdir -p "$shim"
PATH="$shim:$PATH"

# GNU coreutils: -f is --file-system and SUCCEEDS with output that is not an
# mtime. This is the case the BSD-first probe got wrong.
cat > "$shim/stat" <<GNU
#!/usr/bin/env bash
if [ "\$1" = "-c" ]; then printf '%s\n' "$expected"; exit 0; fi
if [ "\$1" = "-f" ]; then printf 'File: "%s"\n  ID: abc Namelen: 255\nBlocks: 419\n%s\n' "\$3" "$expected"; exit 0; fi
exit 1
GNU
chmod +x "$shim/stat"
[ "$(file_mtime "$target")" = "$expected" ] || fail "GNU stat, got [$(file_mtime "$target")]"

# BSD/macOS: no -c at all, mtime lives behind -f %m.
cat > "$shim/stat" <<BSD
#!/usr/bin/env bash
if [ "\$1" = "-f" ]; then printf '%s\n' "$expected"; exit 0; fi
echo "stat: illegal option -- \${1#-}" >&2
exit 1
BSD
chmod +x "$shim/stat"
[ "$(file_mtime "$target")" = "$expected" ] || fail "BSD stat, got [$(file_mtime "$target")]"

# Neither spelling yields a number: callers must degrade to "stale", not abort
# the enclosing block on an arithmetic syntax error.
cat > "$shim/stat" <<'JUNK'
#!/usr/bin/env bash
printf 'Type: overlayfs\nBlocks: Total: 419872768\n'
exit 0
JUNK
chmod +x "$shim/stat"
[ -z "$(file_mtime "$target")" ] || fail "non-numeric stat should print nothing"

# A syntax error here would exit the shell outright, so reaching the assertion
# is itself the check.
junk_mtime=$(file_mtime "$target")
age=$(( 1786469125 - junk_mtime ))
[ "$age" -gt 0 ] || fail "age from empty mtime should stay usable, got $age"

echo "PASS: file_mtime probes GNU before BSD and rejects non-numeric output"

# file_size: same probe order. GNU `stat -f %z FILE` reads %z as a second
# operand, prints the filesystem report for FILE and exits 1, so a BSD-first
# probe captured that report ahead of the real size and never yielded a number.
size_expected=$(python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_size)' "$target")
cat > "$shim/stat" <<GNU
#!/usr/bin/env bash
if [ "\$1" = "-c" ]; then printf '%s\n' "$size_expected"; exit 0; fi
if [ "\$1" = "-f" ]; then printf 'File: "%s"\nBlocks: Total: 419872768  Free: 4242\n' "\$3"; exit 1; fi
exit 1
GNU
chmod +x "$shim/stat"
[ "$(file_size "$target")" = "$size_expected" ] || fail "GNU stat size, got [$(file_size "$target")]"

cat > "$shim/stat" <<BSD
#!/usr/bin/env bash
if [ "\$1" = "-f" ]; then printf '%s\n' "$size_expected"; exit 0; fi
echo "stat: illegal option -- \${1#-}" >&2
exit 1
BSD
chmod +x "$shim/stat"
[ "$(file_size "$target")" = "$size_expected" ] || fail "BSD stat size, got [$(file_size "$target")]"

cat > "$shim/stat" <<'JUNK'
#!/usr/bin/env bash
printf 'Type: overlayfs\nBlocks: Total: 419872768\n'
exit 0
JUNK
chmod +x "$shim/stat"
file_size "$target" >/dev/null && fail "non-numeric stat should make file_size fail"

echo "PASS: file_size probes GNU before BSD and rejects non-numeric output"
