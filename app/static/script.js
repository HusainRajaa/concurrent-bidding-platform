let token = localStorage.getItem("token") || null;
let currentUser = null;
let ws = null;
let auctions = [];
let countdownInterval = null;
let wsReconnectDelay = 1000;
let activeDetailAuctionId = null;


const API_BASE = window.location.origin;

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

let selectedBank = null;

async function initApp() {
    // Check for OAuth redirect token in URL
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get("token");
    if (urlToken) {
        token = urlToken;
        localStorage.setItem("token", token);
        // Clean URL state
        window.history.replaceState({}, document.title, window.location.pathname);
    }

    selectedBank = urlParams.get("bank") || null;

    if (selectedBank) {
        try {
            const response = await fetch(`${API_BASE}/banks`);
            if (!response.ok) throw new Error("Failed to verify bank portal.");
            const banks = await response.json();
            const bankExists = banks.some(b => b.username === selectedBank);
            
            if (!bankExists) {
                showToast(`Private portal '${selectedBank}' does not exist.`, "error");
                selectedBank = null;
                window.history.replaceState({}, document.title, window.location.pathname);
                loadTenantDirectory(banks);
                return;
            }
        } catch (err) {
            console.error(err);
            showToast("Failed to verify bank portal.", "error");
            loadTenantDirectory();
            return;
        }
    }

    if (!selectedBank) {
        loadTenantDirectory();
        return;
    }

    // Set portal branding details
    document.getElementById("btn-exit-portal").classList.remove("hidden");
    document.getElementById("nav-logo").innerHTML = `Nex<span>Bid</span> - ${escapeHTML(selectedBank)}`;
    document.title = `NexBid - ${escapeHTML(selectedBank)} Portal`;

    // Only give option for the buyer, not for the seller when a bank portal is selected
    const regRoleSelect = document.getElementById("reg-role");
    if (regRoleSelect) {
        const bankOption = regRoleSelect.querySelector('option[value="bank"]');
        if (bankOption) {
            bankOption.remove();
        }
        regRoleSelect.value = "user";
    }

    if (token) {
        try {
            await fetchUserProfile();
            if (currentUser.role !== "user") {
                if (currentUser.role === "admin" || currentUser.role === "bank") {
                    localStorage.setItem("token_admin", token);
                    localStorage.removeItem("token");
                    window.location.href = "/admin.html";
                    return;
                }
                showToast("Console accounts must log in via the Console Portal", "error");
                logout();
                return;
            }
            
            // Enforce tenant match
            if (currentUser.tenant_username !== selectedBank) {
                showToast(`This session belongs to the ${currentUser.tenant_username} portal. Redirecting...`, "warning");
                setTimeout(() => {
                    window.location.href = `/?bank=${currentUser.tenant_username}`;
                }, 1500);
                return;
            }
            
            loadDashboard();
        } catch (e) {
            console.error("Token expired or invalid", e);
            logout();
        }
    } else {
        showAuthView();
    }
}

async function loadTenantDirectory(banksList = null) {
    document.getElementById("tenant-directory").classList.remove("hidden");
    document.getElementById("auth-section").classList.add("hidden");
    document.getElementById("user-dashboard").classList.add("hidden");
    document.getElementById("btn-exit-portal").classList.add("hidden");
    document.getElementById("nav-logo").innerHTML = `Nex<span>Bid</span>`;
    document.title = `NexBid - Portals`;
    
    const grid = document.getElementById("banks-grid");
    try {
        const banks = banksList || await (async () => {
            const response = await fetch(`${API_BASE}/banks`);
            if (!response.ok) throw new Error("Could not load banks");
            return await response.json();
        })();
        
        if (banks.length === 0) {
            grid.innerHTML = `<div class="empty-state">No private portals active at this time.</div>`;
            return;
        }
        grid.innerHTML = banks.map(b => `
            <div class="bank-portal-card" onclick="enterPortal('${escapeHTML(b.username)}')">
                <h3>${escapeHTML(b.username)}</h3>
                <span class="badge">Private Portal</span>
            </div>
        `).join('');
    } catch (err) {
        grid.innerHTML = `<div class="empty-state">Error loading portals: ${escapeHTML(err.message)}</div>`;
    }
}

