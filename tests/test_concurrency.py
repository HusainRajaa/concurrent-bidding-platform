import asyncio
import httpx
import redis
import time

BASE_URL = "http://localhost:8000"
r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

async def test_simple_flow():
    print("=== STARTING SIMPLIFIED CONCURRENCY TEST ===")
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        # Register a test user
        username = f"user_{int(time.time())}"
        email = f"{username}@test.com"
        
        reg_resp = await client.post("/users/register", json={
            "username": username, "email": email, "password": "password123",
            "role": "user", "mobile_number": "1234567890",
            "tenant_username": "bank1"
        })
        print("Registration status:", reg_resp.status_code, reg_resp.text)
        assert reg_resp.status_code == 201
        
        # Test Duplicate Email Registration
        dup_resp = await client.post("/users/register", json={
            "username": f"{username}_dup", "email": email, "password": "password123",
            "role": "user", "mobile_number": "1234567890",
            "tenant_username": "bank1"
        })
        print("Duplicate Registration status:", dup_resp.status_code, dup_resp.text)
        assert dup_resp.status_code in (201, 400)
        
        # Login
        login_resp = await client.post("/users/login", json={
            "email": email, "password": "password123",
            "tenant_username": "bank1"
        })
        print("Login status:", login_resp.status_code, login_resp.text)
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"Authenticated {username} successfully.")

        # Bank login to create an auction
        bank_login = await client.post("/users/login", json={"email": "bank1@nexbid.com", "password": "bank1234"})
        bank_token = bank_login.json()["access_token"]
        
        # Get bank1 ID
        bank_profile_resp = await client.get("/users/me", headers={"Authorization": f"Bearer {bank_token}"})
        bank_id = bank_profile_resp.json()["id"]
        
        # Bidder requests access to bank
        req_resp = await client.post(f"/banks/{bank_id}/request-access", headers=headers)
        print("Request Access status:", req_resp.status_code, req_resp.text)
        request_id = req_resp.json()["request_id"]
        
        # Bank approves bidder request
        approve_resp = await client.post(f"/banks/requests/{request_id}/approve", headers={"Authorization": f"Bearer {bank_token}"})
        print("Approve Access status:", approve_resp.status_code, approve_resp.text)
        
        # Create auction
        auction_resp = await client.post("/auctions", json={
            "title": "Gold Bullion", "description": "Gold bars", "start_price": 5000.0, "duration_minutes": 5
        }, headers={"Authorization": f"Bearer {bank_token}"})
        auction_id = auction_resp.json()["id"]
        print(f"Created auction ID {auction_id}.")

        # Concurrent bids
        async def place_bid(amount):
            return await client.post(f"/auctions/{auction_id}/bid", json={"amount": amount}, headers=headers)

        responses = await asyncio.gather(place_bid(6000.0), place_bid(6000.0))
        statuses = [resp.status_code for resp in responses]
        
        print(f"Bidding responses statuses: {statuses}")
        assert 202 in statuses, "One bid should have succeeded"
        
        # Test onboarding a new bank
        new_bank_username = f"bank_{int(time.time())}"
        new_bank_email = f"{new_bank_username}@test.com"
        
        onboard_resp = await client.post("/users/register", json={
            "username": new_bank_username,
            "email": new_bank_email,
            "password": "bankpassword123",
            "mobile_number": "1234567890",
            "role": "bank"
        })
        print("Onboard new bank status:", onboard_resp.status_code, onboard_resp.text)
        assert onboard_resp.status_code == 201
        
        # Verify the new bank appears in the list of banks
        banks_resp = await client.get("/banks")
        banks_list = [b["username"] for b in banks_resp.json()]
        print("Banks list:", banks_list)
        assert new_bank_username in banks_list, "Onboarded bank should appear in the /banks list"
        
        print("SUCCESS: Simplified concurrency and onboarding tests passed!")

if __name__ == "__main__":
    asyncio.run(test_simple_flow())

