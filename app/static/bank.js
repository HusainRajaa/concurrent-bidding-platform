const BASE_URL = window.location.origin;
let socket = null;
let currentBank = null;

// On Page Load
document.addEventListener("DOMContentLoaded", () => {
    const token = localStorage.getItem("token_bank");
    if (token) {
        loadDashboard(token);
    } else {
        document.getElementById("auth-section").classList.remove("hidden");
    }
});

function switchAuthTab(type) {
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");
    const tabLogin = document.getElementById("tab-login");
    const tabRegister = document.getElementById("tab-register");
    const authError = document.getElementById("auth-error");
    
    authError.classList.add("hidden");
    authError.innerText = "";

    if (type === "login") {
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
        tabLogin.classList.add("active");
        tabRegister.classList.remove("active");
    } else {
        loginForm.classList.add("hidden");
        registerForm.classList.remove("hidden");
        tabLogin.classList.remove("active");
        tabRegister.classList.add("active");
    }
}

function fillCredentials(email, password) {
    document.getElementById("login-email").value = email;
    document.getElementById("login-password").value = password;
}

// Authentication Handlers
async function handleLogin(event) {
    event.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    const authError = document.getElementById("auth-error");

    try {
        const response = await fetch(`${BASE_URL}/users/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Authentication failed");
        }

        // Verify the user is a bank partner
        const tempToken = data.access_token;
        const profileResp = await fetch(`${BASE_URL}/users/me`, {
            headers: { "Authorization": `Bearer ${tempToken}` }
        });
        const profile = await profileResp.json();
        if (profile.role !== "bank" && profile.role !== "admin") {
            throw new Error("This portal is restricted to partner banks and console administrators.");
        }

        localStorage.setItem("token_bank", tempToken);
        authError.classList.add("hidden");
        loadDashboard(tempToken);
    } catch (err) {
        authError.innerText = err.message;
        authError.classList.remove("hidden");
    }
}

async function handleRegister(event) {
    event.preventDefault();
    const fullname = document.getElementById("reg-fullname").value;
    const username = document.getElementById("reg-username").value;
    const email = document.getElementById("reg-email").value;
    const branch = document.getElementById("reg-branch").value;
    const mobile_number = document.getElementById("reg-mobile").value;
    const address = document.getElementById("reg-address").value;
    const password = document.getElementById("reg-password").value;
    const otpInput = document.getElementById("reg-otp");
    const otp = otpInput ? otpInput.value.trim() : null;
    const authError = document.getElementById("auth-error");

    try {
        const response = await fetch(`${BASE_URL}/banks/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                fullname,
                username,
                email,
                branch,
                mobile_number,
                address,
                password,
                otp
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Registration failed");
        }

        // Switch to login tab and show success message
        switchAuthTab("login");
        document.getElementById("login-email").value = email;
        authError.innerText = "Bank registered successfully! Please sign in.";
        authError.classList.remove("hidden");
    } catch (err) {
        authError.innerText = err.message;
        authError.classList.remove("hidden");
    }
}

