#!/data/data/com.termux/files/usr/bin/bash
#
# Set up Heroic Chant to run on the phone itself, under Termux.
#
# Pair this with an APK built by tools/patch_apk.py, whose boot URLs point at
# 127.0.0.1:8080 -- then the game and the server are both on the phone and
# nothing else is involved. No PC, no root, no adb, no DNS.
#
# You need two files in your Downloads folder first:
#
#   heroic-chant-data.zip   built by tools/make_mobile_bundle.py (~12 MB)
#   ngelgames.zip           a zip of the client's files/ngelgames tree (~2 GB)
#
# Then, in Termux:
#
#   pkg install -y curl
#   curl -sL https://raw.githubusercontent.com/i-Ac1D-i/heroic-chant/main/tools/termux-setup.sh | bash
#
set -eu

REPO="${HC_REPO:-https://github.com/i-Ac1D-i/heroic-chant.git}"
BRANCH="${HC_BRANCH:-main}"
BASE="$HOME/heroic-chant"
SERVER="$BASE/server"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m !!\033[0m %s\n' "$*" >&2; exit 1; }

# --- packages ---------------------------------------------------------------
say "Installing packages"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python git unzip >/dev/null

# --- storage ----------------------------------------------------------------
# Termux cannot see the phone's Downloads folder until this is granted. It pops
# a permission dialog, so it has to happen before we go looking for the zips.
if [ ! -d "$HOME/storage" ]; then
    say "Requesting storage access (tap Allow)"
    termux-setup-storage
    sleep 3
fi

DL="$HOME/storage/downloads"
[ -d "$DL" ] || die "Can't see your Downloads folder. Run 'termux-setup-storage' and allow it, then re-run this."

# --- find the two zips ------------------------------------------------------
find_zip() {
    # $1 = a glob to try in Downloads, newest match wins
    ls -t "$DL"/$1 2>/dev/null | head -1 || true
}

DATA_ZIP="${HC_DATA_ZIP:-$(find_zip 'heroic-chant-data*.zip')}"
FILES_ZIP="${HC_FILES_ZIP:-$(find_zip 'ngelgames*.zip')}"
[ -n "$DATA_ZIP" ]  || die "heroic-chant-data.zip not found in $DL"
[ -n "$FILES_ZIP" ] || die "ngelgames.zip not found in $DL"
say "Data bundle : $(basename "$DATA_ZIP")"
say "Client files: $(basename "$FILES_ZIP") ($(du -h "$FILES_ZIP" | cut -f1))"

# --- repo -------------------------------------------------------------------
mkdir -p "$BASE"
if [ -d "$SERVER/.git" ]; then
    say "Updating the server"
    git -C "$SERVER" pull --ff-only origin "$BRANCH" >/dev/null 2>&1 || \
        warn "couldn't fast-forward; keeping what's already there"
else
    say "Cloning the server"
    git clone --depth 1 -b "$BRANCH" "$REPO" "$SERVER" >/dev/null
fi

# --- unpack the generated data ---------------------------------------------
# spec.json and data/ are gitignored on purpose (they're game-derived), so
# they arrive in the bundle rather than in the clone.
say "Unpacking game data"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q -o "$DATA_ZIP" -d "$TMP"
[ -f "$TMP/spec.json" ] || die "$DATA_ZIP has no spec.json -- is it the right bundle?"
mkdir -p "$SERVER/hc/protocol"
cp "$TMP/spec.json" "$SERVER/hc/protocol/spec.json"
rm -rf "$SERVER/data"
cp -r "$TMP/data" "$SERVER/data"
cp "$TMP/herocantare.db" "$BASE/herocantare.db"

# The 2 GB of AssetBundles are served straight out of the zip -- the game is
# about to download its own copy anyway, so unpacking ours would cost another
# 2 GB for nothing.
cp -n "$FILES_ZIP" "$BASE/ngelgames.zip" 2>/dev/null || true
[ -f "$BASE/ngelgames.zip" ] || ln -sf "$FILES_ZIP" "$BASE/ngelgames.zip"

# --- launcher ---------------------------------------------------------------
cat > "$BASE/start.sh" <<'LAUNCHER'
#!/data/data/com.termux/files/usr/bin/bash
# Start both halves of Heroic Chant. Ctrl+C stops them.
set -eu
BASE="$HOME/heroic-chant"
cd "$BASE/server"

export HC_DB_PATH="$BASE/herocantare.db"
export HC_CLIENT_FILES="$BASE/ngelgames.zip"

mkdir -p "$BASE/logs"
cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

python tools/bootserver.py --host 127.0.0.1 --http-port 8080 --no-dns \
    --bind 127.0.0.1 > "$BASE/logs/boot.log" 2>&1 &
python -m hc.main --public-host 127.0.0.1 --port 21010 \
    > "$BASE/logs/game.log" 2>&1 &

sleep 3
for f in boot game; do
    if ! grep -qiE 'listening|handlers|serving' "$BASE/logs/$f.log" 2>/dev/null; then
        echo "!! $f server may have failed to start:"
        tail -5 "$BASE/logs/$f.log" 2>/dev/null || true
    fi
done

echo
echo "Heroic Chant is running."
echo "  boot shim  : 127.0.0.1:8080   ($BASE/logs/boot.log)"
echo "  game server: 127.0.0.1:21010  ($BASE/logs/game.log)"
echo
echo "Leave this running, switch to the Hero Cantare app, and play."
echo "First launch downloads ~2 GB from the server over loopback -- that's"
echo "the game populating its own asset folder. It only happens once."
echo
echo "Ctrl+C to stop."
wait
LAUNCHER
chmod +x "$BASE/start.sh"

# --- check ------------------------------------------------------------------
say "Checking the install"
cd "$SERVER"
if HC_DB_PATH="$BASE/herocantare.db" python tools/selftest.py 2>&1 | tail -1 | grep -q PASSED; then
    say "Self-test passed"
else
    warn "Self-test did not pass -- run 'python tools/selftest.py' in $SERVER to see why"
fi

cat <<EOF

  Done.

  Start the server:   ~/heroic-chant/start.sh

  Then switch to the Hero Cantare app and play. Keep Termux running in the
  background -- if Android kills it the game loses its server. Running
  'termux-wake-lock' first helps.

EOF
