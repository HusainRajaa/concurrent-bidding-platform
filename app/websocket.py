import json
import logging
import asyncio
import jwt
import redis
from typing import List, Optional, Dict
from fastapi import WebSocket, WebSocketDisconnect, Query

from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[WebSocket, dict] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def connect(self, websocket: WebSocket, user_payload: dict):
        await websocket.accept()
        self.active_connections[websocket] = user_payload
        logger.info(f"New WebSocket connection accepted. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            del self.active_connections[websocket]
            logger.info(f"WebSocket disconnected. Total active: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        # Parse update event to extract target bank_id and message type
        try:
            data = json.loads(message)
            msg_bank_id = data.get("bank_id")
            msg_type = data.get("type")
        except Exception as e:
            logger.error(f"Error parsing broadcast message: {e}")
            msg_bank_id = None
            msg_type = None

        connections = list(self.active_connections.items())
        logger.info(f"Broadcasting update (bank_id: {msg_bank_id}, type: {msg_type}) to eligible clients among {len(connections)} active connections.")
        
        for websocket, user_payload in connections:
            if msg_bank_id is None:
                try:
                    await websocket.send_text(message)
                except Exception as e:
                    logger.error(f"Failed to send personal message: {e}")
                    self.disconnect(websocket)
                continue

            role = user_payload.get("role")
            user_id = user_payload.get("user_id")
            allowed_bank_ids = user_payload.get("allowed_bank_ids", [])

            # Check authorization to receive this real-time event based on message type
            if msg_type == "access_request":
                # Only target bank or admins receive access requests
                is_authorized = (role == "admin") or (role == "bank" and user_id == msg_bank_id)
            elif msg_type in ("access_approved", "access_declined"):
                # Only the requesting user or admins receive approval notifications
                target_user_id = data.get("user_id")
                is_authorized = (role == "admin") or (role == "user" and user_id == target_user_id)
                
                # Dynamically append allowed bank ID cache for bidder session upon approval
                if is_authorized and msg_type == "access_approved" and role == "user":
                    if msg_bank_id not in allowed_bank_ids:
                        allowed_bank_ids.append(msg_bank_id)
            else:
                # Regular bid updates or auction ended signals
                is_authorized = (
                    role == "admin"
                    or (role == "bank" and user_id == msg_bank_id)
                    or (role == "user" and msg_bank_id in allowed_bank_ids)
                )

            if is_authorized:
                try:
                    await websocket.send_text(message)
                except Exception as e:
                    logger.error(f"Failed to send personal message: {e}")
                    self.disconnect(websocket)

    async def start_redis_listener(self):
        """Starts the background task listening to Redis Pub/Sub updates."""
        self.is_running = True
        self._listener_task = asyncio.create_task(self._redis_listener_loop())
        logger.info("Redis Pub/Sub WebSocket broadcast listener started.")

    async def stop_redis_listener(self):
        """Stops the Redis Pub/Sub broadcast listener."""
        self.is_running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            logger.info("Redis Pub/Sub WebSocket broadcast listener stopped.")

    async def _redis_listener_loop(self):
        while self.is_running:
            pubsub = None
            try:
                pubsub = redis_client.pubsub()
                await pubsub.psubscribe("auction:*:updates")
                logger.info("Successfully subscribed to Redis Pub/Sub updates.")
                
                async for message in pubsub.listen():
                    logger.debug(f"Pub/Sub raw message: {message}")
                    if message["type"] == "pmessage":
                        data = message["data"]
                        logger.info(f"Pub/Sub received update data: {data}")
                        # Broadcast the received update to all connected WebSocket clients
                        await self.broadcast(data)
            except asyncio.CancelledError:
                break
            except (asyncio.TimeoutError, redis.exceptions.TimeoutError):
                # Normal idle timeout when no messages have been sent for a while
                logger.debug("Redis Pub/Sub connection idle. Re-subscribing silently...")
                continue
            except Exception as e:
                logger.error(f"Error in Redis Pub/Sub listener loop: {e}. Reconnecting in 3 seconds...", exc_info=True)
                await asyncio.sleep(3)
            finally:
                if pubsub:
                    try:
                        await pubsub.punsubscribe("auction:*:updates")
                        await pubsub.close()
                    except Exception:
                        pass

# Instantiate global connection manager
manager = ConnectionManager()

async def get_websocket_user(websocket: WebSocket, token: Optional[str] = Query(None)) -> Optional[dict]:
    """
    Validates the JWT token passed as a query param in the WebSocket connection.
    Returns decoded token data or closes connection with 4003 (forbidden).
    """
    if not token:
        await websocket.close(code=4003, reason="Token missing")
        return None
        
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        await websocket.close(code=4003, reason="Invalid token")
        return None
