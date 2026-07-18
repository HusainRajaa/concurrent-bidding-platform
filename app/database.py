from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

db_url = settings.DATABASE_URL
connect_args = {}

# Handle Neon/SSL query parameter compatibility with asyncpg
if "sslmode=require" in db_url or "neon.tech" in db_url:
    if "?" in db_url:
        db_url = db_url.split("?")[0]
    connect_args["ssl"] = True

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False, pool_pre_ping=True, connect_args=connect_args)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
