"""两地日历核算自检：夏令时、跨午夜、边界、预计算纪元与十万条性能。

stdlib unittest，容器内外均可运行：python -m unittest tests.test_dual -v
"""
import json
import random
import sys
import time as time_mod
import types
import unittest
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if not Path('/app/project.json').exists():  # 容器外运行：为 src.config.settings 注入等价桩
    cfg = types.ModuleType('src.config.settings')
    cfg.CONFIG = json.loads((BACKEND / 'project.json').read_text())
    cfg.DATABASE_URL = 'sqlite://'
    cfg.REDIS_URL = 'redis://localhost:6379/0'
    cfg.SECRET = 'test'
    sys.modules['src.config.settings'] = cfg

from src.services.timegrid import build_grid, span_minutes, trace_segments, rules_hash, minute_floor
from src.services.dualcalc import calculate_dual
from src.services import precompute

UTC = timezone.utc


def always_open(tz):
    return {'timezone': tz, 'weekdays': [0, 1, 2, 3, 4, 5, 6], 'windows': [[0, 1440]], 'holidays': []}


def ref_is_open(cal, instant):
    """参照实现：逐个 UTC 分钟判断营业，刻意简单直白。"""
    zone = ZoneInfo(cal['timezone'])
    wall = instant.astimezone(zone).replace(tzinfo=None, second=0, microsecond=0)
    holidays = set(cal.get('holidays', []))
    for delta in (0, -1):  # 本日班次与上一日跨午夜班次
        d = wall.date() + timedelta(days=delta)
        if d.weekday() in cal['weekdays'] and d.isoformat() not in holidays:
            mid = datetime.combine(d, time())
            for s, e in cal['windows']:
                if mid + timedelta(minutes=s) <= wall < mid + timedelta(minutes=e):
                    return True
    return False


def ref_minutes(home, away, a, b):
    total = 0.0
    t = minute_floor(a)
    while t < b:
        if ref_is_open(home, t) and ref_is_open(away, t):
            total += (min(t + timedelta(minutes=1), b) - max(t, a)).total_seconds() / 60
        t += timedelta(minutes=1)
    return total


class DstTest(unittest.TestCase):
    def test_fallback_repeated_hours_counted(self):
        # 2026-11-01 纽约回拨：墙钟 01:00-01:59 出现两次，两段实际时间都要计入
        away = {'timezone': 'America/New_York', 'weekdays': [0, 1, 2, 3, 4, 5, 6], 'windows': [[60, 180]], 'holidays': []}
        a = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
        b = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
        grid = build_grid(always_open('UTC'), away, a, b)
        self.assertEqual(span_minutes(grid, a, b), 180.0)  # 墙钟2小时，实际3小时

    def test_spring_forward_gap_not_counted(self):
        # 2026-03-08 纽约跳时：02:00-02:59 不存在，不计；01:00与03:00两段照常
        away = {'timezone': 'America/New_York', 'weekdays': [0, 1, 2, 3, 4, 5, 6], 'windows': [[60, 240]], 'holidays': []}
        a = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
        b = datetime(2026, 3, 8, 10, 0, tzinfo=UTC)
        grid = build_grid(always_open('UTC'), away, a, b)
        self.assertEqual(span_minutes(grid, a, b), 120.0)  # 墙钟3小时，实际2小时

    def test_lord_howe_half_hour_gap(self):
        # 2026-10-04 Lord Howe 02:00 拨到 02:30，半小时不存在
        away = {'timezone': 'Australia/Lord_Howe', 'weekdays': [0, 1, 2, 3, 4, 5, 6], 'windows': [[0, 240]], 'holidays': []}
        a = datetime(2026, 10, 3, 13, 0, tzinfo=UTC)  # 当地 2026-10-03 23:30
        b = datetime(2026, 10, 3, 18, 30, tzinfo=UTC)
        grid = build_grid(always_open('UTC'), away, a, b)
        self.assertEqual(span_minutes(grid, a, b), 210.0)  # 240墙钟分钟 - 30不存在

    def test_apia_skipped_day(self):
        # 2011-12-30 阿皮亚整日不存在：不存在的墙钟不计，前后日照常
        away = {'timezone': 'Pacific/Apia', 'weekdays': [0, 1, 2, 3, 4, 5, 6], 'windows': [[0, 1440]], 'holidays': []}
        a = datetime(2011, 12, 29, 10, 0, tzinfo=UTC)
        b = datetime(2011, 12, 31, 10, 0, tzinfo=UTC)
        grid = build_grid(always_open('UTC'), away, a, b)
        self.assertEqual(span_minutes(grid, a, b), 2880.0)


