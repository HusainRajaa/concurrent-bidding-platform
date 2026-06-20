import logging
import random
import smtplib
import httpx
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import RedirectResponse
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
    get_current_admin
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
                mobile_number="+15555555555"
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
                mobile_number="+15555555555"
            )
            db.add(bidder2)
            logger.info("Seeded default bidder2 user (bidder2/bidder223).")
            
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
    
    # Check if SMTP configuration is set
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        # Mock Email log to stdout
        mock_log = f"""
============================================================
[MOCK MAIL SENDER] OTP Verification Code Sent
To: {email_to}
Subject: {subject}
OTP Code: {otp}
(Note: SMTP credentials are not configured. This is logged here for free testing.)
============================================================
"""
        logger.info(mock_log)
        return
        
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
        logger.info(f"Successfully sent OTP email to {email_to}")
    except Exception as e:
        logger.error(f"Failed to send OTP email to {email_to}: {e}", exc_info=True)

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
async def register(user_in: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    # 1. Verify OTP
    redis_key = f"otp:email:{user_in.email}"
    cached_otp = await redis_client.get(redis_key)
    
    if not cached_otp:
        raise HTTPException(status_code=400, detail="OTP expired or not found. Please request a new OTP.")
        
    if cached_otp != user_in.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP code.")
        
    # 2. Check duplicates
    result = await db.execute(select(models.User).where(models.User.username == user_in.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already registered")
        
    result = await db.execute(select(models.User).where(models.User.email == user_in.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
        
    # 3. Create User
    new_user = models.User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        mobile_number=user_in.mobile_number,
        role=user_in.role or "user"
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # 4. Clean up Redis OTP
    await redis_client.delete(redis_key)
    
    return new_user

@app.post("/users/login", response_model=schemas.Token)
async def login(user_in: schemas.UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.User).where(models.User.username == user_in.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role, "user_id": user.id}
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/users/me", response_model=schemas.UserResponse)
async def get_me(current_user: models.User = Depends(get_current_user)):
    return current_user

# ----------------- AUCTION ROUTERS -----------------

@app.get("/auctions", response_model=List[schemas.AuctionResponse])
async def list_auctions(db: AsyncSession = Depends(get_db)):
    # Returns all active auctions (end_time in the future)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(models.Auction).where(models.Auction.end_time > now).order_by(models.Auction.end_time.asc())
    )
    return result.scalars().all()

@app.post("/auctions", response_model=schemas.AuctionResponse, status_code=status.HTTP_201_CREATED)
async def create_auction(
    auction_in: schemas.AuctionCreate,
    db: AsyncSession = Depends(get_db),
    admin: models.User = Depends(get_current_admin)
):
    end_time = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=auction_in.duration_minutes)
    
    new_auction = models.Auction(
        title=auction_in.title,
        description=auction_in.description,
        start_price=auction_in.start_price,
        current_price=auction_in.start_price,
        end_time=end_time
    )
    db.add(new_auction)
    await db.commit()
    await db.refresh(new_auction)
    
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
async def get_recent_bids(db: AsyncSession = Depends(get_db)):
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
        .order_by(models.Bid.timestamp.desc())
        .limit(20)
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
    # Bidders should be regular users (admins are not supposed to bid, but we can allow it or restrict it)
    if current_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin accounts cannot place bids on auctions."
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
        
    await manager.connect(websocket)
    
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
    result = await db.execute(select(models.User).where(models.User.email == email))
    user = result.scalar_one_or_none()
    
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
        data={"sub": user.username, "role": user.role, "user_id": user.id}
    )
    
    # Redirect back to the frontend, passing the token in the URL query parameter
    return RedirectResponse(url=f"/?token={access_token}")

# ----------------- STATIC FRONTEND MOUNT -----------------

# Mount the static files folder. All HTML/CSS dashboard resides in app/static/
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
