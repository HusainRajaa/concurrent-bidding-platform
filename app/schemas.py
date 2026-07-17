from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional

# User Schemas
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    mobile_number: str = Field(..., min_length=10, max_length=20)
    otp: Optional[str] = Field(None, min_length=6, max_length=6)
    role: Optional[str] = Field("user", pattern="^(user|bank|admin)$")
    tenant_username: Optional[str] = None
    fullname: Optional[str] = None
    address: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str
    tenant_username: Optional[str] = None

class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    role: str
    mobile_number: Optional[str] = None
    tenant_id: Optional[int] = None
    tenant_username: Optional[str] = None
    fullname: Optional[str] = None
    address: Optional[str] = None
    branch: Optional[str] = None

    class Config:
        from_attributes = True

class OTPRequest(BaseModel):
    email: EmailStr

# Token Schemas
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
    user_id: Optional[int] = None

# Auction Schemas
class AuctionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=150)
    description: Optional[str] = None
    start_price: float = Field(..., gt=0)
    duration_minutes: int = Field(..., gt=0)  # Auction length in minutes

class AuctionResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    start_price: float
    current_price: float
    highest_bidder_id: Optional[int] = None
    end_time: datetime
    version_id: int
    bank_id: Optional[int] = None
    bank_username: Optional[str] = None

    class Config:
        from_attributes = True

# Bid Schemas
class BidCreate(BaseModel):
    amount: float = Field(..., gt=0)

class BidResponse(BaseModel):
    id: int
    auction_id: int
    user_id: int
    amount: float
    timestamp: datetime
    status: str

    class Config:
        from_attributes = True

class BidHistoryResponse(BaseModel):
    id: int
    auction_id: int
    user_id: int
    amount: float
    timestamp: datetime
    status: str
    username: str
    auction_title: str

    class Config:
        from_attributes = True


class BankCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6)
    mobile_number: str = Field(..., min_length=10, max_length=20)
    fullname: str = Field(..., min_length=1, max_length=100)
    address: str = Field(..., min_length=1, max_length=200)
    branch: str = Field(..., min_length=1, max_length=100)
    otp: Optional[str] = Field(None, min_length=6, max_length=6)


class AccessRequestDetail(BaseModel):
    id: int
    user_id: int
    bank_id: int
    status: str
    timestamp: datetime
    bidder_name: str
    bidder_email: str
    bidder_phone: str
    bidder_address: str

    class Config:
        from_attributes = True
