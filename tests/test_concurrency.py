import asyncio
import httpx
import redis

BASE_URL = "http://localhost:8000"
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

async def test_simple_flow():
    print("=== STARTING SIMPLIFIED CONCURRENCY TEST ===")
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # Request OTP and Register a test user
        username = "test_user_small"
        email = f"{username}@test.com"
        await client.post("/users/request-otp", json={"email": email})
        otp = r.get(f"otp:email:{email}")
        
        await client.post("/users/register", json={
            "username": username, "email": email, "password": "password123",
            "role": "user", "mobile_number": "1234567890", "otp": otp
        })
        
        # Login
        login_resp = await client.post("/users/login", json={"username": username, "password": "password123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"Authenticated {username} successfully.")

        # Admin login to create an auction
        admin_login = await client.post("/users/login", json={"username": "admin", "password": "admin123"})
        admin_token = admin_login.json()["access_token"]
        
        # Create auction
        auction_resp = await client.post("/auctions", json={
            "title": "Gold Bullion", "description": "Gold bars", "start_price": 5000.0, "duration_minutes": 5
        }, headers={"Authorization": f"Bearer {admin_token}"})
        auction_id = auction_resp.json()["id"]
        print(f"Created auction ID {auction_id}.")

        # Concurrent bids
        async def place_bid(amount):
            return await client.post(f"/auctions/{auction_id}/bid", json={"amount": amount}, headers=headers)

        responses = await asyncio.gather(place_bid(6000.0), place_bid(6000.0))
        statuses = [resp.status_code for resp in responses]
        
        print(f"Bidding responses statuses: {statuses}")
        assert 202 in statuses, "One bid should have succeeded"
        print("SUCCESS: Simplified concurrency test passed!")

if __name__ == "__main__":
    asyncio.run(test_simple_flow())