class ShiftTest(unittest.TestCase):
    def test_cross_midnight_shift_belongs_to_start_day(self):
        sh = ZoneInfo('Asia/Shanghai')
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [4], 'windows': [[1320, 1560]], 'holidays': []}  # 仅周五 22:00→次日02:00
        away = always_open('UTC')
        # 2026-11-06 是周五；周六 00:30-01:30 属周五开班，计 60 分钟
        a = datetime(2026, 11, 7, 0, 30, tzinfo=sh).astimezone(UTC)
        b = datetime(2026, 11, 7, 1, 30, tzinfo=sh).astimezone(UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 60.0)
        # 周六 22:30-23:30：周六非开班日，不计
        a = datetime(2026, 11, 7, 22, 30, tzinfo=sh).astimezone(UTC)
        b = datetime(2026, 11, 7, 23, 30, tzinfo=sh).astimezone(UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 0.0)

    def test_intersection_only_when_both_open(self):
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [0], 'windows': [[540, 720]], 'holidays': []}  # 周一 09:00-12:00
        away = {'timezone': 'UTC', 'weekdays': [0], 'windows': [[120, 180]], 'holidays': []}  # 周一 02:00-03:00 UTC
        a = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)
        b = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 60.0)  # 仅上海 10:00-11:00 重叠

    def test_holiday_and_weekday_excluded(self):
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [0], 'windows': [[540, 720]], 'holidays': ['2026-11-09']}
        away = always_open('UTC')
        a = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # 周一但显式假日
        b = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 0.0)
        a = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)  # 周二非开班日
        b = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 0.0)

    def test_partial_minute_boundary(self):
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [0], 'windows': [[540, 720]], 'holidays': []}
        away = always_open('UTC')
        a = datetime(2026, 11, 2, 10, 30, 30, tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(UTC)
        b = datetime(2026, 11, 2, 11, 0, 0, tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(UTC)
        self.assertEqual(span_minutes(build_grid(home, away, a, b), a, b), 29.5)  # 边界不足一分钟按秒折算


class DualCalcTest(unittest.TestCase):
    def _pairs(self, sla=180):
        return [{'department': 'g1', 'name': '测', 'version': 1, 'data': {
            'home': {'timezone': 'Asia/Shanghai', 'weekdays': [0], 'windows': [[540, 720]], 'holidays': []},
            'away': always_open('UTC'),
            'sla_minutes': {'P1': sla}}}]

    def _src(self, **kw):
        base = {'period': '2026-11', 'department': 'g1', 'opened': '2026-11-02T09:00:00+08:00',
                'closed': '2026-11-02T12:00:00+08:00', 'priority': 'P1'}
        base.update(kw)
        return base

    def test_sla_boundary_not_overdue(self):
        res = calculate_dual([self._src()], self._pairs(180))
        self.assertEqual(res['summary']['交集分钟合计'], 180.0)
        self.assertEqual(res['summary']['超时数'], 0)  # 恰等于阈值不算超时
        res = calculate_dual([self._src()], self._pairs(179))
        self.assertEqual(res['summary']['超时数'], 1)

    def test_unbound_department_rejected(self):
        with self.assertRaises(ValueError):
            calculate_dual([self._src(department='gX')], self._pairs())

    def test_unknown_priority_rejected(self):
        with self.assertRaises(ValueError):
            calculate_dual([self._src(priority='PX')], self._pairs())

    def test_negative_span_rejected(self):
        with self.assertRaises(ValueError):
            calculate_dual([self._src(closed='2026-11-02T08:00:00+08:00')], self._pairs())

    def test_trace_segments_sum_matches(self):
        pairs = self._pairs()
        s = self._src(closed='2026-11-03T12:00:00+08:00')
        a = datetime.fromisoformat(s['opened']).astimezone(UTC)
        b = datetime.fromisoformat(s['closed']).astimezone(UTC)
        grid, segs, truncated = trace_segments(pairs[0]['data']['home'], pairs[0]['data']['away'], a, b)
        self.assertFalse(truncated)
        self.assertAlmostEqual(sum(x['minutes'] for x in segs), span_minutes(grid, a, b), places=9)
        self.assertEqual(segs[0]['home_start'][:19], '2026-11-02T09:00:00')


class FuzzTest(unittest.TestCase):
    def test_against_reference(self):
        rng = random.Random(2026)
        zones = ['Asia/Shanghai', 'America/New_York', 'Australia/Sydney', 'Europe/London',
                 'Australia/Lord_Howe', 'Asia/Kathmandu', 'America/St_Johns', 'UTC']

        def random_cal():
            wins = []
            start = 0
            for _ in range(rng.randrange(1, 4)):
                s = rng.randrange(start, 1380)
                e = min(s + rng.randrange(30, 1500), 2880)
                wins.append([s, e])
                if e >= 1440:
                    break
                start = e + rng.randrange(0, 240)
                if start >= 1380:
                    break
            return {'timezone': rng.choice(zones),
                    'weekdays': sorted(rng.sample(range(7), rng.randrange(1, 8))),
                    'windows': wins,
                    'holidays': [f'2026-0{rng.randrange(1, 10)}-{rng.randrange(10, 28):02}' for _ in range(rng.randrange(0, 4))]}

        for trial in range(40):
            home, away = random_cal(), random_cal()
            a = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=rng.randrange(0, 360 * 86400))
            b = a + timedelta(seconds=rng.randrange(60, 5 * 86400))
            got = span_minutes(build_grid(home, away, a, b), a, b)
            want = ref_minutes(home, away, a, b)
            self.assertAlmostEqual(got, want, places=6,
                                   msg=f'trial={trial} home={home} away={away} [{a},{b}]')

    def test_long_span_self_consistency(self):
        # 366天请求：整段结果等于逐日切片之和（不逐日展开的网格与切片口径一致）
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [780, 1080], [1260, 1560]], 'holidays': ['2026-11-16']}
        away = {'timezone': 'America/New_York', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[480, 960]], 'holidays': ['2026-11-26']}
        a = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        b = datetime(2027, 1, 2, 0, 0, tzinfo=UTC)  # 366 天
        grid = build_grid(home, away, a, b)
        total = span_minutes(grid, a, b)
        sliced = 0.0
        t = a
        while t < b:
            sliced += span_minutes(grid, t, min(t + timedelta(days=1), b))
            t += timedelta(days=1)
        self.assertAlmostEqual(total, sliced, places=6)
        # 抽 12 个 5 天窗口与参照实现比对
        rng = random.Random(99)
        for _ in range(12):
            x = a + timedelta(seconds=rng.randrange(0, 360 * 86400))
            y = x + timedelta(seconds=rng.randrange(3600, 5 * 86400))
            self.assertAlmostEqual(span_minutes(grid, x, y), ref_minutes(home, away, x, y), places=6)


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.lists = {}

    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, '0')) + 1)
        return int(self.kv[k])

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        self.kv[k] = v
        return True

    def exists(self, k):
        return k in self.kv

    def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)

    def lpop(self, k):
        return self.lists[k].pop(0) if self.lists.get(k) else None

    def eval(self, script, n, ekey, pkey, epoch, payload, ttl):
        if self.kv.get(ekey) == epoch:  # 与 _STORE_LUA 同语义：纪元一致才写入
            self.kv[pkey] = payload
            return True
        return False


