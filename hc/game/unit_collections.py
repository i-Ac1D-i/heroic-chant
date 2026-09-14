from ..data.tables import TABLES, to_int


def _rows():
    return TABLES.sql('unitCollection')


def _hero_ids(row):
    return [to_int(row[f'HeroID{i}']) for i in range(1, 6)
            if to_int(row.get(f'HeroID{i}'), -1) >= 0]


def _grade_sum(player, row):
    total = 0
    for hid in _hero_ids(row):
        u = next((u for u in player.d['units'] if int(u['id']) == hid), None)
        if u is None:
            return -1
        total += int(u.get('grade', 0))
    return total


def all_ids():
    return sorted({to_int(r['ID']) for r in _rows()})


def steps_for(set_id):
    return sorted((r for r in _rows() if to_int(r['ID']) == int(set_id)),
                 key=lambda r: to_int(r['Step']))


def achievable_step(player, set_id):
    best = 0
    for row in steps_for(set_id):
        if _grade_sum(player, row) < to_int(row['GradeCondition']):
            break
        best = to_int(row['Step'])
    return best


def claimed(player):
    return player.d.setdefault('unit_collections', {})


def claimed_step(player, set_id):
    return int(claimed(player).get(str(set_id), 0))


def unlock(player, set_id):
    step = achievable_step(player, set_id)
    if step <= claimed_step(player, set_id):
        return None
    claimed(player)[str(set_id)] = step
    return step


def info(set_id, step):
    from ..protocol.dto import TYPES
    return TYPES['NGUnitCollection'](ID=set_id, Step=step)


def infos(player):
    return [info(int(sid), step) for sid, step in claimed(player).items() if step > 0]
