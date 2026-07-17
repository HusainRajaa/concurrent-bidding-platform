import logging
import random
import smtplib
import httpx
import uuid
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import RedirectResponse, FileResponse
# pyrefly: ignore [missing-import]
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import engine, SessionLocal, Base, get_db
from app import models, schemas
from app.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_admin,
    get_current_bank_or_admin
)
from app.redis_client import redis_client, sync_auction_to_redis, sync_active_auctions_to_redis, place_bid_in_redis
from app.worker import BidsConsumer
from app.websocket import manager, get_websocket_user

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize background consumer
consumer = BidsConsumer(SessionLocal)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure SQL Tables are created
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("SQL database tables created.")
    
    # 2. Seed Default Users & Sync Redis
    async with SessionLocal() as db:
        # Check and seed default admin
        result = await db.execute(select(models.User).where(models.User.username == "admin"))
        admin = result.scalar_one_or_none()
        if not admin:
            admin = models.User(
                username="admin",
                email="admin@nexbid.com",
                hashed_password=hash_password("admin123"),
                role="admin",
                mobile_number="+15555555555"
            )
            db.add(admin)
            logger.info("Seeded default admin user (admin/admin123).")

        # Check and seed default users
        result = await db.execute(select(models.User).where(models.User.username == "bidder1"))
        if not result.scalar_one_or_none():
            bidder1 = models.User(
                username="bidder1",
                email="bidder1@nexbid.com",
                hashed_password=hash_password("bidder123"),
                role="user",
                mobile_number="+15555555555",
                fullname="Bidder One",
                address="456 Oak Lane, NY"
            )
            db.add(bidder1)
            logger.info("Seeded default bidder1 user (bidder1/bidder123).")

        result = await db.execute(select(models.User).where(models.User.username == "bidder2"))
        if not result.scalar_one_or_none():
            bidder2 = models.User(
                username="bidder2",
                email="bidder2@nexbid.com",
                hashed_password=hash_password("bidder223"),
                role="user",
                mobile_number="+15555555555",
                fullname="Bidder Two",
                address="789 Pine Rd, CA"
            )
            db.add(bidder2)
            logger.info("Seeded default bidder2 user (bidder2/bidder223).")

        result = await db.execute(select(models.User).where(models.User.username == "bank1"))
        if not result.scalar_one_or_none():
            bank1 = models.User(
                username="bank1",
                email="bank1@nexbid.com",
                hashed_password=hash_password("bank1234"),
                role="bank",
                mobile_number="+15555555555",
                fullname="Bank 1 Partner",
                branch="Ohio Central Branch",
                address="100 Main St, Columbus, OH"
            )
            db.add(bank1)
            logger.info("Seeded default bank1 user (bank1/bank1234).")
            
        await db.commit()
        
        # Load active auctions to Redis cache
        await sync_active_auctions_to_redis(db)
        
    # 3. Start Background tasks
    await consumer.start()
    await manager.start_redis_listener()
    
    yield
    
    # 4. Cleanup tasks on shutdown
    await consumer.stop()
    await manager.stop_redis_listener()
    await redis_client.aclose()
    logger.info("Lifespan shutdown complete.")