function enterPortal(bankName) {
    window.location.href = `/?bank=${bankName}`;
}

function exitPortal() {
    window.location.href = "/";
}

// ----------------- AUTHENTICATION -----------------

function switchAuthTab(tab) {
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");
    const tabs = document.querySelectorAll(".tab-btn");

    if (tab === "login") {
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
        tabs[0].classList.add("active");
        tabs[1].classList.remove("active");
    } else {
        loginForm.classList.add("hidden");
        registerForm.classList.remove("hidden");
        tabs[0].classList.remove("active");
        tabs[1].classList.add("active");
    }
    document.getElementById("auth-error").classList.add("hidden");
}

function fillCredentials(email, password) {
    document.getElementById("login-email").value = email;
    document.getElementById("login-password").value = password;
}

async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    const errorEl = document.getElementById("auth-error");
    errorEl.classList.add("hidden");

    try {
        const response = await fetch(`${API_BASE}/users/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password, tenant_username: selectedBank })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Authentication failed");
        }

        const data = await response.json();
        const tempToken = data.access_token;
        
        // Fetch profile to verify role before saving token
        const profileResp = await fetch(`${API_BASE}/users/me`, {
            headers: { "Authorization": `Bearer ${tempToken}` }
        });
        if (!profileResp.ok) throw new Error("Could not retrieve profile");
        
        const tempUser = await profileResp.json();
        if (tempUser.role === "admin" || tempUser.role === "bank") {
            localStorage.setItem("token_admin", tempToken);
            showToast("Redirecting to Listing Console...", "success");
            setTimeout(() => {
                window.location.href = "/admin.html";
            }, 1000);
            return;
        }

        token = tempToken;
        currentUser = tempUser;
        localStorage.setItem("token", token);
        
        loadDashboard();
        showToast("Authenticated successfully", "success");
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}

async function requestOTP(e) {
    e.preventDefault();
    const emailInput = document.getElementById("reg-email");
    const errorEl = document.getElementById("auth-error");
    const btnSend = document.getElementById("btn-send-otp");
    
    const email = emailInput.value.trim();
    if (!email) {
        showToast("Please enter an email address first.", "error");
        return;
    }
    
    errorEl.classList.add("hidden");
    btnSend.setAttribute("disabled", "true");
    btnSend.textContent = "Sending...";
    
    try {
        const response = await fetch(`${API_BASE}/users/request-otp`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email })
        });
        
        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to request OTP");
        }
        
        showToast("Verification code sent to your email", "success");
        
        // Cooldown timer for button (30 seconds)
        let cooldown = 30;
        btnSend.textContent = `Retry in ${cooldown}s`;
        const timer = setInterval(() => {
            cooldown--;
            if (cooldown <= 0) {
                clearInterval(timer);
                btnSend.removeAttribute("disabled");
                btnSend.textContent = "Send OTP";
            } else {
                btnSend.textContent = `Retry in ${cooldown}s`;
            }
        }, 1000);
        
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
        showToast(err.message, "error");
        btnSend.removeAttribute("disabled");
        btnSend.textContent = "Send OTP";
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const username = document.getElementById("reg-username").value;
    const email = document.getElementById("reg-email").value;
    const mobile_number = document.getElementById("reg-mobile").value;
    const password = document.getElementById("reg-password").value;
    const errorEl = document.getElementById("auth-error");
    errorEl.classList.add("hidden");

    const role = document.getElementById("reg-role").value;

    try {
        const response = await fetch(`${API_BASE}/users/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, email, password, mobile_number, role, tenant_username: selectedBank })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Registration failed");
        }

        // Auto-login
        const loginResponse = await fetch(`${API_BASE}/users/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password, tenant_username: selectedBank })
        });
        
        const loginData = await loginResponse.json();
        token = loginData.access_token;
        localStorage.setItem("token", token);

        await fetchUserProfile();
        if (currentUser.role === "admin" || currentUser.role === "bank") {
            localStorage.setItem("token_admin", token);
            localStorage.removeItem("token");
            showToast("Account created. Redirecting to Console...", "success");
            setTimeout(() => {
                window.location.href = "/admin.html";
            }, 1000);
            return;
        }
        loadDashboard();
        showToast("Account created successfully", "success");
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}

async function fetchUserProfile() {
    const response = await fetch(`${API_BASE}/users/me`, {
        headers: { "Authorization": `Bearer ${token}` }
    });
    if (!response.ok) throw new Error("Unauthorized");
    currentUser = await response.json();
}

function showAuthView() {
    document.getElementById("auth-section").classList.remove("hidden");
    document.getElementById("user-dashboard").classList.add("hidden");
    document.getElementById("user-profile").classList.add("hidden");
}

function logout() {
    token = null;
    currentUser = null;
    localStorage.removeItem("token");
    if (ws) ws.close();
    if (countdownInterval) clearInterval(countdownInterval);
    showAuthView();
    showToast("Signed out", "warning");
}

// ----------------- DASHBOARD MANAGEMENT -----------------

function loadDashboard() {
    document.getElementById("auth-section").classList.add("hidden");
    
    // Update navbar profile
    document.getElementById("nav-role").textContent = currentUser.role;
    document.getElementById("nav-role").className = `role-badge ${currentUser.role}`;
    document.getElementById("nav-username").textContent = currentUser.username;
    document.getElementById("user-profile").classList.remove("hidden");

    // Display appropriate dashboard
    document.getElementById("user-dashboard").classList.remove("hidden");
    loadUserAuctions();
    loadRecentBids();
    connectWebSocket();
}

// ----------------- USER DASHBOARD & BIDS -----------------

async function loadUserAuctions() {
    try {
        const response = await fetch(`${API_BASE}/auctions?bank=${selectedBank}`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (response.ok) {
            auctions = await response.json();
            renderUserAuctions();
            startCountdownTimers();
        }
    } catch (e) {
        console.error("Error loading user auctions", e);
    }
}

async function loadRecentBids() {
    try {
        const response = await fetch(`${API_BASE}/auctions/bids/recent`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (response.ok) {
            const list = await response.json();
            renderRecentBids(list);
        }
    } catch (e) {
        console.error("Error loading recent bids", e);
    }
}

function renderRecentBids(list) {
    const ledger = document.getElementById("ledger-stream");
    if (list.length === 0) {
        ledger.innerHTML = `<div class="ledger-placeholder">Ledger is active. Live trades will stream here...</div>`;
        return;
    }
    
    ledger.innerHTML = "";
    
    list.forEach(bid => {
        const timestampStr = bid.timestamp;
        const cleanTimestampStr = (timestampStr.endsWith("Z") || timestampStr.includes("+")) ? timestampStr : timestampStr + "Z";
        const time = new Date(cleanTimestampStr).toLocaleTimeString();
        
        const ledgerItem = document.createElement("div");
        ledgerItem.className = "ledger-item";
        ledgerItem.innerHTML = `
            <span class="time">${time}</span>
            <div class="message">
                Bidder <span>${escapeHTML(bid.username)}</span> placed bid of <span class="amount">$${formatMoney(bid.amount)}</span> on Asset <span>"${escapeHTML(bid.auction_title)}"</span>
            </div>
        `;
        ledger.appendChild(ledgerItem);
    });
}

function renderUserAuctions() {
    const grid = document.getElementById("user-auctions-grid");
    if (auctions.length === 0) {
        grid.innerHTML = `<div class="empty-state">No active auctions at this time. Wait for administrator to list assets.</div>`;
        return;
    }

    grid.innerHTML = auctions.map(auc => {
        const endTimeStr = auc.end_time;
        const cleanEndTimeStr = (endTimeStr.endsWith("Z") || endTimeStr.includes("+")) ? endTimeStr : endTimeStr + "Z";
        const isEnded = new Date(cleanEndTimeStr) <= new Date();
        return `
            <article class="glass-card auction-card" id="auc-card-${auc.id}" onclick="openAuctionDetail(${auc.id})">
                <div>
                    <div class="auc-header">
                        <h4>${escapeHTML(auc.title)}</h4>
                        <span class="auc-timer ${isEnded ? 'ended' : ''}" id="timer-${auc.id}" data-endtime="${auc.end_time}">
                            ${isEnded ? 'Ended' : 'Calculating...'}
                        </span>
                    </div>
                    <p class="auc-desc">${escapeHTML(auc.description || 'No description provided')}</p>
                    <div class="auc-bank-label" style="font-size: 0.8rem; color: var(--text-muted); margin-top: 6px; display: flex; align-items: center; gap: 4px;">
                        <span>Listed by:</span> <span class="role-badge bank" style="font-size: 0.65rem; padding: 2px 8px; text-transform: none; font-weight: 500;">${escapeHTML(auc.bank_username || 'System')}</span>
                    </div>
                </div>
                <div>
                    <div class="auc-pricing">
                        <div>
                            <span class="label">Current Bid</span>
                            <div class="auc-price-val" id="price-${auc.id}">
                                $${formatMoney(auc.current_price)}
                            </div>
                            <div class="highest-bidder-label">
                                Bidder ID: <span id="bidder-${auc.id}">${auc.highest_bidder_id || 'None'}</span>
                            </div>
                        </div>
                    </div>
                    <div class="auc-bid-form" onclick="event.stopPropagation()">
                        <form onsubmit="handlePlaceBid(event, ${auc.id})" ${isEnded ? 'disabled' : ''}>
                            <div class="bid-input-group">
                                <input type="number" step="0.01" id="bid-input-${auc.id}" required placeholder="${getMinBidPlaceholder(auc.current_price)}" ${isEnded ? 'disabled' : ''}>
                                <button type="submit" class="btn btn-primary btn-sm" ${isEnded ? 'disabled' : ''}>Place Bid</button>
                            </div>
                            <div class="auc-card-error" id="error-msg-${auc.id}"></div>
                        </form>
                    </div>
                </div>
            </article>
        `;
    }).join('');
}

async function handlePlaceBid(e, auctionId) {
    e.preventDefault();
    const input = document.getElementById(`bid-input-${auctionId}`);
    const errorEl = document.getElementById(`error-msg-${auctionId}`);
    const amount = parseFloat(input.value);
    errorEl.textContent = "";

    try {
        const response = await fetch(`${API_BASE}/auctions/${auctionId}/bid`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ amount })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to place bid");
        }

        showToast("Bid registered. Synchronizing...", "success");
        input.value = "";
    } catch (err) {
        errorEl.textContent = err.message;
        showToast(err.message, "error");
    }
}

function startCountdownTimers() {
    if (countdownInterval) clearInterval(countdownInterval);
    
    countdownInterval = setInterval(() => {
        const timers = document.querySelectorAll("[id^='timer-']");
        timers.forEach(timer => {
            const endTimeStr = timer.getAttribute("data-endtime");
            const cleanEndTimeStr = (endTimeStr.endsWith("Z") || endTimeStr.includes("+")) ? endTimeStr : endTimeStr + "Z";
            const endTime = new Date(cleanEndTimeStr);
            const now = new Date();
            const diff = endTime - now;
            
            const auctionId = timer.id.split('-')[1];
            
            if (diff <= 0) {
                timer.textContent = "Ended";
                timer.classList.add("ended");
                const card = document.getElementById(`auc-card-${auctionId}`);
                if (card) {
                    const inputs = card.querySelectorAll("input, button");
                    inputs.forEach(el => el.setAttribute("disabled", "true"));
                }
            } else {
                const hrs = Math.floor(diff / 3600000);
                const mins = Math.floor((diff % 3600000) / 60000);
                const secs = Math.floor((diff % 60000) / 1000);
                
                timer.textContent = `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
            }
        });

        // Also sync the timer in the detailed modal console
        if (activeDetailAuctionId) {
            const activeTimer = document.getElementById(`timer-${activeDetailAuctionId}`);
            const detailTimer = document.getElementById("detail-timer");
            const detailInput = document.getElementById("detail-bid-input");
            const detailSubmitBtn = document.querySelector("#detail-bid-form button");
            
            if (activeTimer && detailTimer) {
                detailTimer.textContent = activeTimer.textContent;
                if (activeTimer.classList.contains("ended")) {
                    detailTimer.classList.add("ended");
                    if (detailInput) detailInput.setAttribute("disabled", "true");
                    if (detailSubmitBtn) detailSubmitBtn.setAttribute("disabled", "true");
                } else {
                    detailTimer.classList.remove("ended");
                }
            }
        }
    }, 1000);
}

