from datetime import datetime
from typing import Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class SystemSetting(Base):
    """
    Глобальные настройки бота (режим доступа и системные флаги).
    """
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    def __repr__(self):
        return f"<SystemSetting {self.key}={self.value}>"

class Whitelist(Base):
    """
    Белый список пользователей (по telegram_id или username).
    """
    __tablename__ = "whitelist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Whitelist id={self.id} tg_id={self.telegram_id} username={self.username}>"

class User(Base):
    """
    Пользователи бота.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram User ID
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    session: Mapped[Optional["UserSession"]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    broadcast_settings: Mapped[Optional["BroadcastSettings"]] = relationship(
        "BroadcastSettings", back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    messages: Mapped[list["BroadcastMessage"]] = relationship(
        "BroadcastMessage", back_populates="user", cascade="all, delete-orphan"
    )
    channels: Mapped[list["TargetChannel"]] = relationship(
        "TargetChannel", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User id={self.id} username={self.username}>"

class UserSession(Base):
    """
    MTProto Pyrogram StringSession авторизованного пользователя.
    """
    __tablename__ = "user_sessions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    session_string: Mapped[str] = mapped_column(Text, nullable=False)
    two_factor_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship("User", back_populates="session")

    def __repr__(self):
        return f"<UserSession user_id={self.user_id} phone={self.phone_number} active={self.is_active}>"

class BroadcastSettings(Base):
    """
    Индивидуальные настройки рассылки пользователя.
    """
    __tablename__ = "broadcast_settings"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    interval_minutes: Mapped[float] = mapped_column(Float, default=30.0)
    random_jitter_min: Mapped[int] = mapped_column(Integer, default=3)
    random_jitter_max: Mapped[int] = mapped_column(Integer, default=10)
    rotation_mode: Mapped[str] = mapped_column(String(32), default="random")  # 'random' or 'round_robin'
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    total_sent: Mapped[int] = mapped_column(Integer, default=0)
    total_errors: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="broadcast_settings")

    def __repr__(self):
        return f"<BroadcastSettings user_id={self.user_id} is_running={self.is_running} interval={self.interval_minutes}m>"

class BroadcastMessage(Base):
    """
    Пул сообщений для ротации (от 2 до 4 текстов на пользователя).
    """
    __tablename__ = "broadcast_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    slot_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 2, 3, 4
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship("User", back_populates="messages")

    __table_args__ = (
        UniqueConstraint("user_id", "slot_number", name="uq_user_slot"),
    )

    def __repr__(self):
        return f"<BroadcastMessage user_id={self.user_id} slot={self.slot_number}>"

class TargetChannel(Base):
    """
    Целевые каналы и группы, доступные пользователю.
    """
    __tablename__ = "target_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'channel', 'supergroup', 'group'
    is_selected: Mapped[bool] = mapped_column(Boolean, default=True)
    can_send_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 'ok', 'error', 'flood_wait'
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped["User"] = relationship("User", back_populates="channels")

    __table_args__ = (
        UniqueConstraint("user_id", "chat_id", name="uq_user_channel"),
    )

    def __repr__(self):
        return f"<TargetChannel user_id={self.user_id} chat_id={self.chat_id} title={self.title} selected={self.is_selected}>"
