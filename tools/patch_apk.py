"""Build a client APK that talks to a server running on the same phone.

The retail client hardcodes two boot URLs in the il2cpp string-literal blob
inside ``assets/bin/Data/Managed/Metadata/global-metadata.dat``:

    http://dlhc.ngelgames.net/herocantare/patchinfo/      (48 bytes)
    http://dlhc.ngelgames.net/herocantare/serverinfo/     (49 bytes)

Both hosts are dead.  On a PC setup we redirect them with /etc/hosts or a DNS
shim, but neither is available on an unrooted phone -- and even with a redirect
the client would dial port 80, which an unprivileged Android app cannot bind.
So we rewrite the URLs to point at ``127.0.0.1:8080`` instead.

The trick that makes this safe: il2cpp stores every string literal with an
explicit length in a side table, so a replacement of a *different* length would
mean rewriting that table and every offset after it.  We avoid all of that by
padding the path with extra slashes until the replacement is byte-for-byte the
same length as the original:

    http://127.0.0.1:8080/////herocantare/patchinfo/      (48 bytes)

An HTTP server collapses the repeated slashes, so the padding costs nothing --
``bootserver.py`` normalises the path before routing.  Nothing else in the
binary moves, which is why this needs no apktool, no smali and no manifest
surgery.

Everything downstream of those two URLs is already under our control: the
patchinfo JSON we serve carries ``serverip``/``serverport`` (so the game server
address needs no patch at all) and ``cdninfo`` (so the client downloads its own
AssetBundles from us, into its own private directory, exactly as it did in
2023 -- which is why this needs no root either).

    python tools/patch_apk.py ../com.ngelgames.herocantare_1.2.389.apk
    python tools/patch_apk.py in.apk --host 192.168.1.50 --port 8080 -o out.apk

Requires: Android SDK build-tools (zipalign, apksigner) and a JDK (keytool).
"""
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

METADATA = 'assets/bin/Data/Managed/Metadata/global-metadata.dat'

# The two boot URLs, as they appear in the string-literal blob.
BOOT_URLS = [
    b'http://dlhc.ngelgames.net/herocantare/patchinfo/',
    b'http://dlhc.ngelgames.net/herocantare/serverinfo/',
]

# Signature files from the original signing.  These must go: we re-sign, and a
# stale signature block makes the installer reject the APK.  Note that META-INF
# also holds real resources (the androidx *.version files), so we drop by
# extension rather than by prefix.
SIG_SUFFIXES = ('.RSA', '.DSA', '.EC', '.SF')


def build_url(original, host, port):
    """A same-length replacement for one boot URL, padded with slashes.

    Returns the new bytes, or raises ValueError if `host:port` is simply too
    long to fit -- there is no way to grow the string without rewriting the
    il2cpp length table.
    """
    tail = original.split(b'/herocantare/')[1]          # b'patchinfo/'
    base = ('http://%s:%d/' % (host, port)).encode()
    body = b'herocantare/' + tail
    pad = len(original) - len(base) - len(body)
    if pad < 0:
        raise ValueError(
            '%s:%d is %d bytes too long to fit in place of %s -- use a shorter '
            'host or port' % (host, port, -pad, original.decode()))
    return base + b'/' * pad + body


def patch_metadata(blob, host, port):
    """Rewrite the boot URLs in global-metadata.dat.  Returns (blob, notes)."""
    notes = []
    for original in BOOT_URLS:
        n = blob.count(original)
        if n == 0:
            raise SystemExit(
                'boot URL not found in global-metadata.dat: %s\n'
                'This build may not be 1.2.389.' % original.decode())
        if n > 1:
            # Never seen; if it happens, patching all of them is still correct.
            notes.append('%s appears %d times' % (original.decode(), n))
        new = build_url(original, host, port)
        assert len(new) == len(original), 'length drift'
        blob = blob.replace(original, new)
        notes.append('%s -> %s' % (original.decode(), new.decode()))
    return blob, notes


def find_tool(name):
    """Locate an Android build-tool, preferring the newest build-tools dir."""
    for cand in (name, name + '.exe', name + '.bat'):
        found = shutil.which(cand)
        if found:
            return found
    roots = [
        os.path.expanduser(r'~\AppData\Local\Android\Sdk\build-tools'),
        os.path.expanduser('~/Android/Sdk/build-tools'),
        os.path.expanduser('~/Library/Android/sdk/build-tools'),
        r'C:\build_tools',
    ]
    env = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if env:
        roots.insert(0, os.path.join(env, 'build-tools'))
    for root in roots:
        if not os.path.isdir(root):
            continue
        # C:\build_tools is a flat directory; the SDK one has version subdirs.
        for sub in sorted(os.listdir(root), reverse=True) + ['']:
            for cand in (name, name + '.exe', name + '.bat'):
                p = os.path.join(root, sub, cand) if sub else os.path.join(root, cand)
                if os.path.isfile(p):
                    return p
    return None