// ----------------- WEBSOCKET REAL-TIME SYNC -----------------

function connectWebSocket() {
    const wsUrl = `ws://${window.location.host}/ws/auctions?token=${token}`;
    const dot = document.getElementById("ws-dot");
    const statusText = document.getElementById("ws-status-text");

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        dot.className = "status-dot connected";
        statusText.textContent = "Live Stream Connected";
        wsReconnectDelay = 1000;
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleIncomingWSBid(data);
        } catch (e) {
            console.error("Error parsing WS message", e);
        }
    };

    ws.onclose = () => {
        dot.className = "status-dot disconnected";
        statusText.textContent = "Reconnecting...";
        
        setTimeout(() => {
            if (token && currentUser && currentUser.role === "user") {
                connectWebSocket();
                wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
            }
        }, wsReconnectDelay);
    };

    ws.onerror = (err) => {
        console.error("WebSocket error", err);
        ws.close();
    };
}

function handleIncomingWSBid(data) {
    if (data.type === "auction_ended") {
        handleAuctionEndedWS(data);
        return;
    }

    const { auction_id, user_id, amount, timestamp } = data;
    
    const auction = auctions.find(a => a.id === auction_id);
    if (auction) {
        auction.current_price = amount;
        auction.highest_bidder_id = user_id;
    } else {
        loadUserAuctions();
        return;
    }

    const priceEl = document.getElementById(`price-${auction_id}`);
    const bidderEl = document.getElementById(`bidder-${auction_id}`);
    const inputEl = document.getElementById(`bid-input-${auction_id}`);

    if (priceEl) {
        priceEl.textContent = `$${formatMoney(amount)}`;
        priceEl.classList.add("pulse");
        setTimeout(() => priceEl.classList.remove("pulse"), 500);
    }
    
    if (bidderEl) bidderEl.textContent = user_id;
    if (inputEl) inputEl.placeholder = getMinBidPlaceholder(amount);

    // Sync detailed modal view if currently open for this auction
    if (activeDetailAuctionId === auction_id) {
        const detailPriceEl = document.getElementById("detail-current-price");
        const detailBidderEl = document.getElementById("detail-bidder-id");
        const detailInputEl = document.getElementById("detail-bid-input");

        if (detailPriceEl) {
            detailPriceEl.textContent = `$${formatMoney(amount)}`;
            detailPriceEl.classList.add("pulse");
            setTimeout(() => detailPriceEl.classList.remove("pulse"), 500);
        }
        if (detailBidderEl) detailBidderEl.textContent = user_id;
        if (detailInputEl) detailInputEl.placeholder = getMinBidPlaceholder(amount);

        // Reload the specific audit history table to get correct status & usernames
        loadAuctionBidHistory(auction_id);
    }

    const ledger = document.getElementById("ledger-stream");
    const placeholder = ledger.querySelector(".ledger-placeholder");
    if (placeholder) placeholder.remove();

    const time = new Date(timestamp).toLocaleTimeString();
    const ledgerItem = document.createElement("div");
    ledgerItem.className = "ledger-item new-bid";
    ledgerItem.innerHTML = `
        <span class="time">${time}</span>
        <div class="message">
            Bidder <span>#${user_id}</span> placed bid of <span class="amount">$${amount.toLocaleString()}</span> on Asset <span>"${escapeHTML(auction.title)}"</span>
        </div>
    `;

    ledger.insertBefore(ledgerItem, ledger.firstChild);
    if (ledger.children.length > 50) ledger.lastChild.remove();
}

