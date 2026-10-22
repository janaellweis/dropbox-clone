
#creates the connection interface to PostgreSQL.
from sqlalchemy import create_engine

# DeclarativeBase is the parent class for our database models.
# sessionmaker creates sessions for database operations.
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# find the current computer username for our local PostgreSQL setup.
from getpass import getuser


# url pointing to database.
DATABASE_URL = f"postgresql+psycopg://{getuser()}@localhost/dropbox_clone"

# create the SQLAlchemy engine used to communicate with PostgreSQL.
engine = create_engine(DATABASE_URL)

# factory to make database sessions.
# A session lets us query, insert, update, and delete records.
SessionLocal = sessionmaker(bind=engine)


# base class is inherited by all database models (three tables).
class Base(DeclarativeBase):
    pass
