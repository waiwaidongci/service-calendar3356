from sqlalchemy import Column, Integer, String, JSON, Float, UniqueConstraint
from src.config.database import Base


class Pair(Base):
    """两地日历组合：按小组绑定本地与协作地营业日历，规则更新即新版本。"""

    __tablename__ = 'calendar_pairs'
    id = Column(Integer, primary_key=True)
    name = Column(String(120))
    department = Column(String(120), index=True)
    version = Column(Integer)
    epoch = Column(Integer)
    data = Column(JSON, nullable=False)
    created = Column(Float)
    __table_args__ = (UniqueConstraint('department', 'version'),)