function handleAuctionEndedWS(data) {
    const { auction_id, auction_title, highest_bidder_id, username, price } = data;
    
    // Update local auctions state if present
    const auction = auctions.find(a => a.id === auction_id);
    if (auction) {
        auction.is_ended = true;
    }
    
    // Update timer text on main dashboard card immediately
    const timerEl = document.getElementById(`timer-${auction_id}`);
    if (timerEl) {
        timerEl.textContent = "Ended";
        timerEl.classList.add("ended");
    }
    
    // Disable inputs/buttons on main card
    const card = document.getElementById(`auc-card-${auction_id}`);
    if (card) {
        const inputs = card.querySelectorAll("input, button");
        inputs.forEach(el => el.setAttribute("disabled", "true"));
    }
    
    // Update detailed modal if open for this ended auction
    if (activeDetailAuctionId === auction_id) {
        const detailTimer = document.getElementById("detail-timer");
        const detailInput = document.getElementById("detail-bid-input");
        const detailSubmitBtn = document.querySelector("#detail-bid-form button");
        if (detailTimer) {
            detailTimer.textContent = "Ended";
            detailTimer.classList.add("ended");
        }
        if (detailInput) detailInput.setAttribute("disabled", "true");
        if (detailSubmitBtn) detailSubmitBtn.setAttribute("disabled", "true");
        
        // Reload audit trail to update latest bid statuses to success/failed
        loadAuctionBidHistory(auction_id);
    }
    
    // Append announcement log to activity ledger
    const ledger = document.getElementById("ledger-stream");
    const placeholder = ledger.querySelector(".ledger-placeholder");
    if (placeholder) placeholder.remove();
    
    const time = new Date().toLocaleTimeString();
    const ledgerItem = document.createElement("div");
    ledgerItem.className = "ledger-item new-bid";
    ledgerItem.style.borderLeft = "3px solid var(--danger)";
    ledgerItem.style.background = "var(--danger-bg)";
    
    if (highest_bidder_id) {
        ledgerItem.innerHTML = `
            <span class="time">${time}</span>
            <div class="message" style="font-weight: 600; color: #991b1b;">
                SOLD: Asset <span>"${escapeHTML(auction_title)}"</span> won by Bidder <span>"${escapeHTML(username)}"</span> (ID: #${highest_bidder_id}) for <span class="amount">$${formatMoney(price)}</span>!
            </div>
        `;
    } else {
        ledgerItem.innerHTML = `
            <span class="time">${time}</span>
            <div class="message" style="font-style: italic; color: var(--text-muted);">
                ENDED: Asset <span>"${escapeHTML(auction_title)}"</span> ended with no bids received.
            </div>
        `;
    }
    
    ledger.insertBefore(ledgerItem, ledger.firstChild);
    if (ledger.children.length > 50) ledger.lastChild.remove();
}

