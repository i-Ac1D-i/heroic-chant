#!/usr/bin/env bash
#
# A rehearsal of the phone install, on a desktop.
#
#   bash tools/test-termux-setup.sh
#
# termux-setup.sh is the one path in this project that a new player runs
# unattended, on hardware we don't have, over a connection that drops. It used
# to be checked with `bash -n` and nothing else. This runs it for real:
#
#   * every Termux-only command (pkg, pm, termux-open, termux-setup-storage,
#     termux-wake-lock, am) is stubbed onto PATH and its calls recorded
#   * HOME points at a sandbox, so nothing touches your real home
#   * the repo it clones is a throwaway copy of your *working tree*, so this
#     tests the script you're editing, not the one on GitHub
#   * the downloads come off a local HTTP server, with the real spec.json,
#     the real herocantare.db and a cut-down ngelgames.zip -- enough that the
#     boot server is happy and the self-test genuinely runs
#   * it is run the way the docs tell players to run it -- downloaded to a
#     file, then executed -- and scenario 1 additionally proves the older
#     `curl | bash` form still works, since that changes what stdin is
#
# Scenarios, in order:
#   1. clean install
#   2. re-run with nothing new upstream
#   3. re-run with a new commit upstream (the update path)
#   4. re-run after a download died halfway
#   5. a truncated file already at the final name is re-fetched
#   6. the patched-APK branch stops and waits for the tap
#   7. start.sh actually brings both servers up
#
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"        # the server/ repo root
SANDBOX="${HC_TEST_DIR:-$(mktemp -d)}"
PORT="${HC_TEST_PORT:-18099}"

