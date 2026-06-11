import asyncio
import httpx
import sys
import time

BASE_URL = "http://localhost:8000"

import redis

# Connect to Redis to retrieve OTP for programmatic test registration
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

async def register_user(client, username, password, role="user"):
    try:
        # First check if login works (user already exists)
        login_resp = await client.post("/users/login", json={
            "username": username,
            "password": password
        })
        if login_resp.status_code == 200:
            print(f"User {username} already registered (login succeeded), skipping registration.")
            return

        email = f"{username}@test.com"
        # 1. Request OTP
        otp_resp = await client.post("/users/request-otp", json={"email": email})
        if otp_resp.status_code != 200:
            print(f"Failed to request OTP for {username}: {otp_resp.text}")
            return

        # 2. Retrieve OTP from Redis
        otp = r.get(f"otp:email:{email}")
        if not otp:
            print(f"Failed to retrieve OTP from Redis for {email}")
            return

        # 3. Register user with OTP and mobile number
        response = await client.post("/users/register", json={
            "username": username,
            "email": email,
            "password": password,
            "role": role,
            "mobile_number": "1234567890",
            "otp": otp
        })
        if response.status_code == 201:
            print(f"Registered user: {username}")
        else:
            print(f"Failed to register {username}: {response.text}")
    except Exception as e:
        print(f"Failed to register {username}: {e}")

async def get_token(client, username, password):
    response = await client.post("/users/login", json={
        "username": username,
        "password": password
    })
    if response.status_code == 200:
        return response.json()["access_token"]
    raise Exception(f"Failed to login: {username} - {response.text}")

async def place_bid(token, auction_id, amount):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        headers = {"Authorization": f"Bearer {token}"}
        start_time = time.time()
        response = await client.post(
            f"/auctions/{auction_id}/bid",
            json={"amount": amount},
            headers=headers,
            timeout=10.0
        )
        latency = (time.time() - start_time) * 1000
        return response.status_code, response.json(), latency

async def run_tests():
    print("=== STARTING CONCURRENCY TESTING ===")
    
    # 1. Register and Login users
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # Seed users if they don't exist
        await register_user(client, "test_admin", "adminpass", role="admin")
        for i in range(1, 6):
            await register_user(client, f"test_bidder{i}", f"bidderpass{i}")
        
        # Get login tokens
        admin_token = await get_token(client, "test_admin", "adminpass")
        bidder_tokens = []
        for i in range(1, 6):
            token = await get_token(client, f"test_bidder{i}", f"bidderpass{i}")
            bidder_tokens.append((f"test_bidder{i}", token))
            
        print("Successfully authenticated all test accounts.\n")
        
        # 2. Admin creates an auction item
        headers = {"Authorization": f"Bearer {admin_token}"}
        auction_resp = await client.post("/auctions", json={
            "title": "Institutional Gold Bullion Block B-40",
            "description": "High-purity gold bars for treasury settlement",
            "start_price": 10000.0,
            "duration_minutes": 5
        }, headers=headers)
        
        if auction_resp.status_code != 201:
            print(f"Failed to create auction: {auction_resp.text}")
            sys.exit(1)
            
        auction = auction_resp.json()
        auction_id = auction["id"]
        print(f"Created Auction ID: {auction_id} (Starting Price: ${auction['start_price']})")
        
        # 3. Test Concurrency: Place distinct increasing bids simultaneously
        # Let's verify that the system handles multiple concurrent updates cleanly.
        # User 1 bids 11000, User 2 bids 12000, User 3 bids 13000, User 4 bids 14000, User 5 bids 15000
        print("\n--- PHASE 1: Distinct Concurrent Increasing Bids ---")
        tasks = []
        for idx, (username, token) in enumerate(bidder_tokens):
            bid_amount = 11000.0 + (idx * 1000.0) # $11k, $12k, $13k, $14k, $15k
            tasks.append(place_bid(token, auction_id, bid_amount))
            
        results = await asyncio.gather(*tasks)
        
        for idx, (status_code, body, latency) in enumerate(results):
            user = bidder_tokens[idx][0]
            bid_val = 11000.0 + (idx * 1000.0)
            print(f"User: {user} bid ${bid_val} | Response Status: {status_code} | Body: {body} | Latency: {latency:.1f}ms")
            
        # 4. Fetch auction state to verify highest price is $15,000
        await asyncio.sleep(0.5) # Give the background worker a moment to process the queue
        status_resp = await client.get(f"/auctions/{auction_id}")
        auction_state = status_resp.json()
        print(f"\nVerification: Current Price in DB: ${auction_state['current_price']} (Expected $15000.0)")
        assert auction_state["current_price"] == 15000.0, "Concurrency price mismatch!"
        
        # 5. Test Concurrency: Strict Race (Same price from multiple clients at once)
        # All 5 bidders attempt to bid exactly $16,000 at the exact same time.
        # Under distributed locking rules, exactly ONE bidder must succeed (the first to get the lock),
        # and the other four must fail with "Bid amount must be strictly higher than current price".
        print("\n--- PHASE 2: Strict Race (Same Price of $16,000 Concurrent Bids) ---")
        tasks = []
        for username, token in bidder_tokens:
            tasks.append(place_bid(token, auction_id, 16000.0))
            
        results = await asyncio.gather(*tasks)
        
        success_count = 0
        failure_count = 0
        
        for idx, (status_code, body, latency) in enumerate(results):
            user = bidder_tokens[idx][0]
            print(f"User: {user} bid $16000.0 | Response Status: {status_code} | Body: {body} | Latency: {latency:.1f}ms")
            if status_code == 202:
                success_count += 1
            else:
                failure_count += 1
                
        print(f"\nStrict Race Results: Accepted Bids: {success_count} | Rejected Bids: {failure_count}")
        print("Note: In a perfect distributed lock system, Accepted Bids MUST be exactly 1, and Rejected Bids MUST be 4.")
        assert success_count == 1, f"Expected exactly 1 successful bid, got {success_count}!"
        assert failure_count == 4, f"Expected exactly 4 rejected bids, got {failure_count}!"
        print("SUCCESS: Redis Distributed Locking verified. No double-bids allowed at the same value.")

        # 6. Verify final database sync matches
        print("\nWaiting for background worker to persist all bids to PostgreSQL...")
        await asyncio.sleep(1.5)
        
        final_resp = await client.get(f"/auctions/{auction_id}")
        final_state = final_resp.json()
        print(f"Final Auction DB state - Current Price: ${final_state['current_price']} | Version: {final_state['version_id']}")
        assert final_state["current_price"] == 16000.0, "Final database price does not match expected highest bid!"
        print("SUCCESS: PostgreSQL database persistence verified and matches Redis cache.")
        print("\n=== CONCURRENCY TESTING COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    asyncio.run(run_tests())