// ----------------- DETAILED AUCTION CONSOLE VIEW -----------------

async function openAuctionDetail(auctionId) {
    activeDetailAuctionId = auctionId;
    const auction = auctions.find(a => a.id === auctionId);
    if (!auction) return;

    // Reset error message & input
    document.getElementById("detail-bid-error").textContent = "";
    document.getElementById("detail-bid-input").value = "";

    // Set text elements
    document.getElementById("detail-title").textContent = auction.title;
    document.getElementById("detail-desc").textContent = auction.description || 'No description provided';
    document.getElementById("detail-start-price").textContent = `$${formatMoney(auction.start_price)}`;
    document.getElementById("detail-current-price").textContent = `$${formatMoney(auction.current_price)}`;
    document.getElementById("detail-bidder-id").textContent = auction.highest_bidder_id || 'None';

    // Set input placeholder
    document.getElementById("detail-bid-input").placeholder = getMinBidPlaceholder(auction.current_price);

    // Initial check for timer
    const endTimeStr = auction.end_time;
    const cleanEndTimeStr = (endTimeStr.endsWith("Z") || endTimeStr.includes("+")) ? endTimeStr : endTimeStr + "Z";
    const isEnded = new Date(cleanEndTimeStr) <= new Date();

    const timerValEl = document.getElementById("detail-timer");
    const inputEl = document.getElementById("detail-bid-input");
    const submitBtn = document.querySelector("#detail-bid-form button");

    if (isEnded) {
        timerValEl.textContent = "Ended";
        timerValEl.classList.add("ended");
        inputEl.setAttribute("disabled", "true");
        submitBtn.setAttribute("disabled", "true");
    } else {
        timerValEl.textContent = "Calculating...";
        timerValEl.classList.remove("ended");
        inputEl.removeAttribute("disabled");
        submitBtn.removeAttribute("disabled");
    }

    // Show modal
    document.getElementById("detail-modal").classList.remove("hidden");

    // Load history
    await loadAuctionBidHistory(auctionId);
}

