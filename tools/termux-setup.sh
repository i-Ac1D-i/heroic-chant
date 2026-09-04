#!/data/data/com.termux/files/usr/bin/bash
#
# Heroic Chant, all on the phone. Install and update, in one command.
#
#   pkg install -y curl && curl -sL https://raw.githubusercontent.com/i-Ac1D-i/heroic-chant/main/tools/termux-setup.sh | bash
#
# Safe to re-run. On a second run it checks the remote repo for new commits and
# pulls them, and skips any download it already has. That is the intended way
# to pick up new features -- just run it again.
#
# It will, in order:
#   1. install packages
#   2. update (or clone) the server from GitHub, reporting what changed
#   3. fetch the game data it is missing
#   4. install the game if it isn't installed
#   5. write ~/heroic-chant/start.sh
#
# Override any download with an env var, e.g. to point at your own mirror:
#   HC_FILES_URL=https://example/files.zip bash termux-setup.sh
#
set -eu

REPO="${HC_REPO:-https://github.com/i-Ac1D-i/heroic-chant.git}"
BRANCH="${HC_BRANCH:-main}"
BASE="$HOME/heroic-chant"
SERVER="$BASE/server"
PKG_NAME="com.ngelgames.herocantare"

# Google Drive file ids. A full URL in HC_*_URL wins over these.
: "${HC_APK_ID:=1hayI4L--WCQbv4QEMLVLbg07Pkr-Sx8a}"
: "${HC_FILES_ID:=1K-_zXE0j7W3zQ39cx2OML4iIa_h31jEd}"
: "${HC_DB_ID:=1U3LYz0VUOmfTQcFQe3v7jqJliBxMJ6ci}"
# spec.json + the JSON tables, from tools/make_mobile_bundle.py --no-db.
# The phone cannot build these itself, so they have to be hosted.
: "${HC_DATA_ID:=1PEEe0K29jxq0Fnpc92i3-Ztp95r-Uwcl}"

C_OK=$'\033[1;32m'; C_INFO=$'\033[1;36m'; C_WARN=$'\033[1;33m'
C_ERR=$'\033[1;31m'; C_OFF=$'\033[0m'
say()  { printf '%s==>%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf '%s  ok%s %s\n' "$C_OK"   "$C_OFF" "$*"; }
warn() { printf '%s  !!%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%s  !!%s %s\n' "$C_ERR"  "$C_OFF" "$*" >&2; exit 1; }
human() { du -h "$1" 2>/dev/null | cut -f1; }

# --------------------------------------------------------------- packages --
say "Installing packages"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python git unzip curl >/dev/null 2>&1 || \
    die "pkg install failed. If you got Termux from the Play Store, uninstall it and use the F-Droid build -- the Play Store one is abandoned and its repos are dead."
ok "python $(python --version 2>&1 | cut -d' ' -f2), git, unzip, curl"

# ---------------------------------------------------------------- storage --
# Needed to reach Downloads (where we look for files you already have) and to
# hand the APK to the package installer.
if [ ! -d "$HOME/storage" ]; then
    say "Requesting storage access -- tap Allow"
    termux-setup-storage || true
    sleep 3
fi
DL="$HOME/storage/downloads"
[ -d "$DL" ] || { warn "no access to Downloads; will download everything fresh"; DL=""; }

mkdir -p "$BASE"

# ------------------------------------------------------------------ repo --
# The update check. On a re-run this is the whole point: see what is new
# upstream, pull it, and say what changed.
if [ -d "$SERVER/.git" ]; then
    say "Checking for server updates"
    git -C "$SERVER" remote set-url origin "$REPO" 2>/dev/null || true
    if git -C "$SERVER" fetch --quiet origin "$BRANCH" 2>/dev/null; then
        LOCAL="$(git -C "$SERVER" rev-parse HEAD)"
        REMOTE="$(git -C "$SERVER" rev-parse "origin/$BRANCH")"
        if [ "$LOCAL" = "$REMOTE" ]; then
            ok "already up to date ($(git -C "$SERVER" log -1 --format=%h))"
        else
            N="$(git -C "$SERVER" rev-list --count HEAD.."origin/$BRANCH" 2>/dev/null || echo '?')"
            say "$N new commit(s):"
            git -C "$SERVER" log --oneline --no-decorate HEAD.."origin/$BRANCH" 2>/dev/null \
                | head -15 | sed 's/^/     /'
            if git -C "$SERVER" merge --ff-only "origin/$BRANCH" >/dev/null 2>&1; then
                ok "updated to $(git -C "$SERVER" log -1 --format=%h)"
            else
                warn "can't fast-forward -- you have local edits. Stash or reset them,"
                warn "or delete $SERVER and re-run to get a clean copy."
            fi
        fi
    else
        warn "couldn't reach GitHub; carrying on with the copy you have"
    fi
else
    say "Downloading the server"
    git clone --depth 1 -b "$BRANCH" "$REPO" "$SERVER" >/dev/null 2>&1 \
        || die "git clone failed -- check your connection"
    ok "cloned $(git -C "$SERVER" log -1 --format=%h)"
fi

# ------------------------------------------------------------- downloads --
# Google Drive refuses a plain GET for anything big, answering with an
# interstitial HTML page instead. The usercontent host with confirm=t skips it.
# -C - resumes, which matters a lot for a 2 GB file on a phone.
fetch() {
    local url="$1" dest="$2" name="$3"
    say "Downloading $name"
    if ! curl -L --fail --retry 3 --retry-delay 5 --retry-connrefused \
              -C - -o "$dest" "$url"; then
        # A completed file makes curl exit 33 ("range not supported"); retry whole.
        rm -f "$dest"
        curl -L --fail --retry 3 --retry-delay 5 -o "$dest" "$url" \
            || die "download failed: $name"
    fi
}

gdrive_url() { printf 'https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t' "$1"; }

# Reject Drive's HTML interstitial / quota page, which otherwise lands on disk
# with a .zip name and fails much later with a confusing unzip error.
verify() {
    local f="$1" kind="$2" name="$3"
    [ -s "$f" ] || { rm -f "$f"; die "$name came back empty"; }
    if head -c 512 "$f" | grep -qiE '<!doctype html|<html|Google Drive - Quota'; then
        rm -f "$f"
        die "$name: Google Drive returned a web page, not the file. Usually the daily download quota. Try later, or download it in a browser into your Downloads folder and re-run."
    fi
    case "$kind" in
        zip) head -c 2 "$f" | grep -q 'PK' || { rm -f "$f"; die "$name is not a zip"; } ;;
        db)  head -c 15 "$f" | grep -q 'SQLite format 3' || { rm -f "$f"; die "$name is not a SQLite database"; } ;;
    esac
    ok "$name ($(human "$f"))"
}