app = FastAPI(
    title="NexBid",
    description="High-Performance Concurrent Bidding System for Banks",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- AUTH ROUTERS -----------------

def send_otp_email(email_to: str, otp: str):
    subject = "NexBid Portal - Registration OTP Verification"
    body = f"""
    Welcome to NexBid!
    
    Your registration verification code is: {otp}
    
    This verification code will expire in 5 minutes.
    
    If you did not initiate this registration request, please ignore this email.
    """
    
    # Try sending via Resend API first if configured
    if settings.RESEND_API_KEY:
        try:
            logger.info(f"Attempting to send OTP email via Resend API to {email_to}...")
            res = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": "NexBid <onboarding@resend.dev>",
                    "to": [email_to],
                    "subject": subject,
                    "html": f"""
                    <h3>Welcome to NexBid!</h3>
                    <p>Your registration verification code is: <strong>{otp}</strong></p>
                    <p>This verification code will expire in 5 minutes.</p>
                    <p>If you did not initiate this registration request, please ignore this email.</p>
                    """
                },
                timeout=10.0
            )
            if res.status_code in (200, 201):
                logger.info(f"Successfully sent OTP email via Resend to {email_to}. Response: {res.text}")
                return
            else:
                logger.error(f"Resend API returned status {res.status_code}: {res.text}")
        except Exception as err:
            logger.error(f"Failed to send OTP email via Resend API to {email_to}: {err}", exc_info=True)

    # Try sending via Brevo API if configured
    if settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL:
        try:
            logger.info(f"Attempting to send OTP email via Brevo API to {email_to}...")
            res = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "sender": {"name": "NexBid", "email": settings.BREVO_SENDER_EMAIL},
                    "to": [{"email": email_to}],
                    "subject": subject,
                    "htmlContent": f"""
                    <h3>Welcome to NexBid!</h3>
                    <p>Your registration verification code is: <strong>{otp}</strong></p>
                    <p>This verification code will expire in 5 minutes.</p>
                    <p>If you did not initiate this registration request, please ignore this email.</p>
                    """
                },
                timeout=10.0
            )
            if res.status_code in (200, 201):
                logger.info(f"Successfully sent OTP email via Brevo to {email_to}. Response: {res.text}")
                return
            else:
                logger.error(f"Brevo API returned status {res.status_code}: {res.text}")
        except Exception as err:
            logger.error(f"Failed to send OTP email via Brevo API to {email_to}: {err}", exc_info=True)

    # Fallback to SMTP if SMTP is configured
    if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
        try:
            msg = MIMEMultipart()
            msg['From'] = settings.SMTP_FROM
            msg['To'] = email_to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM, email_to, msg.as_string())
            logger.info(f"Successfully sent OTP email via SMTP to {email_to}")
            return
        except Exception as e:
            logger.error(f"Failed to send OTP email via SMTP to {email_to}: {e}", exc_info=True)

    # Fallback to Mock Log if neither is configured
    mock_log = f"""
============================================================
[MOCK MAIL SENDER] OTP Verification Code Sent
To: {email_to}
Subject: {subject}
OTP Code: {otp}
(Note: Resend API Key or SMTP credentials are not configured. This is logged here for local testing.)
============================================================
"""
    logger.info(mock_log)

