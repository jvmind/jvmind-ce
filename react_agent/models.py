"""SQLAlchemy ORM 模型 — JVMind Community Edition

精简版：去除计费、套餐、组织、邮件验证、API Token、博客、token 用量计费相关表。
保留：用户、会话、消息、记忆、GC/jstack/heapdump 报告、上传文件、技能、反馈、审计、系统设置。
"""
from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import relationship

from .db import Base
from .timeutil import now_str as _now_str


class UserModel(Base):
    __tablename__ = "users"

    id = Column(Text, primary_key=True)
    username = Column(Text, unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    is_admin = Column(Integer, default=0)
    email = Column(Text, default="")
    config = Column(Text, default="{}")
    created_at = Column(Text, default=_now_str)

    sessions = relationship("SessionModel", back_populates="user", cascade="all, delete-orphan")
    skills = relationship("SkillModel", back_populates="user", cascade="all, delete-orphan")
    uploaded_files = relationship("UploadedFileModel", back_populates="user", cascade="all, delete-orphan")


class SessionModel(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_updated_at", "updated_at"),
    )

    id = Column(Text, primary_key=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    title = Column(Text, default="")
    created_at = Column(Text, default=_now_str)
    updated_at = Column(Text, default=_now_str)

    user = relationship("UserModel", back_populates="sessions")
    messages = relationship("MessageModel", back_populates="session", cascade="all, delete-orphan")
    facts = relationship("FactModel", back_populates="session", cascade="all, delete-orphan")
    gc_reports = relationship("GCReportModel", back_populates="session", cascade="all, delete-orphan")
    jstack_reports = relationship("JStackReportModel", back_populates="session", cascade="all, delete-orphan")
    heapdump_reports = relationship("HeapdumpReportModel", back_populates="session", cascade="all, delete-orphan")


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_id", "session_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Text, ForeignKey("sessions.id"), nullable=False)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    ts = Column(Text, default=_now_str)
    session = relationship("SessionModel", back_populates="messages")


class FactModel(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_session_id", "session_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Text, ForeignKey("sessions.id"), nullable=False)
    content = Column(Text, nullable=False)
    last_accessed_at = Column(Text, nullable=True)
    access_count = Column(Integer, server_default=text("0"), nullable=False)
    category = Column(Text, server_default="user_remembered", nullable=False)
    created_at = Column(Text, default=_now_str)
    session = relationship("SessionModel", back_populates="facts")


class GCReportModel(Base):
    __tablename__ = "gc_reports"
    __table_args__ = (
        Index("ix_gc_reports_session_id", "session_id"),
    )

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("sessions.id"), nullable=False)
    filename = Column(Text, nullable=False)
    size = Column(Integer, default=0)
    file_id = Column(Text, nullable=True)
    stats = Column(Text, default="{}")
    ai_conclusion = Column(Text, default="")
    created_at = Column(Text, default=_now_str)
    session = relationship("SessionModel", back_populates="gc_reports")


class JStackReportModel(Base):
    __tablename__ = "jstack_reports"
    __table_args__ = (
        Index("ix_jstack_reports_session_id", "session_id"),
    )

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("sessions.id"), nullable=False)
    filename = Column(Text, nullable=False)
    size = Column(Integer, default=0)
    file_id = Column(Text, nullable=True)
    stats = Column(Text, default="{}")
    ai_conclusion = Column(Text, default="")
    created_at = Column(Text, default=_now_str)
    session = relationship("SessionModel", back_populates="jstack_reports")


class HeapdumpReportModel(Base):
    """Heapdump 分析报告（GB 级 hprof 异步解析 + 多维交互查询）。"""

    __tablename__ = "heapdump_reports"
    __table_args__ = (
        Index("ix_heapdump_reports_session_id", "session_id"),
        Index("ix_heapdump_reports_status", "status"),
        Index("ix_heapdump_reports_user_id", "user_id"),
    )

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    filename = Column(Text, nullable=False)
    size = Column(Integer, default=0)
    status = Column(Text, default="QUEUED")
    progress = Column(Integer, default=0)
    phase = Column(Text, default="")
    dump_dir = Column(Text, default="")
    parse_args = Column(Text, default="{}")
    stats = Column(Text, default="{}")
    ai_conclusion = Column(Text, default="")
    error = Column(Text, default="")
    worker_id = Column(Text, default="")
    heartbeat = Column(Text, default="")
    attempts = Column(Integer, default=0)
    created_at = Column(Text, default=_now_str)
    queued_at = Column(Text, default="")
    started_at = Column(Text, default="")
    finished_at = Column(Text, default="")

    session = relationship("SessionModel", back_populates="heapdump_reports")


class HeapdumpWorkerModel(Base):
    __tablename__ = "heapdump_workers"

    worker_id = Column(Text, primary_key=True)
    hostname = Column(Text, default="")
    pid = Column(Integer, default=0)
    started_at = Column(Text, default="")
    last_heartbeat = Column(Text, default="")
    current_task_id = Column(Text, default="")
    last_error = Column(Text, default="")


class UploadedFileModel(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        Index("ix_uploaded_files_user_id", "user_id"),
        Index("ix_uploaded_files_expires_at", "expires_at"),
    )

    file_id = Column(Text, primary_key=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    content_type = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    storage_backend = Column(Text, default="db")
    storage_key = Column(Text, default="")
    size = Column(Integer, default=0)
    sha256 = Column(Text, default="")
    expires_at = Column(Text, default="")
    created_at = Column(Text, default=_now_str)
    user = relationship("UserModel", back_populates="uploaded_files")


class SkillModel(Base):
    __tablename__ = "skills"
    __table_args__ = (
        Index("ix_skills_user_id", "user_id"),
    )

    id = Column(Text, primary_key=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    name = Column(Text, nullable=False)
    description = Column(Text, default="")
    instruction = Column(Text, nullable=False)
    category = Column(Text, default="")
    args_hint = Column(Text, default="input")
    source = Column(Text, default="manual")
    created_at = Column(Text, default=_now_str)
    user = relationship("UserModel", back_populates="skills")


class SystemSettingModel(Base):
    __tablename__ = "system_settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(Text, default=_now_str)


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=True)
    action = Column(Text, nullable=False)
    resource = Column(Text, default="")
    details = Column(Text, default="")
    ip = Column(Text, default="")
    created_at = Column(Text, default=_now_str)


class DiagnosisFeedbackModel(Base):
    """用户对 AI 诊断结论的反馈采集。"""
    __tablename__ = "diagnosis_feedback"
    __table_args__ = (
        Index("ix_diagnosis_feedback_user_id", "user_id"),
        Index("ix_diagnosis_feedback_target", "target_type", "target_id"),
        Index("ix_diagnosis_feedback_verdict", "verdict"),
        Index("ix_diagnosis_feedback_created_at", "created_at"),
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_diagnosis_feedback_user_target"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    target_type = Column(Text, nullable=False)
    target_id = Column(Text, nullable=False)
    session_id = Column(Text, nullable=True)
    verdict = Column(Text, nullable=False)
    comment = Column(Text, default="")
    prompt_key = Column(Text, default="")
    model = Column(Text, default="")
    created_at = Column(Text, default=_now_str)
    updated_at = Column(Text, default=_now_str)