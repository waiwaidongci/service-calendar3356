from fastapi import APIRouter,Depends
from src.config.database import database
from src.controllers.auth import current,admin
from src.models.pair import Pair
from src.schemas.requests import DataInput
from src.services.source import save_pair
from src.services.precompute import precompute_status
from src.utils.serialize import row
router=APIRouter()
@router.get('/pairs')
def pairs(db=Depends(database),u=Depends(current)):
 out=[]
 for p in db.query(Pair).order_by(Pair.department,Pair.version.desc()).all():
  d=row(p);d['precompute']=precompute_status(p.department,p.data['home'],p.data['away']);out.append(d)
 return out
@router.post('/pairs')
def create(b:DataInput,db=Depends(database),u=Depends(admin)): return row(save_pair(db,u,b.data))
