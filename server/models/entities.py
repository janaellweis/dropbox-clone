
import uuid
from datetime import datetime, timezone

# SQLAlchemy column types and database constraints.
from sqlalchemy import (
    Boolean,
    DateTime,
    String,
    ForeignKey,
    Integer,
    BigInteger,
    UniqueConstraint,
    Index,
    text,
)

# Tools for mapping Python classes to database tables.
from sqlalchemy.orm import Mapped, mapped_column

# Import the Base class that the models inherit from in (database.py)
from server.database import Base


# TABLE 1: Stores file metadata, not the actual file contents.
class File(Base):
    __tablename__ = "files"

    # Only active files must have unique paths.
    # Deleted files can have paths that are reused later,  ex offlien client can know if a file was deleted later
    __table_args__ = (
        Index(
            "uq_active_file_key",
            "key",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )

    #file gets a unique UUID that identifies it independently of its name or path
    #allowing us to rename or move files without changing their identity.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4
    )

    #key stores the file's relative path 
    #all clients can recreate the same folder structure when synchronizing files.
    key: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    # Soft deletion to not completely remove the file from existence
    # just to let other clients know if they were offline
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    #time when the file record was first created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    # time of the latest metadata update.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    #tells us if this record is a folder or a regular file.
    is_folder: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

# TABLE 2 Stores the version history of each file
class FileVersion(Base):
    __tablename__ = "file_versions"

    #file cant have two records with the same version number
    __table_args__ = (
        UniqueConstraint("file_id", "version_number"),
    )

    #unique ID for this version record
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4
    )

    #FK to link this version to its parent file.
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id"),
        nullable=False
    )

    # version number
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    # the SHA-256 hash
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False
    )

    # size of file contents in bytes.
    size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False
    )

    # time when this version was created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# TABLE 3 records changes that clients need to synchronize
class ChangeJournal(Base):
    __tablename__ = "change_journal"

    # increasing event ID used to have changes in order 
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    # identifies which file was changed
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id"),
        nullable=False
    )

    # what type of change happened to the file
    action: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    # file path of changed file
    path: Mapped[str] = mapped_column(
        String,
        nullable=False
    )

    # previous path
    # None means no previous path was provided.
    old_path: Mapped[str | None] = mapped_column(
        String,
        nullable=True
    )

    # to know what version is changed incase one is outdated and not synchronized
    version_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )

    # time when the change happened
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