def run(cmd, what):
    """Run a build tool, surfacing its own error text on failure."""
    # .bat wrappers need a shell on Windows; everything else does not.
    shell = cmd[0].lower().endswith('.bat')
    r = subprocess.run(subprocess.list2cmdline(cmd) if shell else cmd,
                       shell=shell, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('%s failed:\n%s\n%s' % (what, r.stdout.strip(), r.stderr.strip()))
    return r.stdout


def repack(src, dst, metadata):
    """Copy the APK entry by entry, swapping in the patched metadata.

    Compression type is preserved per entry -- resources.arsc is STOREd in this
    build and must stay that way, and re-deflating everything would change
    alignment for no reason.
    """
    kept = dropped = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
        for info in zin.infolist():
            if info.filename.upper().endswith(SIG_SUFFIXES) or \
                    info.filename.upper() == 'META-INF/MANIFEST.MF':
                dropped += 1
                continue
            data = metadata if info.filename == METADATA else zin.read(info.filename)
            # Carry the original entry across verbatim apart from its content,
            # so stored entries stay stored and dates do not churn.
            out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            out.compress_type = info.compress_type
            out.external_attr = info.external_attr
            out.internal_attr = info.internal_attr
            out.create_system = info.create_system
            zout.writestr(out, data)
            kept += 1
    return kept, dropped


def ensure_keystore(path, alias, password):
    """Create a self-signed key if there isn't one.  Sideloading only."""
    if os.path.exists(path):
        return False
    keytool = find_tool('keytool')
    if not keytool:
        sys.exit('keytool not found -- install a JDK, or pass --keystore')
    run([keytool, '-genkeypair', '-v',
         '-keystore', path, '-alias', alias,
         '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000',
         '-storepass', password, '-keypass', password,
         '-dname', 'CN=Heroic Chant, OU=Private Server, O=Heroic Chant, C=US'],
        'keytool')
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('apk', help='the original 1.2.389 APK')
    ap.add_argument('-o', '--out', help='output APK (default: <name>-heroicchant.apk)')
    ap.add_argument('--host', default='127.0.0.1',
                    help='where the client should look for the boot server. '
                         'Leave as 127.0.0.1 for the phone-only setup; set a LAN '
                         'IP if the server runs on another machine (default: %(default)s)')
    ap.add_argument('--port', type=int, default=8080,
                    help='boot server port. Must be >1024 so an unprivileged '
                         'Android app can bind it (default: %(default)s)')
    ap.add_argument('--keystore', default=None,
                    help='signing keystore (default: tools/heroicchant.keystore, '
                         'created on first run)')
    ap.add_argument('--ks-pass', default='heroicchant')
    ap.add_argument('--ks-alias', default='heroicchant')
    ap.add_argument('--no-sign', action='store_true',
                    help='stop after zipalign, leaving an unsigned APK')
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.abspath(args.apk)
    if not os.path.isfile(src):
        sys.exit('no such APK: %s' % src)
    out = os.path.abspath(
        args.out or os.path.splitext(src)[0] + '-heroicchant.apk')
    keystore = os.path.abspath(args.keystore or
                               os.path.join(here, 'heroicchant.keystore'))
    staging = out + '.unaligned'

    print('reading   %s (%.0f MB)' % (os.path.basename(src),
                                      os.path.getsize(src) / 1e6))
    with zipfile.ZipFile(src) as z:
        try:
            blob = z.read(METADATA)
        except KeyError:
            sys.exit('%s not found -- is this a Unity il2cpp APK?' % METADATA)

    blob, notes = patch_metadata(blob, args.host, args.port)
    print('patching  global-metadata.dat')
    for n in notes:
        print('            %s' % n)

    kept, dropped = repack(src, staging, blob)
    print('repacked  %d entries (dropped %d signature files)' % (kept, dropped))

    zipalign = find_tool('zipalign')
    if not zipalign:
        sys.exit('zipalign not found -- install Android SDK build-tools, or set '
                 'ANDROID_HOME')
    if os.path.exists(out):
        os.remove(out)
    # -p page-aligns .so entries; harmless here (they are deflated) but correct
    # if a future build ships them uncompressed.
    run([zipalign, '-p', '-f', '4', staging, out], 'zipalign')
    os.remove(staging)
    print('aligned   %s' % os.path.basename(out))

    if args.no_sign:
        print('\nunsigned APK at %s' % out)
        return 0

    apksigner = find_tool('apksigner')
    if not apksigner:
        sys.exit('apksigner not found -- install Android SDK build-tools')
    if ensure_keystore(keystore, args.ks_alias, args.ks_pass):
        print('created   %s' % os.path.basename(keystore))
    run([apksigner, 'sign', '--ks', keystore,
         '--ks-key-alias', args.ks_alias,
         '--ks-pass', 'pass:' + args.ks_pass,
         '--key-pass', 'pass:' + args.ks_pass,
         out], 'apksigner sign')
    verify = run([apksigner, 'verify', '--verbose', out], 'apksigner verify')
    scheme = [ln for ln in verify.splitlines() if 'scheme' in ln.lower()]
    print('signed    %s' % ('; '.join(s.strip() for s in scheme) or 'ok'))

    print('\ndone: %s (%.0f MB)' % (out, os.path.getsize(out) / 1e6))
    print('\nInstall it on the phone, then start the server with')
    print('    python -m hc.main --public-host %s' % args.host)
    print('    python tools/bootserver.py --host %s --http-port %d --no-dns --cdn ...'
          % (args.host, args.port))
    print('\nThe patched client will not talk to the retail servers, and the '
          'original APK must be uninstalled first (different signature).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
