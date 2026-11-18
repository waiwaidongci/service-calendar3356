"""两地营业日历分钟网格引擎。

把每个日历的营业时窗展开到 UTC 分钟网格（夏令时安全），两地网格按位与得到
“双方同时营业”的分钟，前缀和支撑 O(1) 区间查询，避免逐请求逐日展开。

口径约定：
- 跨午夜班次（结束分钟大于1440）归开班日，按开班日判定工作日与假日；
- 夏令时回拨产生的重复墙钟分钟，两段实际时间都计入；
- 春季跳时不存在的墙钟分钟不计入。
"""
import hashlib
import json
import math
from array import array
from datetime import datetime, timedelta, time, timezone
from itertools import accumulate
from zoneinfo import ZoneInfo

_UINT = 'I' if array('I').itemsize >= 4 else 'L'
_MIN = timedelta(minutes=1)


def minute_floor(dt):
    return dt.replace(second=0, microsecond=0)


def wall_utc_instants(zone, naive):
    """本地墙钟分钟对应的 UTC 时刻列表：0 个（跳时不存在）、1 个、2 个（回拨重复）。"""
    out = []
    for fold in (0, 1):
        dt = naive.replace(tzinfo=zone, fold=fold)
        if dt.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            u = dt.astimezone(timezone.utc)
            if u not in out:
                out.append(u)
    return out


def calendar_bitmap(cal, grid_start, cells):
    """把日历在 [grid_start, grid_start+cells分钟) 内的营业分钟标记为 1。

    grid_start 为分钟对齐的 UTC 时刻。非夏令时转换日走线性快路径；
    转换日逐墙钟分钟解析（回拨重复分钟标记两段 UTC，跳时分钟不标记）。
    """
    zone = ZoneInfo(cal['timezone'])
    bm = bytearray(cells)
    holidays = set(cal.get('holidays', []))
    weekdays = set(cal['weekdays'])
    windows = cal['windows']
    max_end = max(e for _, e in windows)
    grid_end = grid_start + cells * _MIN
    # 前后留余量：跨午夜班次可从上一本地日探入网格（结束分钟小于2880，余量2天足够）
    day = (grid_start.astimezone(zone) - timedelta(days=2)).date()
    last = (grid_end.astimezone(zone) + timedelta(days=1)).date()
    while day <= last:
        if day.weekday() in weekdays and day.isoformat() not in holidays:
            mid = datetime.combine(day, time())
            u0 = wall_utc_instants(zone, mid)
            u1 = wall_utc_instants(zone, mid + timedelta(minutes=max_end))
            if len(u0) == 1 and len(u1) == 1 and u1[0] - u0[0] == timedelta(minutes=max_end):
                base = round((u0[0] - grid_start).total_seconds() / 60)
                for s, e in windows:
                    lo = max(0, base + s)
                    hi = min(cells, base + e)
                    if hi > lo:
                        bm[lo:hi] = b'\x01' * (hi - lo)
            else:
                for s, e in windows:
                    for i in range(s, e):
                        for u in wall_utc_instants(zone, mid + timedelta(minutes=i)):
                            idx = round((u - grid_start).total_seconds() / 60)
                            if 0 <= idx < cells:
                                bm[idx] = 1
        day += timedelta(days=1)
    return bm


def combine(home_bm, away_bm):
    """两地分钟网格按位与：只保留双方同时营业的分钟。"""
    n = len(home_bm)
    return (int.from_bytes(home_bm, 'little') & int.from_bytes(away_bm, 'little')).to_bytes(n, 'little')


def prefix_sums(both):
    """pref[k] 为前 k 个网格分钟的营业分钟数，支撑 O(1) 区间查询。"""
    return array(_UINT, accumulate(both, initial=0))


class Grid:
    __slots__ = ('start', 'cells', 'both', 'pref')

    def __init__(self, start, cells, both, pref):
        self.start = start
        self.cells = cells
        self.both = both
        self.pref = pref

    def covers(self, lo, hi):
        return self.start <= lo and self.start + self.cells * _MIN >= hi


def build_grid(home, away, lo, hi):
    """构建覆盖 [lo, hi]（UTC 时刻）的双方同时营业分钟网格。"""
    start = minute_floor(lo.astimezone(timezone.utc))
    cells = max(1, math.ceil((hi - start).total_seconds() / 60))
    both = combine(calendar_bitmap(home, start, cells), calendar_bitmap(away, start, cells))
    return Grid(start, cells, both, prefix_sums(both))


def span_minutes(grid, a, b):
    """[a,b) 内双方同时营业的精确分钟，边界不足一分钟按秒折算。"""
    both = grid.both
    pref = grid.pref
    cells = grid.cells
    sa = (a - grid.start).total_seconds()
    sb = (b - grid.start).total_seconds()
    if sb <= sa:
        return 0.0
    ia = sa / 60.0
    ib = sb / 60.0
    fa = math.floor(ia)
    fb = math.floor(ib)
    if fa == fb:
        return both[fa] * (ib - ia) if 0 <= fa < cells else 0.0
    total = 0.0
    if 0 <= fa < cells:
        total += both[fa] * (fa + 1 - ia)
    lo = max(fa + 1, 0)
    hi = min(fb, cells)
    if hi > lo:
        total += pref[hi] - pref[lo]
    if 0 <= fb < cells:
        total += both[fb] * (ib - fb)
    return total


def trace_segments(home, away, a, b, cap=500):
    """逐段列出 [a,b) 内双方同时营业的交集时段，附两地各自本地墙钟表示。"""
    grid = build_grid(home, away, a, b)
    home_zone = ZoneInfo(home['timezone'])
    away_zone = ZoneInfo(away['timezone'])
    segs = []
    i = 0
    both = grid.both
    while i < grid.cells:
        if both[i]:
            j = i
            while j < grid.cells and both[j]:
                j += 1
            lo = max(grid.start + i * _MIN, a)
            hi = min(grid.start + j * _MIN, b)
            if hi > lo:
                segs.append({
                    'utc_start': lo.isoformat(),
                    'utc_end': hi.isoformat(),
                    'minutes': (hi - lo).total_seconds() / 60,
                    'home_start': lo.astimezone(home_zone).isoformat(),
                    'home_end': hi.astimezone(home_zone).isoformat(),
                    'away_start': lo.astimezone(away_zone).isoformat(),
                    'away_end': hi.astimezone(away_zone).isoformat(),
                })
            i = j
        else:
            i += 1
    truncated = len(segs) > cap
    return grid, segs[:cap], truncated


def _canon(cal):
    return {
        'timezone': cal['timezone'],
        'weekdays': sorted(int(x) for x in cal['weekdays']),
        'windows': sorted([int(s), int(e)] for s, e in cal['windows']),
        'holidays': sorted(cal.get('holidays', [])),
    }


def rules_hash(home, away):
    """两地日历规则的内容散列：规则更新即失效，旧散列的预计算不再被引用。"""
    blob = json.dumps({'home': _canon(home), 'away': _canon(away)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()
