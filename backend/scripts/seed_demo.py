import json,random,time
from copy import deepcopy
from src.config.database import Base,engine,Session
from src.config.settings import CONFIG
from src.models.identity import User
from src.models.source import Source,Policy,Period
from src.models.pair import Pair
from src.models import run
from src.utils.security import password_hash
from src.services.validation import validate_source,validate_pair

def demo_rows():
 rng=random.Random(42);rows=[]
 for i in range(240):
  day=1+i%20;rows.append({'period':'2026-11','department':['数据建模组','数据采样组','数据核验组'][i%3],'opened':f'2026-11-{day:02}T10:30:00+08:00','closed':f'2026-11-{day:02}T16:30:00+08:00','priority':['普通','紧急'][i%2]})
 for i in range(24):
  day=1+i%20;rows.append({'period':'2026-11','department':'数据核验组','opened':f'2026-11-{day:02}T19:30:00+08:00','closed':f'2026-11-{day:02}T23:30:00+08:00','priority':['普通','紧急'][i%2]})
 return rows

SH={'timezone':'Asia/Shanghai','weekdays':[0,1,2,3,4],'windows':[[540,720],[780,1080]],'holidays':['2026-11-16']}
def demo_pairs():
 return [
  {'name':'沪悉联合服务日历','department':'数据建模组','home':deepcopy(SH),'away':{'timezone':'Australia/Sydney','weekdays':[0,1,2,3,4],'windows':[[540,1080]],'holidays':[]},'sla_minutes':{'普通':480,'紧急':120}},
  {'name':'沪京联合服务日历','department':'数据采样组','home':deepcopy(SH),'away':{'timezone':'Asia/Tokyo','weekdays':[0,1,2,3,4],'windows':[[540,1080]],'holidays':[]},'sla_minutes':{'普通':480,'紧急':120}},
  {'name':'沪约夜班联合日历','department':'数据核验组','home':{'timezone':'Asia/Shanghai','weekdays':[0,1,2,3,4],'windows':[[540,720],[780,1080],[1260,1560]],'holidays':['2026-11-16']},'away':{'timezone':'America/New_York','weekdays':[0,1,2,3,4],'windows':[[480,960]],'holidays':[]},'sla_minutes':{'普通':480,'紧急':120}},
 ]

def seed():
 Base.metadata.create_all(engine)
 with Session() as db:
  for role in ('admin','analyst','viewer'):
   if not db.query(User).filter_by(email=role+'@demo.local').first():db.add(User(email=role+'@demo.local',role=role,password=password_hash('Demo2026!')))
  rows=demo_rows()
  for name in sorted(set(r['period'] for r in rows)):
   if not db.query(Period).filter_by(name=name).first():db.add(Period(name=name,description='合成演示业务期间，seed=42'))
  for i,data in enumerate(rows):
   validate_source(data);key='demo-'+str(i+1)
   if not db.query(Source).filter_by(natural_key=key).first():db.add(Source(natural_key=key,data=data))
  if not db.query(Policy).filter_by(version=CONFIG['policy']['version']).first():db.add(Policy(version=CONFIG['policy']['version'],data=CONFIG['policy']))
  queued=[]
  for data in demo_pairs():
   validate_pair(data)
   if not db.query(Pair).filter_by(department=data['department']).first():
    db.add(Pair(name=data['name'],department=data['department'],version=1,epoch=1,data={'home':data['home'],'away':data['away'],'sla_minutes':data['sla_minutes']},created=time.time()));queued.append(data)
  db.commit()
  for data in queued:
   try:
    from src.services.precompute import enqueue_for_pair
    enqueue_for_pair(data['department'],1,data['home'],data['away'])
   except Exception: pass
  print(json.dumps({'users':db.query(User).count(),'sources':db.query(Source).count(),'policies':db.query(Policy).count(),'periods':db.query(Period).count(),'pairs':db.query(Pair).count()},ensure_ascii=False))
if __name__=='__main__':seed()
