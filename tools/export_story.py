"""Export the story cutscenes and dialogue as readable text.

Two sources, both already in the client:

* ``Scenarios.json``  -- the cutscene scripts.  Each scenario is a list of
  groups (background image + sound), each group a list of parts, each part a
  speaking character (``charactorNameID``) and a line (``dialog.stringID``).
* ``storyInfo.json``  -- in-battle chatter, keyed by story set; rows with
  ``storyType == 1`` carry a dialogue ``stringID``, type 2 rows are buff
  descriptions and type 3 are rarer effects.

String ids resolve through the ``hc_string_<Language>`` tables in
herocantare.db, so any of Korean, English, Japanese, French or
ChineseTraditional can be dumped.

    python tools/export_story.py --lang English -o story/
    python tools/export_story.py --scenario 12 --lang Korean
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from hc.data.tables import TABLES, to_int          # noqa: E402

LANGS = ('Korean', 'English', 'Japanese', 'French', 'ChineseTraditional')


def scenario_lines(scenario, strings):
    """Yield ('speaker', 'line') / ('--', 'stage direction') pairs.

    A scene keeps the inactive speaker's box on screen alongside the active
    one, and those held boxes are emitted as extra parts carrying the same
    stringID with ``dialog.type == 0``.  Skipping them is what turns the raw
    parts list back into the script as it reads on screen.
    """
    for group in scenario.get('group', []):
        bg = group.get('backgroundImage', -1)
        if bg and bg != -1:
            yield ('--', '[background %s]' % bg)
        for part in group.get('parts', []):
            dialog = part.get('dialog') or {}
            sid = dialog.get('stringID', -1)
            if sid is None or sid < 0 or dialog.get('type') == 0:
                continue
            char = part.get('charactor') or {}
            speaker = strings.get(char.get('charactorNameID', -1), '')
            yield (speaker or '???', strings.get(sid, '<missing string %d>' % sid))


def write_scenarios(out, strings, only=None):
    data = TABLES.json('Scenarios')['data']
    n_sc = n_lines = 0
    for sc in data:
        sid = sc.get('scenarioID')
        if only is not None and sid != only:
            continue
        lines = list(scenario_lines(sc, strings))
        if not lines:
            continue
        n_sc += 1
        out.write('\n=== scenario %s ===\n' % sid)
        for speaker, text in lines:
            if speaker == '--':
                out.write('  %s\n' % text)
            else:
                out.write('  %s: %s\n' % (speaker, text.replace('\n', '\n      ')))
                n_lines += 1
    return n_sc, n_lines


def write_story_info(out, strings):
    rows = TABLES.json('storyInfo')
    by_set = {}
    for r in rows:
        if to_int(r['storyType']) != 1:
            continue
        by_set.setdefault(to_int(r['storySetID']), []).append(r)
    n = 0
    for set_id in sorted(by_set):
        out.write('\n=== story set %d ===\n' % set_id)
        for r in sorted(by_set[set_id], key=lambda x: to_int(x['storyIndex'])):
            text = strings.get(to_int(r['storyValue']), '<missing>')
            out.write('  %s\n' % text.replace('\n', '\n    '))
            n += 1
    return len(by_set), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lang', default='English', choices=LANGS)
    ap.add_argument('--scenario', type=int, help='export just one scenario id')
    ap.add_argument('-o', '--out', help='directory to write .txt files into '
                                        '(default: print to stdout)')
    args = ap.parse_args()

    strings = TABLES.strings(args.lang)

    buf = io.StringIO()
    n_sc, n_lines = write_scenarios(buf, strings, args.scenario)
    scenarios_text = buf.getvalue()

    buf2 = io.StringIO()
    n_sets, n_chatter = write_story_info(buf2, strings)
    story_text = buf2.getvalue()

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        a = os.path.join(args.out, 'cutscenes_%s.txt' % args.lang)
        b = os.path.join(args.out, 'battle_dialogue_%s.txt' % args.lang)
        with open(a, 'w', encoding='utf-8') as fh:
            fh.write(scenarios_text)
        with open(b, 'w', encoding='utf-8') as fh:
            fh.write(story_text)
        print('%d scenarios, %d spoken lines -> %s' % (n_sc, n_lines, a))
        print('%d story sets, %d lines      -> %s' % (n_sets, n_chatter, b))
    else:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.write(scenarios_text)
        sys.stdout.write(story_text)


if __name__ == '__main__':
    main()
