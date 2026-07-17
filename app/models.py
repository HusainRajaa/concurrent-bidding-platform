from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)  # "user", "bank" or "admin"
    mobile_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    fullname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(100), nullable=True)

    bids: Mapped[list["Bid"]] = relationship("Bid", back_populates="user", foreign_keys="[Bid.user_id]")
    won_auctions: Mapped[list["Auction"]] = relationship("Auction", back_populates="highest_bidder", foreign_keys="[Auction.highest_bidder_id]")
    tenant: Mapped[Optional["User"]] = relationship("User", remote_side=[id], lazy="selectin")

    @property
    def tenant_username(self) -> str | None:
        try:
            return self.tenant.username if self.tenant else None
        except Exception:
            return None


class Auction(Base):
    __tablename__ = "auctions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    start_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    highest_bidder_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_ended_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Optimistic locking version column
    version_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    bank_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    __mapper_args__ = {
        "version_id_col": version_id
    }

    # Relationships
    highest_bidder: Mapped[User | None] = relationship("User", back_populates="won_auctions", foreign_keys=[highest_bidder_id])
    bank: Mapped[User | None] = relationship("User", foreign_keys=[bank_id], lazy="selectin")
    bids: Mapped[list["Bid"]] = relationship("Bid", back_populates="auction", cascade="all, delete-orphan")

    @property
    def bank_username(self) -> str | None:
        try:
            return self.bank.username if self.bank else "System"
        except Exception:
            return "System"


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    auction_id: Mapped[int] = mapped_column(Integer, ForeignKey("auctions.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # "pending", "success", "failed"

    # Relationships
    auction: Mapped[Auction] = relationship("Auction", back_populates="bids")
    user: Mapped[User] = relationship("User", back_populates="bids", foreign_keys=[user_id])


class BankAccessRequest(Base):
    __tablename__ = "bank_access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    bank_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False) # "pending", "allowed", "disallowed"
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    bank: Mapped["User"] = relationship("User", foreign_keys=[bank_id])