def send_welcome_email(email_to: str, username: str, fullname: Optional[str] = None):
    subject = "Welcome to NexBid Platform!"
    name = fullname or username
    body = f"""
    Welcome to NexBid, {name}!
    
    Your account has been successfully created.
    
    You can now log in to the portal and request bidding access to our partner banks.
    
    Username: {username}
    Email Address: {email_to}
    
    If you have any questions, feel free to contact us.
    """
    
    # Try sending via Resend API first if configured
    if settings.RESEND_API_KEY:
        try:
            logger.info(f"Attempting to send welcome email via Resend API to {email_to}...")
            res = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": "NexBid <onboarding@resend.dev>",
                    "to": [email_to],
                    "subject": subject,
                    "html": f"""
                    <h3>Welcome to NexBid!</h3>
                    <p>Hello <strong>{name}</strong>,</p>
                    <p>Thank you for signing up on the <strong>NexBid Bidding Platform</strong>. Your registration is successful!</p>
                    <p>You can now log in to the portal and request bidding access to our partner banks.</p>
                    <p><strong>Username:</strong> {username}<br><strong>Email:</strong> {email_to}</p>
                    """
                },
                timeout=10.0
            )
            if res.status_code in (200, 201):
                logger.info(f"Successfully sent welcome email via Resend to {email_to}.")
                return
            else:
                logger.error(f"Resend API returned status {res.status_code} for welcome email: {res.text}")
        except Exception as err:
            logger.error(f"Failed to send welcome email via Resend API to {email_to}: {err}", exc_info=True)

    # Try sending via Brevo API if configured
    if settings.BREVO_API_KEY and settings.BREVO_SENDER_EMAIL:
        try:
            logger.info(f"Attempting to send welcome email via Brevo API to {email_to}...")
            res = httpx.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "Content-Type": "application/json"
                },
                json={
                    "sender": {"name": "NexBid Platform", "email": settings.BREVO_SENDER_EMAIL},
                    "to": [{"email": email_to, "name": name}],
                    "subject": subject,
                    "htmlContent": f"""
                    <html>
                        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #dddddd; border-radius: 8px;">
                            <h2 style="color: #1e3a8a; text-align: center; border-bottom: 2px solid #1e3a8a; padding-bottom: 10px;">Welcome to NexBid!</h2>
                            <p>Hello <strong>{name}</strong>,</p>
                            <p>Thank you for signing up on the <strong>NexBid Bidding Platform</strong>. Your registration is successful!</p>
                            <p>You can now log in to the portal and request bidding access to our partner banks.</p>
                            <div style="background-color: #f3f4f6; padding: 15px; border-radius: 6px; margin: 20px 0;">
                                <p style="margin: 0;"><strong>Username:</strong> {username}</p>
                                <p style="margin: 0;"><strong>Email Address:</strong> {email_to}</p>
                            </div>
                            <p>Please secure your password and do not share it with anyone.</p>
                            <p style="text-align: center; margin-top: 30px;">
                                <a href="http://localhost:8000" style="background-color: #1e3a8a; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 4px; font-weight: bold; display: inline-block;">Get Started</a>
                            </p>
                            <hr style="border: 0; border-top: 1px solid #dddddd; margin: 30px 0;">
                            <p style="font-size: 0.8rem; color: #666666; text-align: center;">This is an automated message, please do not reply directly to this email.</p>
                        </body>
                    </html>
                    """
                },
                timeout=10.0
            )
            if res.status_code in (200, 201):
                logger.info(f"Successfully sent welcome email via Brevo to {email_to}.")
                return
            else:
                logger.error(f"Brevo API returned status {res.status_code} for welcome email: {res.text}")
        except Exception as err:
            logger.error(f"Failed to send welcome email via Brevo API to {email_to}: {err}", exc_info=True)

    # Fallback to SMTP if SMTP is configured
    if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
        try:
            msg = MIMEMultipart()
            msg['From'] = settings.SMTP_FROM
            msg['To'] = email_to
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM, email_to, msg.as_string())
            logger.info(f"Successfully sent welcome email via SMTP to {email_to}")
            return
        except Exception as e:
            logger.error(f"Failed to send welcome email via SMTP to {email_to}: {e}", exc_info=True)

    # Fallback to Mock Log if neither is configured
    mock_log = f"""
============================================================
[MOCK MAIL SENDER] Welcome Email Sent
To: {email_to}
Subject: {subject}
Username: {username}
(Note: Resend, Brevo API Keys, or SMTP credentials are not configured. This is logged here for local testing.)
============================================================
"""
    logger.info(mock_log)

@app.post("/users/request-otp")
async def request_otp(otp_req: schemas.OTPRequest, background_tasks: BackgroundTasks):
    email = otp_req.email
    # Generate 6 digit code
    otp = f"{random.randint(100000, 999999)}"
    
    # Cache OTP in Redis for 5 minutes
    redis_key = f"otp:email:{email}"
    await redis_client.set(redis_key, otp, ex=300)
    logger.info(f"Generated OTP {otp} for email {email} and stored in Redis.")
    
    # Send email in background
    background_tasks.add_task(send_otp_email, email, otp)
    
    return {"status": "success", "message": "OTP verification code has been sent to your email."}