// Load Dashboard Screen
async function loadDashboard(token) {
    try {
        const profileResp = await fetch(`${BASE_URL}/users/me`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        
        if (!profileResp.ok) {
            throw new Error("Session expired. Please log in again.");
        }

        currentBank = await profileResp.json();
        
        // Show/hide views
        document.getElementById("auth-section").classList.add("hidden");
        document.getElementById("bank-dashboard").classList.remove("hidden");
        document.getElementById("user-profile").classList.remove("hidden");
        
        // Render bank details
        document.getElementById("nav-username").innerText = currentBank.fullname;
        document.getElementById("nav-role").innerText = currentBank.role.toUpperCase();
        
        document.getElementById("bank-title").innerText = `${currentBank.fullname} Bidding Dashboard`;
        document.getElementById("bank-subtitle").innerText = `${currentBank.branch || "Headquarters"} | Code: ${currentBank.username}`;

        // Initialize lists and socket connection
        fetchRequests(token);
        fetchActiveListings(token);
        fetchTradeHistory(token);
        connectWebSocket(token);

        // Start request polling (every 5 seconds)
        setInterval(() => {
            const activeToken = localStorage.getItem("token_bank");
            if (activeToken) {
                fetchRequests(activeToken);
            }
        }, 5000);

    } catch (err) {
        logout();
    }
}

// Fetch Access Requests from Bidders
async function fetchRequests(token) {
    try {
        const response = await fetch(`${BASE_URL}/banks/requests`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        const requests = await response.json();
        
        const container = document.getElementById("requests-container");
        if (requests.length === 0) {
            container.innerHTML = `<div class="empty-state">No access requests at this time.</div>`;
            return;
        }

        let html = "";
        requests.forEach(req => {
            const date = new Date(req.timestamp).toLocaleTimeString();
            let actionButtons = "";
            if (req.status === "pending") {
                actionButtons = `
                    <div style="display:flex; gap:10px; margin-top:10px;">
                        <button onclick="updateRequestStatus(${req.id}, 'approve')" class="btn btn-sm" style="background:#10b981; color:#fff;">Approve</button>
                        <button onclick="updateRequestStatus(${req.id}, 'disallow')" class="btn btn-secondary btn-sm">Disallow</button>
                    </div>
                `;
            } else {
                const badgeColor = req.status === "allowed" ? "#10b981" : "#ef4444";
                actionButtons = `<span style="font-weight:600; color:${badgeColor}; font-size:0.9rem; text-transform:uppercase;">${req.status}</span>`;
            }

            html += `
                <div class="ledger-item" style="padding: 15px; margin-bottom: 12px; display: block; border-left: 4px solid #3b82f6;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <strong>${req.bidder_name}</strong>
                        <span style="font-size:0.8rem; opacity:0.6;">${date}</span>
                    </div>
                    <div style="font-size:0.85rem; opacity:0.8; margin-top:5px; line-height:1.4;">
                        Email: ${req.bidder_email}<br>
                        Phone: ${req.bidder_phone}<br>
                        Address: ${req.bidder_address}
                    </div>
                    ${actionButtons}
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (err) {
        console.error("Failed to load requests:", err);
    }
}

// Approve / Disallow Bidder Requests
async function updateRequestStatus(reqId, action) {
    const token = localStorage.getItem("token_bank");
    try {
        const response = await fetch(`${BASE_URL}/banks/requests/${reqId}/${action}`, {
            method: "POST",
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) {
            throw new Error("Failed to process request action");
        }
        fetchRequests(token);
    } catch (err) {
        alert(err.message);
    }
}

// Create Bidding Asset
async function handleCreateAuction(event) {
    event.preventDefault();
    const token = localStorage.getItem("token_bank");
    const title = document.getElementById("auc-title").value;
    const description = document.getElementById("auc-desc").value;
    const start_price = parseFloat(document.getElementById("auc-price").value);
    const duration_minutes = parseInt(document.getElementById("auc-duration").value);

    try {
        const response = await fetch(`${BASE_URL}/auctions`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ title, description, start_price, duration_minutes })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Listing creation failed");
        }

        // Reset form and reload list
        event.target.reset();
        fetchActiveListings(token);
    } catch (err) {
        alert(err.message);
    }
}

// Fetch bank's listings
async function fetchActiveListings(token) {
    try {
        const response = await fetch(`${BASE_URL}/auctions?bank=${currentBank.username}`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        const listings = await response.json();
        renderListings(listings);
    } catch (err) {
        console.error("Failed to fetch listings:", err);
    }
}

function renderListings(listings) {
    const container = document.getElementById("active-auctions-list");
    if (listings.length === 0) {
        container.innerHTML = `<div class="empty-state">No active listings. Use the form above to list assets.</div>`;
        return;
    }

    let html = "";
    listings.forEach(auction => {
        const endStr = auction.end_time;
        const cleanEndStr = (endStr.endsWith("Z") || endStr.includes("+")) ? endStr : endStr + "Z";
        const end = new Date(cleanEndStr).toLocaleTimeString();
        html += `
            <div class="ledger-item" style="display:block; padding: 15px; margin-bottom: 10px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <strong>${auction.title}</strong>
                    <span class="role-badge admin">Ends: ${end}</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px;">
                    <span style="opacity:0.7;">Current Price:</span>
                    <strong style="color:var(--neon-green); font-size:1.1rem;">₹${auction.current_price.toLocaleString()}</strong>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
}

// Recent Bid Trades
async function fetchTradeHistory(token) {
    try {
        const response = await fetch(`${BASE_URL}/auctions/bids/recent`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) return;
        const trades = await response.json();
        renderTrades(trades);
    } catch (err) {
        console.error(err);
    }
}

function renderTrades(trades) {
    const stream = document.getElementById("ledger-stream");
    if (trades.length === 0) {
        stream.innerHTML = `<div class="ledger-placeholder">No bid history found.</div>`;
        return;
    }

    let html = "";
    trades.forEach(trade => {
        const time = new Date(trade.timestamp).toLocaleTimeString();
        html += `
            <div class="ledger-item">
                <span class="ledger-time">[${time}]</span>
                <span class="ledger-user">${trade.username}</span> bid 
                <span class="ledger-amount">₹${trade.amount.toLocaleString()}</span> on 
                <span class="ledger-auction">${trade.auction_title}</span>
            </div>
        `;
    });
    stream.innerHTML = html;
}

// Live WebSocket connection
function connectWebSocket(token) {
    if (socket) {
        socket.close();
    }

    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${wsProtocol}//${window.location.host}/ws/auctions?token=${token}`);

    socket.onopen = () => {
        console.log("WebSocket connected successfully.");
    };

    socket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const activeToken = localStorage.getItem("token_bank");

            if (data.type === "access_request") {
                showToast(`New bidding access request from ${data.fullname || data.username}!`, "warning");
                if (activeToken) {
                    fetchRequests(activeToken);
                }
                return;
            }
            
            // Reload listings and trade history if any changes occur
            if (activeToken) {
                fetchActiveListings(activeToken);
                fetchTradeHistory(activeToken);
            }
        } catch (e) {
            console.error("Error processing websocket update:", e);
        }
    };

    socket.onclose = () => {
        console.log("WebSocket closed. Attempting reconnect...");
        setTimeout(() => {
            const activeToken = localStorage.getItem("token_bank");
            if (activeToken) connectWebSocket(activeToken);
        }, 3000);
    };
}

function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    
    let icon = "✓";
    if (type === "error") icon = "✗";
    if (type === "warning") icon = "⚠";

    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(20px) scale(0.9)";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function escapeHTML(str) {
    if (!str) return '';
    return str.replace(/[&<>'"]/g, 
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag)
    );
}

function logout() {
    localStorage.removeItem("token_bank");
    if (socket) socket.close();
    location.reload();
}

async function sendOTP(role) {
    const emailInput = document.getElementById("reg-email");
    const sendBtn = document.getElementById("btn-send-otp");
    const otpContainer = document.getElementById("otp-container");
    const otpInput = document.getElementById("reg-otp");
    const errorEl = document.getElementById("auth-error");

    const email = emailInput.value.trim();
    if (!email) {
        showToast("Please enter a valid email address first.", "error");
        return;
    }

    sendBtn.disabled = true;
    sendBtn.textContent = "Sending...";
    if (errorEl) errorEl.classList.add("hidden");

    try {
        const response = await fetch("/users/request-otp", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ email })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Failed to send OTP verification code.");
        }

        showToast("OTP sent to your email! Please check inbox.", "success");
        
        // Show OTP field
        otpContainer.classList.remove("hidden");
        otpInput.required = true;
        
        // Countdown timer on send button
        let seconds = 30;
        sendBtn.textContent = `Resend in ${seconds}s`;
        const interval = setInterval(() => {
            seconds--;
            if (seconds <= 0) {
                clearInterval(interval);
                sendBtn.disabled = false;
                sendBtn.textContent = "Send OTP";
            } else {
                sendBtn.textContent = `Resend in ${seconds}s`;
            }
        }, 1000);

    } catch (err) {
        showToast(err.message, "error");
        if (errorEl) {
            errorEl.textContent = err.message;
            errorEl.classList.remove("hidden");
        }
        sendBtn.disabled = false;
        sendBtn.textContent = "Send OTP";
    }
}