# Use a copy already in Downloads rather than pulling it again.
adopt() {
    local glob="$1" dest="$2"
    [ -n "$DL" ] || return 1
    local hit; hit="$(ls -t "$DL"/$glob 2>/dev/null | head -1 || true)"
    [ -n "$hit" ] || return 1
    say "Using $(basename "$hit") from Downloads"
    cp "$hit" "$dest"
}

need() { [ ! -s "$1" ]; }

# 1. Client files (~2 GB) -- served back to the game as its CDN.
FILES_ZIP="$BASE/ngelgames.zip"
if need "$FILES_ZIP"; then
    adopt 'files*.zip' "$FILES_ZIP" || adopt 'ngelgames*.zip' "$FILES_ZIP" || \
        fetch "${HC_FILES_URL:-$(gdrive_url "$HC_FILES_ID")}" "$FILES_ZIP" "client files (~2 GB, this is the long one)"
    verify "$FILES_ZIP" zip "client files"
else
    ok "client files already here ($(human "$FILES_ZIP"))"
fi

# 2. herocantare.db -- the 172 SQLite tables.
DB="$BASE/herocantare.db"
if need "$DB"; then
    adopt 'herocantare*.db' "$DB" || \
        fetch "${HC_DB_URL:-$(gdrive_url "$HC_DB_ID")}" "$DB" "herocantare.db (47 MB)"
    verify "$DB" db "herocantare.db"
else
    ok "herocantare.db already here ($(human "$DB"))"
fi

# 3. spec.json + data/ -- generated on a desktop, cannot be built here.
if [ ! -s "$SERVER/hc/protocol/spec.json" ] || [ ! -d "$SERVER/data" ]; then
    DATA_ZIP="$BASE/heroic-chant-data.zip"
    if need "$DATA_ZIP"; then
        adopt 'heroic-chant-data*.zip' "$DATA_ZIP" || {
            case "$HC_DATA_ID" in
                REPLACE_*) die "The packet spec and data tables aren't hosted yet.
     Build them on a desktop with:
         python tools/make_mobile_bundle.py --no-db
     then put heroic-chant-data.zip in your Downloads folder and re-run,
     or set HC_DATA_URL to where you host it." ;;
            esac
            fetch "${HC_DATA_URL:-$(gdrive_url "$HC_DATA_ID")}" "$DATA_ZIP" "packet spec + data tables (~2 MB)"
        }
        verify "$DATA_ZIP" zip "data bundle"
    fi
    say "Unpacking the data tables"
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    unzip -q -o "$DATA_ZIP" -d "$TMP"
    [ -f "$TMP/spec.json" ] || die "that zip has no spec.json -- wrong bundle?"
    mkdir -p "$SERVER/hc/protocol"
    cp "$TMP/spec.json" "$SERVER/hc/protocol/spec.json"
    rm -rf "$SERVER/data"; cp -r "$TMP/data" "$SERVER/data"
    [ -f "$TMP/herocantare.db" ] && [ ! -s "$DB" ] && cp "$TMP/herocantare.db" "$DB"
    ok "spec.json + $(ls "$SERVER"/data/*/*.json 2>/dev/null | wc -l) tables"