@app.post("/users/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_in: schemas.UserCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # 0. Enforce OTP check (bypass for test emails)
    if not user_in.email.endswith("@test.com"):
        if not user_in.otp:
            raise HTTPException(status_code=400, detail="OTP verification code is required")
        redis_key = f"otp:email:{user_in.email}"
        cached_otp = await redis_client.get(redis_key)
        if not cached_otp or cached_otp != user_in.otp:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP verification code")
        await redis_client.delete(redis_key)

    # 1. Check duplicates
    result = await db.execute(select(models.User).where(models.User.username == user_in.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already registered")
        
    # result = await db.execute(select(models.User).where(models.User.email == user_in.email))
    # if result.scalar_one_or_none():
    #     raise HTTPException(status_code=400, detail="Email already registered")
        
    # 2. Bidders register globally (role="user")
    role = user_in.role or "user"

    # 3. Create User
    new_user = models.User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        mobile_number=user_in.mobile_number,
        role=role,
        fullname=user_in.fullname,
        address=user_in.address,
        tenant_id=None
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Send welcome email in background
    background_tasks.add_task(send_welcome_email, new_user.email, new_user.username, new_user.fullname)
    
    return new_user

@app.post("/users/login", response_model=schemas.Token)
async def login(user_in: schemas.UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.User)
        .options(selectinload(models.User.tenant))
        .where(models.User.email == user_in.email)
        .order_by(models.User.id.desc())
    )
    user = result.scalars().first()
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Enforce correct credentials login
    pass
            
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role, "user_id": user.id, "tenant_id": user.tenant_id}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=schemas.UserResponse)
async def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user

@app.get("/banks", response_model=List[schemas.UserResponse])
async def list_banks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.role == "bank"))
    return result.scalars().all()

@app.get("/bank-onboard")
async def get_bank_onboard():
    return FileResponse("app/static/bank-onboard.html")

# ----------------- AUCTION ROUTERS -----------------

@app.get("/auctions", response_model=List[schemas.AuctionResponse])
async def list_auctions(
    bank: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Returns all active auctions (end_time in the future)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(models.Auction).where(models.Auction.end_time > now)
    if bank:
        # Resolve bank username to user object
        bank_res = await db.execute(select(models.User).where(models.User.username == bank, models.User.role == "bank"))
        bank_obj = bank_res.scalar_one_or_none()
        if not bank_obj:
            raise HTTPException(status_code=404, detail="Bank not found")

        # Bidders must have allowed status to fetch a bank's private auctions
        if current_user.role == "user":
            access_res = await db.execute(
                select(models.BankAccessRequest)
                .where(
                    models.BankAccessRequest.user_id == current_user.id,
                    models.BankAccessRequest.bank_id == bank_obj.id,
                    models.BankAccessRequest.status == "allowed"
                )
            )
            if not access_res.scalar_one_or_none():
                raise HTTPException(status_code=403, detail="Access denied: You do not have approved access for this bank.")

        stmt = stmt.where(models.Auction.bank_id == bank_obj.id)
        
    result = await db.execute(stmt.order_by(models.Auction.end_time.asc()))
    return result.scalars().all()

@app.post("/auctions", response_model=schemas.AuctionResponse, status_code=status.HTTP_201_CREATED)
async def create_auction(
    auction_in: schemas.AuctionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_bank_or_admin)
):
    end_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=auction_in.duration_minutes)
    
    new_auction = models.Auction(
        title=auction_in.title,
        description=auction_in.description,
        start_price=auction_in.start_price,
        current_price=auction_in.start_price,
        end_time=end_time,
        bank_id=current_user.id
    )
    db.add(new_auction)
    await db.commit()
    await db.refresh(new_auction)
    
    # Eagerly load bank relationship for schemas.AuctionResponse
    result = await db.execute(
        select(models.Auction)
        .options(selectinload(models.Auction.bank))
        .where(models.Auction.id == new_auction.id)
    )
    new_auction = result.scalar_one()
    
    # Cache the new auction in Redis
    await sync_auction_to_redis(new_auction)
    
    return new_auction

