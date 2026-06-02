import json
import logging
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm.exc import StaleDataError

from app.redis_client import redis_client
from app import models

logger = logging.getLogger(__name__)

class BidsConsumer:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory
        self.is_running = False
        self._task = None
        self._end_task = None

    async def start(self):
        self.is_running = True
        self._task = asyncio.create_task(self._consume_loop())
        self._end_task = asyncio.create_task(self._end_checker_loop())
        logger.info("Bids persistence and Auction End Checker background tasks started.")

    async def stop(self):
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._end_task:
            self._end_task.cancel()
            try:
                await self._end_task
            except asyncio.CancelledError:
                pass
        logger.info("Bids persistence and Auction End Checker background tasks stopped.")

    async def _consume_loop(self):
        while self.is_running:
            try:
                # Use blpop (blocking list pop) to block and wait for items, avoiding tight CPU loops
                # timeout=1 means it will block for at most 1 second if the queue is empty
                res = await redis_client.blpop("bids:queue", timeout=1)
                if not res:
                    continue
                
                _, payload_str = res
                payload = json.loads(payload_str)
                
                # Process the bid with retries for optimistic locking conflicts
                await self._persist_bid_with_retry(payload)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in background consumer loop: {e}", exc_info=True)
                await asyncio.sleep(1) # Backoff on critical error

    async def _persist_bid_with_retry(self, payload: dict, retries: int = 3):
        auction_id = payload["auction_id"]
        user_id = payload["user_id"]
        amount = payload["amount"]
        # Convert timezone-aware string to native naive datetime representing UTC
        timestamp_str = payload["timestamp"]
        # Python isoformat can have Z or +00:00. Replace Z with +00:00 for fromisoformat
        clean_timestamp_str = timestamp_str[:-1] + "+00:00" if timestamp_str.endswith("Z") else timestamp_str
        timestamp = datetime.fromisoformat(clean_timestamp_str).replace(tzinfo=None)

        for attempt in range(retries):
            async with self.session_factory() as session:
                async with session.begin():
                    try:
                        # 1. Fetch auction
                        result = await session.execute(
                            select(models.Auction).where(models.Auction.id == auction_id)
                        )
                        auction = result.scalar_one_or_none()
                        
                        if not auction:
                            logger.error(f"Postgres sync failed: Auction {auction_id} not found in DB.")
                            # Record failed bid in db for audit
                            failed_bid = models.Bid(
                                auction_id=auction_id,
                                user_id=user_id,
                                amount=amount,
                                timestamp=timestamp,
                                status="failed"
                            )
                            session.add(failed_bid)
                            return
                        
                        # 2. Check if the bid is still higher
                        if amount > auction.current_price:
                            # Update auction
                            auction.current_price = amount
                            auction.highest_bidder_id = user_id
                            
                            # Record successful bid
                            bid = models.Bid(
                                auction_id=auction_id,
                                user_id=user_id,
                                amount=amount,
                                timestamp=timestamp,
                                status="success"
                            )
                            session.add(bid)
                            
                            # Save changes - version_id will trigger optimistic check
                            await session.commit()
                            logger.info(f"Persisted bid of {amount} on auction {auction_id} by user {user_id}")
                            return
                        else:
                            # Stale bid (already outbid in db)
                            logger.warning(f"Bid of {amount} on auction {auction_id} is stale (DB price is {auction.current_price}). Marking failed.")
                            bid = models.Bid(
                                auction_id=auction_id,
                                user_id=user_id,
                                amount=amount,
                                timestamp=timestamp,
                                status="failed"
                            )
                            session.add(bid)
                            await session.commit()
                            return
                            
                    except StaleDataError:
                        # Optimistic locking failure (another transaction committed first)
                        await session.rollback()
                        logger.warning(f"Optimistic lock conflict for auction {auction_id} (attempt {attempt + 1}/{retries}). Retrying...")
                        await asyncio.sleep(0.05 * (attempt + 1)) # Exponential-ish sleep before retry
                    except Exception as e:
                        await session.rollback()
                        logger.error(f"Error persisting bid (attempt {attempt + 1}/{retries}): {e}", exc_info=True)
                        await asyncio.sleep(0.1)
        
        logger.error(f"Failed to persist bid of {amount} on auction {auction_id} after {retries} attempts.")

    async def _end_checker_loop(self):
        while self.is_running:
            try:
                await self._check_ended_auctions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in auction end checker loop: {e}", exc_info=True)
            await asyncio.sleep(5)  # Check every 5 seconds

    async def _check_ended_auctions(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with self.session_factory() as session:
            # Select active auctions that have ended but haven't been processed yet
            result = await session.execute(
                select(models.Auction)
                .where(models.Auction.end_time <= now)
                .where(models.Auction.is_ended_processed == False)
            )
            ended_auctions = result.scalars().all()
            
            for auction in ended_auctions:
                # 1. Fetch highest bidder username if exists
                username = None
                if auction.highest_bidder_id:
                    user_res = await session.execute(
                        select(models.User.username).where(models.User.id == auction.highest_bidder_id)
                    )
                    username = user_res.scalar_one_or_none()
                
                # 2. Mark as processed
                auction.is_ended_processed = True
                session.add(auction)
                await session.commit()
                
                # 3. Log to stdout/console
                if username:
                    sold_log = f"Auction '{auction.title}' (ID: {auction.id}) ended. Sold to Bidder '{username}' (ID: {auction.highest_bidder_id}) for ${auction.current_price:,.2f}."
                else:
                    sold_log = f"Auction '{auction.title}' (ID: {auction.id}) ended. No bids received."
                
                logger.info(f"[AUCTION ENDED LOG] {sold_log}")
                
                # 4. Remove auction from Redis cache to keep it clean
                await redis_client.delete(f"auction:{auction.id}")
                
                # 5. Broadcast to WebSocket client so it posts to activity stream in real-time
                end_payload = {
                    "type": "auction_ended",
                    "auction_id": auction.id,
                    "auction_title": auction.title,
                    "highest_bidder_id": auction.highest_bidder_id,
                    "username": username,
                    "price": auction.current_price if username else auction.start_price
                }
                logger.info(f"Publishing auction end broadcast to Redis Pub/Sub: auction:{auction.id}:updates")
                await redis_client.publish(
                    f"auction:{auction.id}:updates",
                    json.dumps(end_payload)
                )
