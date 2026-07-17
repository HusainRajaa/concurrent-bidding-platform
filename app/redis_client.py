import json
import logging
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Tuple, Optional
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.config import settings
from app import models

logger = logging.getLogger(__name__)

redis_client = aioredis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    health_check_interval=30
)

RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

async def acquire_lock(lock_key: str, token: str, timeout_ms: int = 2000, max_retries: int = 5, retry_delay_ms: int = 50) -> bool:
    for _ in range(max_retries):
        success = await redis_client.set(lock_key, token, nx=True, px=timeout_ms)
        if success:
            return True
        await asyncio.sleep(retry_delay_ms / 1000.0)
    return False

async def release_lock(lock_key: str, token: str) -> bool:
    try:
        result = await redis_client.eval(RELEASE_LOCK_LUA, 1, lock_key, token)
        return bool(result)
    except Exception as e:
        logger.error(f"Failed to release lock {lock_key}: {e}")
        return False

async def sync_auction_to_redis(auction: models.Auction):
    auction_id = auction.id
    end_time_iso = auction.end_time.replace(tzinfo=timezone.utc).isoformat() if auction.end_time.tzinfo else auction.end_time.isoformat() + "Z"
    
    await redis_client.hset(f"auction:{auction_id}", mapping={
        "id": str(auction_id),
        "title": auction.title,
        "current_price": str(auction.current_price),
        "highest_bidder_id": str(auction.highest_bidder_id) if auction.highest_bidder_id else "",
        "end_time": end_time_iso,
        "bank_id": str(auction.bank_id)
    })

async def sync_active_auctions_to_redis(db: AsyncSession):
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(models.Auction).where(models.Auction.end_time > now)
        )
        active_auctions = result.scalars().all()
        for auction in active_auctions:
            await sync_auction_to_redis(auction)
        logger.info(f"Successfully synced {len(active_auctions)} active auctions to Redis.")
    except Exception as e:
        logger.error(f"Error syncing active auctions to Redis: {e}")

async def place_bid_in_redis(db: AsyncSession, auction_id: int, user_id: int, amount: float) -> Tuple[bool, str]:
    lock_key = f"lock:auction:{auction_id}"
    token = str(uuid.uuid4())
    
    auction_exists = await redis_client.exists(f"auction:{auction_id}")
    if not auction_exists:
        result = await db.execute(select(models.Auction).where(models.Auction.id == auction_id))
        auction = result.scalar_one_or_none()
        if not auction:
            return False, "Auction not found"
        await sync_auction_to_redis(auction)
    
    auction_data = await redis_client.hgetall(f"auction:{auction_id}")
    end_time_str = auction_data.get("end_time")
    
    try:
        clean_end_time_str = end_time_str[:-1] + "+00:00" if end_time_str.endswith("Z") else end_time_str
        end_time = datetime.fromisoformat(clean_end_time_str)
        if datetime.now(timezone.utc) > end_time:
            return False, "Auction has already ended"
    except Exception as e:
        logger.error(f"Error parsing auction end time {end_time_str}: {e}")
        return False, "Auction details invalid"

    lock_acquired = await acquire_lock(lock_key, token, timeout_ms=2000)
    if not lock_acquired:
        return False, "Server is busy processing bids for this auction. Please retry."
        
    try:
        current_price = float(await redis_client.hget(f"auction:{auction_id}", "current_price") or 0.0)
        
        if amount <= current_price:
            return False, "Bid amount must be strictly higher than current price"
            
        timestamp = datetime.now(timezone.utc).isoformat()
        
        await redis_client.hset(f"auction:{auction_id}", mapping={
            "current_price": str(amount),
            "highest_bidder_id": str(user_id)
        })
        
        bid_payload = {
            "auction_id": auction_id,
            "user_id": user_id,
            "amount": amount,
            "timestamp": timestamp,
            "bank_id": int(auction_data.get("bank_id") or 0)
        }
        await redis_client.rpush("bids:queue", json.dumps(bid_payload))
        
        logger.info(f"Publishing bid update to Redis Pub/Sub: auction:{auction_id}:updates -> {bid_payload}")
        await redis_client.publish(
            f"auction:{auction_id}:updates",
            json.dumps(bid_payload)
        )
        
        return True, "Bid accepted"
    finally:
        await release_lock(lock_key, token)
