import hashlib,json,time
from fastapi import HTTPException
from src.models.source import Source,Policy
from src.models.pair import Pair
from src.models.run import Run
from src.services.audit import log
from src.config.settings import REDIS_URL
import redis
def submit(db,user,body):
 sources=[x.data for x in db.query(Source).order_by(Source.id).all() if body.period=='全部' or x.data.get('period')==body.period]
 if not sources: raise HTTPException(422,'所选期间没有底稿')
 if body.mode=='dual':
  latest={}
  for p in db.query(Pair).all():
   if p.department not in latest or p.version>latest[p.department].version: latest[p.department]=p
  depts={s['department'] for s in sources}
  missing=sorted(depts-set(latest))
  if missing: raise HTTPException(422,'小组未绑定两地日历：'+'、'.join(missing))
  frozen=[{'department':d,'name':p.name,'version':p.version,'epoch':p.epoch,'data':p.data} for d,p in sorted(latest.items()) if d in depts]
  snapshot={'mode':'dual','pairs':frozen,'sources':sources,'period':body.period}
 else:
  policy=db.get(Policy,body.policy_id)
  if not policy: raise HTTPException(404,'口径版本不存在')
  snapshot={'policy':policy.data,'sources':sources,'period':body.period}
 checksum=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
 obj=Run(name=body.name,status='queued',snapshot=snapshot,checksum=checksum,created=time.time(),approved=False)
 db.add(obj);log(db,user.email,'run.queue',body.name);db.commit()
 redis.Redis.from_url(REDIS_URL).rpush('report-jobs',obj.id)
 return obj
def approve(db,user,id,note):
 obj=db.get(Run,id)
 if not obj: raise HTTPException(404,'报告不存在')
 if obj.status!='completed': raise HTTPException(409,'仅可复核已完成报告')
 if obj.approved: raise HTTPException(409,'报告已经复核')
 obj.approved=True;obj.review=note;log(db,user.email,'run.approve',id);db.commit();return obj