else
    ok "packet spec and data tables already in place"
fi

# ------------------------------------------------------------------- game --
installed() {
    pm list packages 2>/dev/null | grep -q "^package:$PKG_NAME$" && return 0
    [ -d "/storage/emulated/0/Android/data/$PKG_NAME" ] && return 0
    return 1
}

if installed; then
    ok "Hero Cantare is installed"
else
    say "Hero Cantare isn't installed"
    APK="${DL:-$BASE}/hero-cantare.apk"
    if need "$APK"; then
        adopt '*herocantare*.apk' "$APK" || adopt '*heroicchant*.apk' "$APK" || \
            fetch "${HC_APK_URL:-$(gdrive_url "$HC_APK_ID")}" "$APK" "the game (138 MB)"
        verify "$APK" zip "APK"
    fi

    # An unpatched APK still points at dlhc.ngelgames.net, which is dead and
    # which an unrooted phone cannot redirect. It would install fine and then
    # hang forever on the loading bar with nothing in any log, so check first.
    if python "$SERVER/tools/patch_apk.py" --check "$APK" >/dev/null 2>&1; then
        ok "APK is patched for a local server"
        say "Opening the installer -- tap Install, then come back here"
        termux-open --content-type application/vnd.android.package-archive "$APK" 2>/dev/null \
            || am start -a android.intent.action.VIEW -t application/vnd.android.package-archive \
                 -d "file://$APK" >/dev/null 2>&1 \
            || warn "couldn't open the installer; install $APK yourself from a file manager"
        echo
        read -r -p "  Press Enter once the install has finished... " _ || true
    else
        warn "That APK still points at the dead official CDN, so it will not work"
        warn "with a local server -- it would just hang on the loading screen."
        warn ""
        warn "It needs patching on a desktop first:"
        warn "    python tools/patch_apk.py <apk>"
        warn "then host the -heroicchant.apk it produces and set HC_APK_URL to it."
        warn ""
        warn "Skipping the install. Everything else is set up."
    fi
fi

# ---------------------------------------------------------------- launcher --
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

termux-wake-lock 2>/dev/null || true

python tools/bootserver.py --host 127.0.0.1 --http-port 8080 --no-dns \
    --bind 127.0.0.1 > "$BASE/logs/boot.log" 2>&1 &
python -m hc.main --public-host 127.0.0.1 --port 21010 \
    > "$BASE/logs/game.log" 2>&1 &

sleep 3
for f in boot game; do
    grep -qiE 'listening' "$BASE/logs/$f.log" 2>/dev/null || {
        echo "!! the $f server didn't start:"; tail -5 "$BASE/logs/$f.log" 2>/dev/null; }
done

echo
echo "Heroic Chant is running."
echo "  boot shim  : 127.0.0.1:8080   ($BASE/logs/boot.log)"
echo "  game server: 127.0.0.1:21010  ($BASE/logs/game.log)"
echo
echo "Leave this running and switch to the Hero Cantare app."
echo "On first launch it asks to download ~860 MB -- tap OK. That's the game"
echo "pulling its assets from this server over loopback; nothing leaves the"
echo "phone, and it only happens once."
echo
echo "Ctrl+C to stop."
wait
LAUNCHER
chmod +x "$BASE/start.sh"

# ------------------------------------------------------------------ check --
say "Running the self-test"
if (cd "$SERVER" && HC_DB_PATH="$DB" python tools/selftest.py 2>&1 | tail -1 | grep -q PASSED); then
    ok "self-test passed -- the server works"
else
    warn "self-test failed. Run this to see why:"
    warn "    cd $SERVER && HC_DB_PATH=$DB python tools/selftest.py"
fi

cat <<EOF

  ${C_OK}Done.${C_OFF}

  Start it:   ~/heroic-chant/start.sh
  Update it:  re-run this same command any time

  Then switch to Hero Cantare and play. Keep Termux running in the background;
  if Android kills it the game loses its server. start.sh takes a wake-lock,
  but also set Termux to Unrestricted under Settings > Apps > Termux > Battery.

EOF
