from sqlalchemy import Column, Integer, String, BigInteger, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from src.db.database import Base


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    message_id = Column(BigInteger, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uix_chat_message"),
    )


class UserChatHit(Base):
    __tablename__ = "user_chat_hits"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    user_id = Column(BigInteger, index=True)
    last_hit_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uix_chat_user"),
    )


class Blacklist(Base):
    __tablename__ = "blacklist"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LeadEvent(Base):
    __tablename__ = "lead_events"

    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    chat_title = Column(String(300), nullable=True)
    chat_username = Column(String(200), nullable=True)
    author_id = Column(BigInteger, index=True, nullable=True)
    author_username = Column(String(200), nullable=True)
    group_name = Column(String(200), index=True, nullable=True)
    keyword = Column(String(255), index=True, nullable=True)
    message_text = Column(String, nullable=False, default="")
    parser_account_id = Column(Integer, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