@app.get("/auctions/{auction_id}", response_model=schemas.AuctionResponse)
async def get_auction(auction_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Auction).where(models.Auction.id == auction_id))
    auction = result.scalar_one_or_none()
    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")
    return auction

@app.get("/auctions/bids/recent", response_model=List[schemas.BidHistoryResponse])
async def get_recent_bids(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    stmt = (
        select(
            models.Bid.id,
            models.Bid.auction_id,
            models.Bid.user_id,
            models.Bid.amount,
            models.Bid.timestamp,
            models.Bid.status,
            models.User.username,
            models.Auction.title.label("auction_title")
        )
        .join(models.User, models.Bid.user_id == models.User.id)
        .join(models.Auction, models.Bid.auction_id == models.Auction.id)
        .where(models.Bid.status == "success")
    )
    
    # Enforce tenant isolation on recent bids list
    if current_user.role == "user":
        stmt = stmt.where(models.Auction.bank_id == current_user.tenant_id)
    elif current_user.role == "bank":
        stmt = stmt.where(models.Auction.bank_id == current_user.id)
        
    stmt = stmt.order_by(models.Bid.timestamp.desc()).limit(20)
    result = await db.execute(stmt)
    
    bids = []
    for row in result.all():
        bids.append({
            "id": row.id,
            "auction_id": row.auction_id,
            "user_id": row.user_id,
            "amount": row.amount,
            "timestamp": row.timestamp,
            "status": row.status,
            "username": row.username,
            "auction_title": row.auction_title
        })
    return bids

@app.get("/auctions/{auction_id}/bids", response_model=List[schemas.BidHistoryResponse])
async def get_auction_bids(auction_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            models.Bid.id,
            models.Bid.auction_id,
            models.Bid.user_id,
            models.Bid.amount,
            models.Bid.timestamp,
            models.Bid.status,
            models.User.username,
            models.Auction.title.label("auction_title")
        )
        .join(models.User, models.Bid.user_id == models.User.id)
        .join(models.Auction, models.Bid.auction_id == models.Auction.id)
        .where(models.Bid.auction_id == auction_id)
        .order_by(models.Bid.timestamp.desc())
    )
    result = await db.execute(stmt)
    
    bids = []
    for row in result.all():
        bids.append({
            "id": row.id,
            "auction_id": row.auction_id,
            "user_id": row.user_id,
            "amount": row.amount,
            "timestamp": row.timestamp,
            "status": row.status,
            "username": row.username,
            "auction_title": row.auction_title
        })
    return bids

# ----------------- BIDDING ROUTERS (FAST-WRITE PATH) -----------------

@app.post("/auctions/{auction_id}/bid", status_code=status.HTTP_202_ACCEPTED)
async def place_bid(
    auction_id: int,
    bid_in: schemas.BidCreate,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Bidders should be regular users (admins and banks are not supposed to bid)
    if current_user.role in ("admin", "bank"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Console accounts cannot place bids on auctions."
        )

    # Fetch auction details to verify tenant/bank boundaries
    result = await db.execute(select(models.Auction).where(models.Auction.id == auction_id))
    auction = result.scalar_one_or_none()
    if not auction:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Auction not found."
        )

    # Enforce private selling: normal users can only bid on auctions if they have approved access
    if current_user.role == "user":
        access_res = await db.execute(
            select(models.BankAccessRequest)
            .where(
                models.BankAccessRequest.user_id == current_user.id,
                models.BankAccessRequest.bank_id == auction.bank_id,
                models.BankAccessRequest.status == "allowed"
            )
        )
        if not access_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access forbidden: You must have approved bidding access to place bids for this bank."
            )

    # Place bid via Redis-based concurrency lock path
    success, message = await place_bid_in_redis(
        db=db,
        auction_id=auction_id,
        user_id=current_user.id,
        amount=bid_in.amount
    )
    
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
        
    return {"status": "success", "message": message}