class PrecomputeTest(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        home = {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [1260, 1560]], 'holidays': []}
        away = {'timezone': 'America/New_York', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[480, 960]], 'holidays': []}
        a = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        b = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
        grid = build_grid(home, away, a, b)
        g2 = precompute.unpack(precompute.pack(grid))
        self.assertEqual(g2.start, grid.start)
        self.assertEqual(g2.cells, grid.cells)
        self.assertEqual(bytes(g2.both), bytes(grid.both))
        self.assertEqual(list(g2.pref), list(grid.pref))
        x = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)
        y = datetime(2026, 11, 6, 2, 0, tzinfo=UTC)
        self.assertEqual(span_minutes(g2, x, y), span_minutes(grid, x, y))

    def test_late_rebuild_does_not_overwrite(self):
        r = FakeRedis()
        home = always_open('Asia/Shanghai')
        away_v1 = always_open('UTC')
        away_v2 = {'timezone': 'UTC', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720]], 'holidays': []}
        h1, h2 = rules_hash(home, away_v1), rules_hash(home, away_v2)
        self.assertNotEqual(h1, h2)
        e1 = precompute.invalidate_and_enqueue(r, 'g', 1, h1)
        e2 = precompute.invalidate_and_enqueue(r, 'g', 2, h2)
        self.assertLess(e1, e2)
        # 旧规则重建迟到：纪元不一致，拒绝写入
        self.assertFalse(precompute.store_if_current(r, 'g', h1, e1, b'old'))
        self.assertIsNone(r.get(precompute.pre_key('g', h1)))
        # 新规则重建落库
        self.assertTrue(precompute.store_if_current(r, 'g', h2, e2, b'new'))
        self.assertEqual(r.get(precompute.pre_key('g', h2)), b'new')

    def test_cache_hit_matches_fresh_build(self):
        r = FakeRedis()
        pairs = [{'department': 'g1', 'name': '测', 'version': 1, 'data': {
            'home': {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [780, 1080]], 'holidays': []},
            'away': {'timezone': 'Australia/Sydney', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 1080]], 'holidays': []},
            'sla_minutes': {'P1': 120}}}]
        sources = [{'period': '2026-11', 'department': 'g1',
                    'opened': f'2026-11-{2 + i % 18:02}T10:30:00+08:00',
                    'closed': f'2026-11-{2 + i % 18:02}T16:30:00+08:00', 'priority': 'P1'} for i in range(50)]
        payload = precompute.build_pair_payload(pairs[0]['data'], sources)
        h = rules_hash(pairs[0]['data']['home'], pairs[0]['data']['away'])
        r.set(precompute.pre_key('g1', h), payload)
        fresh = calculate_dual(sources, pairs)
        cached = calculate_dual(sources, pairs, cache=precompute.GridCache(r))
        self.assertEqual(fresh['summary'], cached['summary'])
        self.assertEqual(fresh['rows'], cached['rows'])


