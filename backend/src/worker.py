import time,json,traceback
from pathlib import Path
import redis
from src.config.database import Base,engine,Session
from src.config.settings import REDIS_URL
from src.models import identity,source,run,pair
from src.models.run import Run
from src.services.calculation import calculate
from src.services.dualcalc import calculate_dual
from src.services.precompute import GridCache,rebuild_job
from src.services.delivery import tick
from src.services.audit import log
Base.metadata.create_all(engine)
r=redis.Redis.from_url(REDIS_URL)
cache=GridCache(r)
while True:
 Path('/tmp/worker-heartbeat').write_text(str(time.time()))
 try:
  item=r.lpop('report-jobs')
  if item:
   with Session() as db:
    job=db.get(Run,int(item))
    if job and job.status=='queued':
     job.status='running';db.commit()
     try:
      snap=job.snapshot
      if 'pairs' in snap: job.result=calculate_dual(snap['sources'],snap['pairs'],cache=cache)
      else: job.result=calculate(snap['sources'],snap['policy'])
      job.status='completed'
     except Exception as exc: job.status='failed';job.error=str(exc);traceback.print_exc()
     db.commit()
  pitem=r.lpop('pair-jobs')
  if pitem:
   with Session() as db:
    try:
     outcome=rebuild_job(db,r,json.loads(pitem))
     log(db,'worker','pair.precompute',outcome);db.commit()
    except Exception: traceback.print_exc()
  with Session() as db: tick(db)
 except Exception: traceback.print_exc()
 time.sleep(1)
