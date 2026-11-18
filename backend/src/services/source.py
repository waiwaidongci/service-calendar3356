from fastapi import HTTPException
from src.models.source import Source,Policy,Period
from src.models.pair import Pair
from src.services.validation import validate_source,validate_policy,validate_pair
from src.services.audit import log
import time,uuid
def save_source(db,user,data,id=None):
 validate_source(data)
 obj=db.get(Source,id) if id else Source(natural_key='user-'+uuid.uuid4().hex)
 if not obj: raise HTTPException(404,'底稿不存在')
 obj.data=data;db.add(obj)
 if not db.query(Period).filter_by(name=data['period']).first():db.add(Period(name=data['period'],description='业务来源自动登记期间'))
 log(db,user.email,'source.update' if id else 'source.create',obj.natural_key);db.commit();return obj
def save_policy(db,user,data):
 validate_policy(data)
 if db.query(Policy).filter_by(version=data['version']).first(): raise HTTPException(409,'版本名已存在，请另建版本保留历史')
 obj=Policy(version=data['version'],data=data);db.add(obj);log(db,user.email,'policy.version',data['version']);db.commit();return obj
def save_pair(db,user,data):
 validate_pair(data)
 dept=data['department']
 latest=db.query(Pair).filter_by(department=dept).order_by(Pair.version.desc()).first()
 version=latest.version+1 if latest else 1
 epoch=latest.epoch+1 if latest else 1
 obj=Pair(name=data['name'],department=dept,version=version,epoch=epoch,data={'home':data['home'],'away':data['away'],'sla_minutes':data['sla_minutes']},created=time.time())
 db.add(obj);log(db,user.email,'pair.register',dept+' v'+str(version));db.commit()
 try:
  from src.services.precompute import enqueue_for_pair
  enqueue_for_pair(dept,version,data['home'],data['away'])
 except Exception:
  pass
 return obj
