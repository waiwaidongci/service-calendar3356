"""两地日历预计算缓存：规则更新即失效重建，旧重建迟到不覆盖新规则。

缓存按规则内容散列寻址（pair:pre:{小组}:{规则散列}），天然不会串版本；
另以每小组单调递增的纪元（pair:epoch:{小组}）守卫写入：重建任务携带排队时
纪元，落库前与当前纪元比对，不一致说明是旧规则的迟到重建，直接丢弃。
"""
import json
from array import array
from datetime import datetime, timedelta, timezone

from src.config.settings import REDIS_URL
from src.services.timegrid import Grid, build_grid, minute_floor, rules_hash, _UINT

TTL = 86400
_QUEUE = 'pair-jobs'


def epoch_key(dept):
    return 'pair:epoch:' + dept


def pre_key(dept, h):
    return 'pair:pre:' + dept + ':' + h


def pack(grid):
    meta = json.dumps({'start': grid.start.isoformat(), 'cells': grid.cells}).encode()
    return len(meta).to_bytes(4, 'big') + meta + bytes(grid.both) + grid.pref.tobytes()


def unpack(blob):
    n = int.from_bytes(blob[:4], 'big')
    meta = json.loads(blob[4:4 + n])
    cells = meta['cells']
    both = blob[4 + n:4 + n + cells]
    pref = array(_UINT)
    pref.frombytes(blob[4 + n + cells:4 + n + cells + pref.itemsize * (cells + 1)])
    return Grid(datetime.fromisoformat(meta['start']), cells, both, pref)


class GridCache:
    """核算路径只读缓存；命中且覆盖请求区间即免去逐日展开。"""

    def __init__(self, r):
        self.r = r

    def get(self, dept, h):
        try:
            blob = self.r.get(pre_key(dept, h))
        except Exception:
            return None
        return unpack(blob) if blob else None


_STORE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('set', KEYS[2], ARGV[2], 'EX', ARGV[3])
else
  return false
end
"""


def store_if_current(r, dept, h, epoch, payload):
    """仅当纪元未变（期间没有更新的规则登记）才写入，原子比对。"""
    return bool(r.eval(_STORE_LUA, 2, epoch_key(dept), pre_key(dept, h), str(epoch), payload, TTL))


def invalidate_and_enqueue(r, dept, version, h):
    """登记新规则后调用：递增纪元使在途重建失效，并排队新规则的重建。"""
    epoch = r.incr(epoch_key(dept))
    r.rpush(_QUEUE, json.dumps({'department': dept, 'version': version, 'hash': h, 'epoch': epoch}, ensure_ascii=False))
    return epoch


def enqueue_for_pair(dept, version, home, away):
    import redis
    r = redis.Redis.from_url(REDIS_URL)
    return invalidate_and_enqueue(r, dept, version, rules_hash(home, away))


def precompute_status(dept, home, away):
    import redis
    try:
        r = redis.Redis.from_url(REDIS_URL)
        return 'ready' if r.exists(pre_key(dept, rules_hash(home, away))) else 'pending'
    except Exception:
        return 'unknown'


def build_pair_payload(pair_data, sources):
    """按小组现有底稿覆盖区间（前后各留一天余量）构建预计算网格并打包。"""
    lo = minute_floor(min(datetime.fromisoformat(s['opened']).astimezone(timezone.utc) for s in sources)) - timedelta(days=1)
    hi = max(datetime.fromisoformat(s['closed']).astimezone(timezone.utc) for s in sources) + timedelta(days=1)
    grid = build_grid(pair_data['home'], pair_data['away'], lo, hi)
    return pack(grid)


def rebuild_job(db, r, job):
    """worker 执行：重建指定版本的预计算；旧规则迟到重建不写入。"""
    from src.models.pair import Pair
    from src.models.source import Source
    dept = job['department']
    pair = db.query(Pair).filter_by(department=dept, version=job['version']).first()
    if not pair:
        return '组合版本不存在，丢弃：' + dept
    h = rules_hash(pair.data['home'], pair.data['away'])
    if h != job['hash']:
        return '规则散列不一致，丢弃：' + dept
    sources = [x.data for x in db.query(Source).all() if x.data.get('department') == dept]
    if not sources:
        return '小组暂无底稿，跳过预计算：' + dept
    payload = build_pair_payload(pair.data, sources)
    if store_if_current(r, dept, h, job['epoch'], payload):
        return '预计算已重建：%s v%s' % (dept, job['version'])
    return '旧规则重建迟到，已丢弃：%s v%s' % (dept, job['version'])
