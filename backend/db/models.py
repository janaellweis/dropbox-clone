import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from backend.db.database import Base


class File(Base):
    __tablename__ = "files"

    event_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )

    key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "uq_files_active_key",
            "key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class FileVersion(Base):
    __tablename__ = "file_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id"),
        nullable=False,
    )

    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "version_number",
            name="uq_file_version_number",
        ),
    )


class ChangeAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    RENAME = "RENAME"


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PartitionState(Base):
    __tablename__ = "partition_state"

    partition_id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    last_position: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default="0",
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "event_sequence",
            name="uq_outbox_file_event_sequence",
        ),
    )

    event_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        unique=True,
        nullable=False,
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id"),
        nullable=False,
    )

    action: Mapped[ChangeAction] = mapped_column(
        Enum(ChangeAction),
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus),
        nullable=False,
        default=OutboxStatus.PENDING,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    last_error: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    partition_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )


class ChangeJournal(Base):
    __tablename__ = "change_journal"

    __table_args__ = (
        UniqueConstraint(
            "partition_id",
            "partition_position",
            name="uq_journal_partition_position",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id"),
        nullable=False,
    )

    action: Mapped[ChangeAction] = mapped_column(
        Enum(ChangeAction),
        nullable=False,
    )

    path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )

    old_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    version_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        unique=True,
        nullable=False,
    )

    request_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    outbox_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("outbox_events.id"),
        unique=True,
        nullable=True,
    )

    partition_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    partition_position: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