class PerfTest(unittest.TestCase):
    def test_100k_requests_within_30s(self):
        rng = random.Random(7)
        pairs = [
            {'department': 'A', 'name': '沪悉', 'version': 1, 'data': {
                'home': {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [780, 1080]], 'holidays': []},
                'away': {'timezone': 'Australia/Sydney', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 1080]], 'holidays': []},
                'sla_minutes': {'P1': 480, 'P2': 120}}},
            {'department': 'B', 'name': '沪约', 'version': 1, 'data': {
                'home': {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [1260, 1560]], 'holidays': []},
                'away': {'timezone': 'America/New_York', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[480, 960]], 'holidays': []},
                'sla_minutes': {'P1': 480, 'P2': 120}}},
            {'department': 'C', 'name': '沪京', 'version': 1, 'data': {
                'home': {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 720], [780, 1080]], 'holidays': []},
                'away': {'timezone': 'Asia/Tokyo', 'weekdays': [0, 1, 2, 3, 4], 'windows': [[540, 1080]], 'holidays': []},
                'sla_minutes': {'P1': 480, 'P2': 120}}},
        ]
        base = datetime(2026, 1, 1, tzinfo=UTC)
        sources = []
        for i in range(100000):
            start = base + timedelta(seconds=rng.randrange(0, 365 * 86400))
            span = rng.randrange(3600, 366 * 86400) if i % 10 == 0 else rng.randrange(3600, 3 * 86400)
            end = min(start + timedelta(seconds=span), base + timedelta(seconds=366 * 86400))
            sources.append({'period': '2026-11', 'department': 'ABC'[i % 3],
                            'opened': start.isoformat(), 'closed': end.isoformat(),
                            'priority': 'P1' if i % 2 else 'P2'})
        t0 = time_mod.monotonic()
        res = calculate_dual(sources, pairs)
        elapsed = time_mod.monotonic() - t0
        self.assertEqual(res['summary']['样本数'], 100000)
        self.assertLess(elapsed, 30, f'十万条核算耗时 {elapsed:.1f}s，超过30秒预算')
        print(f'\n十万条（最长366天）两地核算耗时 {elapsed:.2f}s')


if __name__ == '__main__':
    unittest.main()