PASS=0; FAIL=0
C_OK=$'\033[1;32m'; C_ERR=$'\033[1;31m'; C_INFO=$'\033[1;36m'; C_OFF=$'\033[0m'
say()  { printf '\n%s== %s%s\n' "$C_INFO" "$*" "$C_OFF"; }
ok()   { PASS=$((PASS+1)); printf '  %sPASS%s  %s\n' "$C_OK" "$C_OFF" "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  %sFAIL%s  %s\n' "$C_ERR" "$C_OFF" "$*"; }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }
fsize(){ wc -c < "$1" 2>/dev/null | tr -d ' \r' || echo -1; }

# start.sh backgrounds two pythons and then waits, so killing the shell that
# launched it leaves them holding 8080 and 21010. Kill the pythons by name.
stop_servers() {
    if command -v pkill >/dev/null 2>&1; then
        pkill -f 'tools/bootserver.py' 2>/dev/null
        pkill -f 'hc\.main' 2>/dev/null
    elif command -v powershell >/dev/null 2>&1; then
        # Filter on Name first. Without it the query matches the powershell
        # process running the query -- the pattern is right there in its own
        # command line -- and it kills the whole shell tree, this script
        # included, which looks exactly like a mysterious hang.
        powershell -NoProfile -Command \
          "Get-CimInstance Win32_Process | Where-Object { \$_.Name -eq 'python.exe' -and \$_.CommandLine -match 'bootserver|hc\.main' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" \
          >/dev/null 2>&1
    fi
    return 0
}

cleanup() {
    [ -n "${HTTP_PID:-}" ] && kill "$HTTP_PID" 2>/dev/null
    stop_servers
    [ -n "${HC_TEST_DIR:-}" ] || rm -rf "$SANDBOX"
    return 0
}
trap cleanup EXIT

echo "sandbox: $SANDBOX"

# ------------------------------------------------------------------ stubs --
# Everything Termux provides and a desktop does not. Each one appends to
# $SANDBOX/calls.log so a scenario can assert that it was reached.
BIN="$SANDBOX/bin"; mkdir -p "$BIN"
CALLS="$SANDBOX/calls.log"
: > "$CALLS"
mkstub() { printf '#!/usr/bin/env bash\necho "%s $*" >> "%s"\n%s\n' \
                  "$1" "$CALLS" "${2:-exit 0}" > "$BIN/$1"; chmod +x "$BIN/$1"; }

mkstub pkg
mkstub am
mkstub termux-wake-lock
mkstub termux-open
mkstub termux-setup-storage 'mkdir -p "$HOME/storage/downloads"; exit 0'
# A flag file, not an env var, so a scenario can "install the game" while the
# script is already running and waiting for it.
FLAG="$SANDBOX/installed.flag"
mkstub pm "[ -f '$FLAG' ] && echo 'package:com.ngelgames.herocantare'; exit 0"

# ------------------------------------------------------------- fake origin --
# A git repo holding the working tree as it is right now, tracked files only --
# which is exactly what a real clone gets, since data/ and spec.json are
# gitignored and arrive in the data bundle instead.
ORIGIN="$SANDBOX/origin"
mkdir -p "$ORIGIN"
(cd "$HERE" && git ls-files -z | tar --null -T - -cf -) | (cd "$ORIGIN" && tar -xf -)
git -C "$ORIGIN" init -q -b main
git -C "$ORIGIN" -c user.email=t@t -c user.name=t add -A 2>/dev/null
git -C "$ORIGIN" -c user.email=t@t -c user.name=t commit -qm "working tree under test"

# --------------------------------------------------------------- artifacts --
# Real spec.json + real tables + real db, so the self-test at the end of the
# installer is a genuine pass and not a stub saying yes.
ART="$SANDBOX/artifacts"; mkdir -p "$ART"

DB_SRC="${HC_TEST_DB:-$HERE/../table/herocantare.db}"
FILES_SRC="${HC_TEST_FILES:-$HERE/../files}"
for p in "$DB_SRC" "$FILES_SRC/ngelgames/assetList.json.gz"; do
    [ -e "$p" ] || { echo "missing $p -- set HC_TEST_DB / HC_TEST_FILES"; exit 2; }
done
cp "$DB_SRC" "$ART/herocantare.db"

python - "$HERE" "$ART" "$FILES_SRC" <<'PY'
import os, sys, zipfile
here, art, files = sys.argv[1:4]

# data bundle: what tools/make_mobile_bundle.py --no-db produces
with zipfile.ZipFile(os.path.join(art, 'heroic-chant-data.zip'), 'w',
                     zipfile.ZIP_DEFLATED) as z:
    z.write(os.path.join(here, 'hc/protocol/spec.json'), 'spec.json')
    n = 0
    for root, _, names in os.walk(os.path.join(here, 'data')):
        for name in names:
            p = os.path.join(root, name)
            z.write(p, os.path.relpath(p, here))
            n += 1
    print('data bundle: spec.json + %d tables' % n)

# ngelgames.zip, cut down to the three files bootserver.py refuses to start
# without, plus the small JSONs it serves during boot. Same files/ngelgames/
# nesting as the real one, so this also exercises the zip-depth detection.
want = ['assetList.json.gz', 'dnsinfo.json', 'auth.json', 'copyMovie.json',
        'centerservergroup.json', 'AssetBundle/script/dungeon']
with zipfile.ZipFile(os.path.join(art, 'ngelgames.zip'), 'w',
                     zipfile.ZIP_DEFLATED) as z:
    for rel in want:
        p = os.path.join(files, 'ngelgames', rel)
        if os.path.exists(p):
            z.write(p, 'files/ngelgames/' + rel)
    print('ngelgames.zip: %d entries' % len(z.namelist()))

# Stand-in APKs. patch_apk.py --check only reads the boot URLs out of
# global-metadata.dat, so a zip with that one entry is enough to drive both
# branches: the unpatched one must warn and carry on, the patched one must
# open the installer and then wait for the tap.
META = 'assets/bin/Data/Managed/Metadata/global-metadata.dat'
for name, url in [
        ('hero-cantare.apk',         b'http://dlhc.ngelgames.net/herocantare/patchinfo/'),
        ('hero-cantare-patched.apk', b'http://127.0.0.1:8080/////herocantare/patchinfo/')]:
    with zipfile.ZipFile(os.path.join(art, name), 'w') as z:
        z.writestr(META, b'\x00' * 32 + url + b'\x00' * 32)
PY

# http.server on its own answers every Range request with the whole file, so
# `curl -C -` could never resume against it and scenario 4 would prove nothing.
# Google Drive does honour ranges, so the harness has to as well.
cat > "$SANDBOX/serve.py" <<'PY'
import functools, http.server, os, re, sys


class Ranged(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get('Range', '')
        m = re.match(r'bytes=(\d+)-(\d*)$', rng)
        if not m:
            return super().send_head()
        path = self.translate_path(self.path)
        try:
            size = os.path.getsize(path)
        except OSError:
            return super().send_head()
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        if start >= size:
            self.send_error(416, 'Requested Range Not Satisfiable')
            return None
        f = open(path, 'rb')
        f.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        return f

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer(
    ('127.0.0.1', int(sys.argv[1])),
    functools.partial(Ranged, directory=sys.argv[2])).serve_forever()
PY

python "$SANDBOX/serve.py" "$PORT" "$ART" >/dev/null 2>&1 &
HTTP_PID=$!
for _ in $(seq 20); do
    curl -sf -o /dev/null "http://127.0.0.1:$PORT/heroic-chant-data.zip" && break
    sleep 1
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/heroic-chant-data.zip" \
    || { echo "local http server did not come up on $PORT"; exit 2; }

# The real script's size floors are the shipped artifact sizes; ours are these
# cut-down stand-ins. Exact rather than approximate, so scenario 4b -- a
# truncated file that already sits at the final name -- is actually caught.
FILES_MIN="$(fsize "$ART/ngelgames.zip")"
DB_MIN="$(fsize "$ART/herocantare.db")"
DATA_MIN="$(fsize "$ART/heroic-chant-data.zip")"

# ------------------------------------------------------------------- runner --
U="http://127.0.0.1:$PORT"
run_setup() {
    ( export HOME="$SANDBOX/home" \
             PATH="$BIN:$PATH" \
             HC_REPO="$ORIGIN" \
             HC_FILES_URL="$U/ngelgames.zip" \
             HC_DB_URL="$U/herocantare.db" \
             HC_DATA_URL="$U/heroic-chant-data.zip" \
             HC_APK_URL="${HC_APK_URL:-$U/hero-cantare.apk}" \
             HC_NONINTERACTIVE="${HC_NONINTERACTIVE:-0}" \
             HC_FILES_MIN="$FILES_MIN" HC_DB_MIN="$DB_MIN" \
             HC_DATA_MIN="$DATA_MIN" HC_APK_MIN=1
      mkdir -p "$HOME"
      if [ "${PIPE_IT:-0}" = 1 ]; then
          # `curl | bash` makes stdin the script itself, and anything in the
          # script that reads stdin will eat it. Still supported, still tested.
          cat "$ORIGIN/tools/termux-setup.sh" | bash
      else
          # What the docs actually tell players to run: download to a file,
          # then run the file. curl -fsSL is the point -- plain -sL says
          # nothing at all when it cannot reach GitHub, which looks exactly
          # like the script being broken.
          cp "$ORIGIN/tools/termux-setup.sh" "$HOME/hc-setup.sh"
          bash "$HOME/hc-setup.sh"
      fi ) > "$SANDBOX/run.log" 2>&1
    echo $?
}

HOMEDIR="$SANDBOX/home"
BASE="$HOMEDIR/heroic-chant"

# ================================================================ scenario 1 =
say "1. clean install"
RC="$(run_setup)"
sed 's/^/    | /' "$SANDBOX/run.log" | tail -30

check "exits 0"                        "[ '$RC' = 0 ]"
check "reached the end"                "grep -q 'Done\.' '$SANDBOX/run.log'"
check "cloned the server"              "[ -d '$BASE/server/.git' ]"
check "spec.json in place"             "[ -s '$BASE/server/hc/protocol/spec.json' ]"
check "data tables unpacked"           "[ \$(ls '$BASE/server'/data/*/*.json 2>/dev/null | wc -l) -gt 200 ]"
check "herocantare.db in place"        "[ -s '$BASE/herocantare.db' ]"
check "client files in place"          "[ -s '$BASE/ngelgames.zip' ]"
check "no .part left behind"           "[ -z \"\$(ls '$BASE'/*.part 2>/dev/null)\" ]"
check "start.sh written, executable"   "[ -x '$BASE/start.sh' ]"
check "self-test passed"               "grep -q 'self-test passed' '$SANDBOX/run.log'"
check "warned about the unpatched APK" "grep -q 'dead official CDN' '$SANDBOX/run.log'"
check "did not open the installer"     "! grep -q '^termux-open' '$CALLS'"
check "asked for storage access"       "grep -q '^termux-setup-storage' '$CALLS'"

# The docs moved to download-then-run, but plenty of people will still pipe it,
# and the brace wrap is what makes that safe. Prove both invocations work.
RC="$(PIPE_IT=1 run_setup)"
check "still works when piped into bash" "[ '$RC' = 0 ]"
check "  ...and reaches the end"         "grep -q 'Done\.' '$SANDBOX/run.log'"

# ================================================================ scenario 2 =
say "2. re-run, nothing new upstream"
RC="$(run_setup)"
check "exits 0"                  "[ '$RC' = 0 ]"
check "says already up to date"  "grep -q 'already up to date' '$SANDBOX/run.log'"
check "skips the big download"   "grep -q 'client files already here' '$SANDBOX/run.log'"
check "self-test passed"         "grep -q 'self-test passed' '$SANDBOX/run.log'"

# ================================================================ scenario 3 =
say "3. re-run with a new commit upstream"
echo "# a change from upstream" >> "$ORIGIN/README.md"
git -C "$ORIGIN" -c user.email=t@t -c user.name=t commit -qam "upstream change"
NEW="$(git -C "$ORIGIN" log -1 --format=%h)"
RC="$(run_setup)"
check "exits 0"                 "[ '$RC' = 0 ]"
check "lists the new commit"    "grep -q 'upstream change' '$SANDBOX/run.log'"
check "fast-forwarded"          "grep -q 'updated to' '$SANDBOX/run.log'"
check "local HEAD moved"        "[ \"\$(git -C '$BASE/server' log -1 --format=%h)\" = '$NEW' ]"

# ================================================================ scenario 4 =
say "4. re-run after a download died halfway"
FULL=$(fsize "$ART/ngelgames.zip")
head -c $((FULL / 3)) "$ART/ngelgames.zip" > "$BASE/ngelgames.zip.part"
rm -f "$BASE/ngelgames.zip"
RC="$(run_setup)"
check "exits 0"                     "[ '$RC' = 0 ]"
check "recovered the client files"  "[ -s '$BASE/ngelgames.zip' ]"
check "and they are complete"       "[ \$(fsize '$BASE/ngelgames.zip') = $FULL ]"
check "resumed rather than restarted" \
      "grep -q 'resuming' '$SANDBOX/run.log'"
check "no .part left behind"        "[ -z \"\$(ls '$BASE'/*.part 2>/dev/null)\" ]"

# ================================================================ scenario 5 =
say "5. a truncated whole file is not accepted as done"
head -c $((FULL / 3)) "$ART/ngelgames.zip" > "$BASE/ngelgames.zip"
RC="$(run_setup)"
check "exits 0"                    "[ '$RC' = 0 ]"
check "re-fetched it"              "[ \$(fsize '$BASE/ngelgames.zip') = $FULL ]"

# ================================================================ scenario 6 =
# The one branch that has to stop and wait for a human. Under `curl | bash` a
# plain `read` doesn't wait at all -- it eats the next line of the script off
# stdin -- so this checks the script actually blocks until the game shows up.
say "6. patched APK: opens the installer and waits for it"
rm -f "$HOMEDIR/storage/downloads/hero-cantare.apk" "$BASE/hero-cantare.apk" "$FLAG"
( sleep 12; touch "$FLAG" ) &
FLAGGER=$!
START=$(date +%s)
RC="$(HC_APK_URL="$U/hero-cantare-patched.apk" HC_NONINTERACTIVE=1 run_setup)"
ELAPSED=$(( $(date +%s) - START ))
wait "$FLAGGER" 2>/dev/null
check "exits 0"                    "[ '$RC' = 0 ]"
check "recognised it as patched"   "grep -q 'APK is patched' '$SANDBOX/run.log'"
check "opened the installer"       "grep -q '^termux-open' '$CALLS'"
check "waited for the install"     "grep -q 'Waiting for the install' '$SANDBOX/run.log'"
check "did not blow straight past it (${ELAPSED}s)" "[ '$ELAPSED' -ge 10 ]"
check "confirmed the game arrived" "grep -q 'Hero Cantare is installed' '$SANDBOX/run.log'"
check "still reached the end"      "grep -q 'Done\.' '$SANDBOX/run.log'"

# ================================================================ scenario 7 =
say "7. start.sh brings both servers up"
stop_servers
if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/ 2>/dev/null; then
    bad "something is already on port 8080 -- stop it and re-run"
else
    ( export HOME="$HOMEDIR" PATH="$BIN:$PATH"; bash "$BASE/start.sh" ) >/dev/null 2>&1 &
    SH_PID=$!
    for _ in $(seq 30); do
        grep -qi listening "$BASE/logs/game.log" 2>/dev/null && break
        sleep 1
    done
    sleep 2
    check "boot shim is listening"   "grep -qi listening '$BASE/logs/boot.log'"
    check "game server is listening" "grep -qi listening '$BASE/logs/game.log'"
    check "boot shim answers"        "curl -sf -o /dev/null 'http://127.0.0.1:8080/herocantare/serverinfo/dnsinfo.json'"
    check "game port accepts a TCP connection" \
          "python -c \"import socket;socket.create_connection(('127.0.0.1',21010),5).close()\""
    # A phone-only install has no reason to be reachable from the rest of the
    # café wifi. The boot shim already passes --bind; the game server didn't.
    check "game server bound to loopback, not 0.0.0.0" \
          "grep -qi 'listening on 127\.0\.0\.1' '$BASE/logs/game.log'"
    kill "$SH_PID" 2>/dev/null
    stop_servers
fi

# ===================================================================== done =
echo
if [ "$FAIL" -eq 0 ]; then
    printf '%sALL CHECKS PASSED%s (%d)\n' "$C_OK" "$C_OFF" "$PASS"
else
    printf '%s%d FAILED%s, %d passed -- log: %s\n' \
           "$C_ERR" "$FAIL" "$C_OFF" "$PASS" "$SANDBOX/run.log"
fi
exit $((FAIL > 0))
