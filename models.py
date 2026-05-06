import uuid
import datetime as dt
from sqlalchemy import create_engine, Column, String, Boolean, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = "sqlite:///./scheduler.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    cron_expr = Column(String, nullable=True)          # e.g. "0 9 * * MON"
    interval_seconds = Column(Integer, nullable=True)   # alternative to cron
    webhook_url = Column(String, nullable=False)
    webhook_headers = Column(Text, default="{}")        # JSON string
    webhook_body = Column(Text, default="{}")           # JSON string
    timezone = Column(String, default="UTC")
    is_active = Column(Boolean, default=True)
    next_run_at = Column(DateTime, nullable=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    created_by = Column(String, default="human")        # "human" or agent identifier

    executions = relationship("Execution", back_populates="schedule", order_by="Execution.triggered_at.desc()")


class Execution(Base):
    __tablename__ = "executions"

    id = Column(String, primary_key=True, default=new_id)
    schedule_id = Column(String, ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)
    triggered_at = Column(DateTime, default=dt.datetime.utcnow)
    status = Column(String, default="pending")          # pending, success, failed, timeout
    response_code = Column(Integer, nullable=True)
    response_body = Column(Text, default="")            # truncated to 2KB
    duration_ms = Column(Integer, default=0)

    schedule = relationship("Schedule", back_populates="executions")


Base.metadata.create_all(bind=engine)