# ----------------- WEBSOCKETS ROUTER -----------------

@app.websocket("/ws/auctions")
async def websocket_endpoint(websocket: WebSocket):
    # Handshake & Authentication check
    token = websocket.query_params.get("token")
    user_payload = await get_websocket_user(websocket, token)
    if not user_payload:
        return # Websocket closed in helper
        
    # Fetch user's allowed bank IDs if they are a standard bidder
    allowed_bank_ids = []
    if user_payload.get("role") == "user":
        async with SessionLocal() as db:
            res = await db.execute(
                select(models.BankAccessRequest.bank_id)
                .where(
                    models.BankAccessRequest.user_id == user_payload.get("user_id"),
                    models.BankAccessRequest.status == "allowed"
                )
            )
            allowed_bank_ids = [r[0] for r in res.all()]
    user_payload["allowed_bank_ids"] = allowed_bank_ids
        
    await manager.connect(websocket, user_payload)
    
    try:
        # Keep connection open. We don't expect messages from client
        # since WS is unidirectional (Server-to-Client updates).
        # We can implement a keep-alive listener.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# ----------------- GOOGLE OAUTH ROUTERS -----------------

@app.get("/auth/google/login")
async def google_login():
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file."
        )
        
    # Redirect to real Google consent screen
    google_oauth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={settings.GOOGLE_CLIENT_ID}&"
        f"redirect_uri={settings.GOOGLE_REDIRECT_URI}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile"
    )
    return RedirectResponse(url=google_oauth_url)

@app.get("/auth/google/callback")
async def google_callback(code: str, db: AsyncSession = Depends(get_db)):
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in your .env file."
        )
        
    email = None
    username = None
    
    # Real OAuth token exchange
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            token_resp = await client.post(token_url, data=data)
            if not token_resp.is_success:
                logger.error(f"Google token exchange failed: {token_resp.text}")
                raise HTTPException(status_code=400, detail="Failed to exchange authorization code for tokens.")
            
            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            
            # Fetch user details
            userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
            headers = {"Authorization": f"Bearer {access_token}"}
            userinfo_resp = await client.get(userinfo_url, headers=headers)
            
            if not userinfo_resp.is_success:
                logger.error(f"Google userinfo fetch failed: {userinfo_resp.text}")
                raise HTTPException(status_code=400, detail="Failed to retrieve user profile from Google.")
            
            user_info = userinfo_resp.json()
            email = user_info.get("email")
            username = user_info.get("name") or user_info.get("given_name") or email.split("@")[0]
        except Exception as e:
            logger.error(f"Error during Google OAuth process: {e}", exc_info=True)
            raise HTTPException(status_code=400, detail="Google authentication failed.")
                
    if not email:
        raise HTTPException(status_code=400, detail="Google did not return a valid email address.")
        
    # Check if user already exists in PostgreSQL database
    result = await db.execute(select(models.User).where(models.User.email == email).order_by(models.User.id.desc()))
    user = result.scalars().first()
    
    if not user:
        # Create a new user account with default values
        base_username = username.replace(" ", "_").lower()
        unique_username = base_username
        
        # Verify uniqueness of username
        check_user = await db.execute(select(models.User).where(models.User.username == unique_username))
        counter = 1
        while check_user.scalar_one_or_none():
            unique_username = f"{base_username}{counter}"
            check_user = await db.execute(select(models.User).where(models.User.username == unique_username))
            counter += 1
            
        random_pass = str(uuid.uuid4())  # Unused random password for Google-authenticated user
        
        user = models.User(
            username=unique_username,
            email=email,
            hashed_password=hash_password(random_pass),
            role="user",
            mobile_number="+15555555555"  # Generic placeholder
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"Registered new Google-authenticated user: {user.username} ({user.email})")
        
    # Issue our signed access token
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role, "user_id": user.id, "tenant_id": user.tenant_id}
    )
    
    # Redirect back to the frontend, passing the token in the URL query parameter
    return RedirectResponse(url=f"/?token={access_token}")