function closeAuctionDetail() {
    activeDetailAuctionId = null;
    document.getElementById("detail-modal").classList.add("hidden");
}

async function loadAuctionBidHistory(auctionId) {
    try {
        const response = await fetch(`${API_BASE}/auctions/${auctionId}/bids`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (response.ok) {
            const list = await response.json();
            // Verify if the active modal hasn't changed while request was in-flight
            if (activeDetailAuctionId === auctionId) {
                renderAuctionBidHistory(list);
            }
        }
    } catch (e) {
        console.error("Error loading auction bid history", e);
    }
}

function renderAuctionBidHistory(list) {
    const tbody = document.getElementById("detail-history-rows");
    if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-dim); font-style: italic;">No bids recorded yet. Be the first to place a bid!</td></tr>`;
        return;
    }

    tbody.innerHTML = list.map(bid => {
        const timestampStr = bid.timestamp;
        const cleanTimestampStr = (timestampStr.endsWith("Z") || timestampStr.includes("+")) ? timestampStr : timestampStr + "Z";
        const time = new Date(cleanTimestampStr).toLocaleTimeString();
        
        let badgeClass = "badge-status status-pending";
        if (bid.status === "success") badgeClass = "badge-status status-success";
        if (bid.status === "failed") badgeClass = "badge-status status-failed";

        return `
            <tr>
                <td>${time}</td>
                <td>${escapeHTML(bid.username || `#${bid.user_id}`)}</td>
                <td>$${formatMoney(bid.amount)}</td>
                <td><span class="${badgeClass}">${bid.status}</span></td>
            </tr>
        `;
    }).join('');
}

async function handlePlaceDetailBid(e) {
    e.preventDefault();
    if (!activeDetailAuctionId) return;

    const input = document.getElementById("detail-bid-input");
    const errorEl = document.getElementById("detail-bid-error");
    const amount = parseFloat(input.value);
    errorEl.textContent = "";

    try {
        const response = await fetch(`${API_BASE}/auctions/${activeDetailAuctionId}/bid`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ amount })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to place bid");
        }

        showToast("Bid registered. Synchronizing...", "success");
        input.value = "";
    } catch (err) {
        errorEl.textContent = err.message;
        showToast(err.message, "error");
    }
}

// ----------------- TOAST SYSTEM -----------------

function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
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

function formatMoney(amount) {
    if (amount >= 1e15) {
        return amount.toExponential(4);
    }
    return amount.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function getMinBidPlaceholder(amount) {
    const minBid = amount + 0.01;
    if (minBid >= 1e15) {
        return `Min: $${minBid.toExponential(4)}`;
    }
    return `Min: $${minBid.toFixed(2)}`;
}
