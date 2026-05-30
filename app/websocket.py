import json
import logging
import asyncio
import jwt
from typing import List, Optional
from fastapi import WebSocket, WebSocketDisconnect, Query

from app.config import settings
from app.redis_client import redis_client

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._listener_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New WebSocket connection accepted. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total active: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        connections = list(self.active_connections)
        logger.info(f"Broadcasting update to {len(connections)} clients: {message}")
        for connection in connections:
            try:
                await connection.send_text(message)
                logger.info("Sent WebSocket packet successfully.")
            except Exception as e:
                logger.error(f"Failed to send message to connection: {e}")
                self.disconnect(connection)

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
