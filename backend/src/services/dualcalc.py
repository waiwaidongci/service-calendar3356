"""两地日历组合核算：只计双方同时营业的真实分钟。

按小组绑定两地营业日历；每个小组构建一次分钟网格（可命中预计算缓存），
之后每条请求只做 O(1) 区间查询，不逐请求逐日展开。
"""
from collections import defaultdict
from datetime import timezone, datetime

from src.services.timegrid import build_grid, span_minutes, rules_hash, minute_floor, _MIN
from src.services.validation import validate_source


def calculate_dual(sources, pairs, cache=None):
    """sources 为冻结底稿列表；pairs 为排队时冻结的两地规则列表。

    cache 可选，提供 get(department, rules_hash) 返回预计算 Grid 或 None。
    """
    by_dept = {p['department']: p for p in pairs}
    parsed = []
    depts = set()
    for s in sources:
        validate_source(s)
        a = datetime.fromisoformat(s['opened']).astimezone(timezone.utc)
        b = datetime.fromisoformat(s['closed']).astimezone(timezone.utc)
        parsed.append((s, a, b))
        depts.add(s['department'])
    missing = sorted(depts - set(by_dept))
    if missing:
        raise ValueError('小组未绑定两地日历：' + '、'.join(missing))
    pivot = defaultdict(lambda: {'count': 0, 'minutes': 0.0, 'overdue': 0})
    used = {}
    for dept in sorted(depts):
        pair = by_dept[dept]
        data = pair['data']
        home, away, sla = data['home'], data['away'], data['sla_minutes']
        group = [(s, a, b) for s, a, b in parsed if s['department'] == dept]
        lo = minute_floor(min(a for _, a, _ in group))
        hi = max(b for _, _, b in group)
        h = rules_hash(home, away)
        grid = cache.get(dept, h) if cache else None
        if grid is None or not grid.covers(lo, hi):
            grid = build_grid(home, away, lo, hi)
        used[dept] = {'department': dept, 'name': pair['name'], 'version': pair['version'],
                      'home_tz': home['timezone'], 'away_tz': away['timezone'], 'rules_hash': h[:12]}
        for s, a, b in group:
            if s['priority'] not in sla:
                raise ValueError('优先级无SLA口径：' + s['priority'])
            mins = span_minutes(grid, a, b)
            k = (s['period'], dept, s['priority'])
            p = pivot[k]
            p['count'] += 1
            p['minutes'] += mins
            p['overdue'] += int(mins > sla[s['priority']])
    rows = []
    for (period, dept, priority), p in sorted(pivot.items()):
        u = used[dept]
        rows.append({'月份': period, '小组': dept, '优先级': priority,
                     '本地时区': u['home_tz'], '协作时区': u['away_tz'],
                     '样本数': p['count'], '交集分钟合计': p['minutes'],
                     '平均交集分钟': p['minutes'] / p['count'],
                     '超时数': p['overdue'], '超时率': p['overdue'] / p['count']})
    return {
        'dual': True,
        'summary': {'样本数': len(sources),
                    '交集分钟合计': sum(p['minutes'] for p in pivot.values()),
                    '超时数': sum(p['overdue'] for p in pivot.values()),
                    '日历组合数': len(used)},
        'rows': rows,
        'method': '两地口径：请求区间分别映射到本地与协作地营业日历的UTC分钟网格（跨午夜班次归开班日；夏令时回拨重复分钟计入、跳时不存在分钟不计），按位与取双方同时营业分钟后精确求和，边界不足一分钟按秒折算；排除双方非工作日与显式假日。恰等于SLA阈值不算超时。预计算分钟网格按规则散列寻址，规则更新即失效重建。',
        'warnings': ['统计合成已结束样本；不包含未关闭请求，也不承诺实时事件提醒。',
                     '逐请求交集时段请在报告详情按序号追查。'],
        'pairs': [used[d] for d in sorted(used)],
    }