# ----------------- BANK SPECIFIC PORTAL ROUTERS & ACCESS CONTROLS -----------------

@app.post("/banks/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
async def register_bank(
    bank_in: schemas.BankCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    # 0. Enforce OTP check (bypass for test emails)
    if not bank_in.email.endswith("@test.com"):
        if not bank_in.otp:
            raise HTTPException(status_code=400, detail="OTP verification code is required")
        redis_key = f"otp:email:{bank_in.email}"
        cached_otp = await redis_client.get(redis_key)
        if not cached_otp or cached_otp != bank_in.otp:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP verification code")
        await redis_client.delete(redis_key)

    # 1. Check duplicates
    result = await db.execute(select(models.User).where(models.User.username == bank_in.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Bank identifier code already registered")
        
    # result = await db.execute(select(models.User).where(models.User.email == bank_in.email))
    # if result.scalar_one_or_none():
    #     raise HTTPException(status_code=400, detail="Bank email already registered")
        
    # 2. Create Bank User
    new_bank = models.User(
        username=bank_in.username,
        email=bank_in.email,
        hashed_password=hash_password(bank_in.password),
        mobile_number=bank_in.mobile_number,
        role="bank",
        fullname=bank_in.fullname,
        address=bank_in.address,
        branch=bank_in.branch
    )
    db.add(new_bank)
    await db.commit()
    await db.refresh(new_bank)
    
    # Send welcome email in background
    background_tasks.add_task(send_welcome_email, new_bank.email, new_bank.username, new_bank.fullname)
    
    return new_bank

@app.post("/banks/{bank_id}/request-access", status_code=status.HTTP_201_CREATED)
async def request_bank_access(
    bank_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != "user":
        raise HTTPException(status_code=400, detail="Only bidders can request access to banks.")
        
    # Verify bank exists
    bank_res = await db.execute(select(models.User).where(models.User.id == bank_id, models.User.role == "bank"))
    if not bank_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Bank not found")
        
    # Check duplicate request
    exist_res = await db.execute(
        select(models.BankAccessRequest)
        .where(models.BankAccessRequest.user_id == current_user.id, models.BankAccessRequest.bank_id == bank_id)
    )
    existing = exist_res.scalar_one_or_none()
    if existing:
        if existing.status == "disallowed":
            existing.status = "pending"
            existing.timestamp = datetime.utcnow()
            await db.commit()
            
            # Publish re-submission update
            access_payload = {
                "type": "access_request",
                "bank_id": bank_id,
                "user_id": current_user.id,
                "username": current_user.username,
                "fullname": current_user.fullname,
                "email": current_user.email,
                "mobile_number": current_user.mobile_number,
                "address": current_user.address,
                "request_id": existing.id,
                "timestamp": existing.timestamp.isoformat() + "Z"
            }
            await redis_client.publish("auction:access:updates", json.dumps(access_payload))
            
            return {"status": "success", "message": "Access request re-submitted.", "request_id": existing.id}
        return {"status": "success", "message": "Access request already submitted.", "request_id": existing.id, "approval_status": existing.status}
        
    # Create request
    req = models.BankAccessRequest(
        user_id=current_user.id,
        bank_id=bank_id,
        status="pending"
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    
    # Publish submission update
    access_payload = {
        "type": "access_request",
        "bank_id": bank_id,
        "user_id": current_user.id,
        "username": current_user.username,
        "fullname": current_user.fullname,
        "email": current_user.email,
        "mobile_number": current_user.mobile_number,
        "address": current_user.address,
        "request_id": req.id,
        "timestamp": req.timestamp.isoformat() + "Z"
    }
    await redis_client.publish("auction:access:updates", json.dumps(access_payload))
    
    return {"status": "success", "message": "Access request submitted successfully.", "request_id": req.id}

@app.get("/banks/{bank_id}/access-status")
async def get_bank_access_status(
    bank_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    res = await db.execute(
        select(models.BankAccessRequest)
        .where(models.BankAccessRequest.user_id == current_user.id, models.BankAccessRequest.bank_id == bank_id)
    )
    req = res.scalar_one_or_none()
    if not req:
        return {"status": "none"}
    return {"status": req.status, "request_id": req.id}

@app.get("/banks/requests", response_model=List[schemas.AccessRequestDetail])
async def list_bank_requests(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != "bank":
        raise HTTPException(status_code=403, detail="Only banks can access their notifications.")
        
    stmt = (
        select(
            models.BankAccessRequest.id,
            models.BankAccessRequest.user_id,
            models.BankAccessRequest.bank_id,
            models.BankAccessRequest.status,
            models.BankAccessRequest.timestamp,
            models.User.fullname.label("bidder_name"),
            models.User.email.label("bidder_email"),
            models.User.mobile_number.label("bidder_phone"),
            models.User.address.label("bidder_address")
        )
        .join(models.User, models.BankAccessRequest.user_id == models.User.id)
        .where(
            models.BankAccessRequest.bank_id == current_user.id,
            models.BankAccessRequest.status == "pending"
        )
        .order_by(models.BankAccessRequest.timestamp.desc())
    )
    
    result = await db.execute(stmt)
    requests_list = []
    for row in result.all():
        requests_list.append({
            "id": row.id,
            "user_id": row.user_id,
            "bank_id": row.bank_id,
            "status": row.status,
            "timestamp": row.timestamp,
            "bidder_name": row.bidder_name or "Unknown",
            "bidder_email": row.bidder_email,
            "bidder_phone": row.bidder_phone or "None",
            "bidder_address": row.bidder_address or "None"
        })
    return requests_list

@app.post("/banks/requests/{request_id}/approve")
async def approve_access_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != "bank":
        raise HTTPException(status_code=403, detail="Only banks can approve requests.")
        
    res = await db.execute(
        select(models.BankAccessRequest)
        .where(models.BankAccessRequest.id == request_id, models.BankAccessRequest.bank_id == current_user.id)
    )
    req = res.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
        
    req.status = "allowed"
    await db.commit()

    # Publish approval event
    approval_payload = {
        "type": "access_approved",
        "bank_id": current_user.id,
        "bank_name": current_user.fullname or current_user.username,
        "user_id": req.user_id,
        "status": "allowed"
    }
    await redis_client.publish("auction:access:updates", json.dumps(approval_payload))
    
    return {"status": "success", "message": "Bidding access approved."}

@app.post("/banks/requests/{request_id}/disallow")
async def disallow_access_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    if current_user.role != "bank":
        raise HTTPException(status_code=403, detail="Only banks can disallow requests.")
        
    res = await db.execute(
        select(models.BankAccessRequest)
        .where(models.BankAccessRequest.id == request_id, models.BankAccessRequest.bank_id == current_user.id)
    )
    req = res.scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
        
    req.status = "disallowed"
    await db.commit()

    # Publish declined event
    declined_payload = {
        "type": "access_declined",
        "bank_id": current_user.id,
        "bank_name": current_user.fullname or current_user.username,
        "user_id": req.user_id,
        "status": "disallowed"
    }
    await redis_client.publish("auction:access:updates", json.dumps(declined_payload))
    
    return {"status": "success", "message": "Bidding access disallowed."}

# ----------------- PAGE ROUTERS -----------------

@app.get("/")
async def redirect_to_bidding():
    return RedirectResponse(url="/bidding")

@app.get("/bidding")
async def get_bidding():
    return FileResponse("app/static/index.html")

@app.get("/bank")
async def get_bank():
    return FileResponse("app/static/bank.html")

# ----------------- STATIC FRONTEND MOUNT -----------------

# Mount the static files folder. All HTML/CSS dashboard resides in app/static/
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
