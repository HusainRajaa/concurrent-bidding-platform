let token = localStorage.getItem("token") || null;
let currentUser = null;
let ws = null;
let auctions = [];
let countdownInterval = null;
let wsReconnectDelay = 1000;
let activeDetailAuctionId = null;
let selectedBank = null; // Stored as the selected bank object

const API_BASE = window.location.origin;

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    // Check for OAuth redirect token in URL
    const urlParams = new URLSearchParams(window.location.search);
    const urlToken = urlParams.get("token");
    if (urlToken) {
        token = urlToken;
        localStorage.setItem("token", token);
        window.history.replaceState({}, document.title, window.location.pathname);
    }

    if (!token) {
        showAuthView();
        return;
    }

    try {
        await fetchUserProfile();
        if (currentUser.role !== "user") {
            if (currentUser.role === "admin" || currentUser.role === "bank") {
                localStorage.setItem("token_bank", token);
                localStorage.removeItem("token");
                window.location.href = "/bank";
                return;
            }
            logout();
            return;
        }

        // Render user details in navbar
        document.getElementById("user-profile").classList.remove("hidden");
        document.getElementById("nav-username").innerText = currentUser.fullname || currentUser.username;
        document.getElementById("nav-role").innerText = "BIDDER";

        // Show Bank Portal Selection Directory
        loadTenantDirectory();
        
        // Connect WebSocket for real-time notifications
        connectWebSocket();
    } catch (e) {
        console.error("Token expired or invalid", e);
        logout();
    }
}

async function loadTenantDirectory() {
    // Hide auth section, dashboard, and access status section
    document.getElementById("auth-section").classList.add("hidden");
    document.getElementById("user-dashboard").classList.add("hidden");
    document.getElementById("access-status-section").classList.add("hidden");
    document.getElementById("btn-exit-portal").classList.add("hidden");
    document.getElementById("tenant-directory").classList.remove("hidden");
    
    document.getElementById("nav-logo").innerHTML = `Nex<span>Bid</span>`;
    document.title = `NexBid - Portals`;
    
    const grid = document.getElementById("banks-grid");
    try {
        const response = await fetch(`${API_BASE}/banks`);
        if (!response.ok) throw new Error("Could not load banks");
        const banks = await response.json();
        
        if (banks.length === 0) {
            grid.innerHTML = `<div class="empty-state">No private portals active at this time.</div>`;
            return;
        }
        
        grid.innerHTML = banks.map(b => `
            <div class="bank-portal-card" onclick="checkPortalAccess(${JSON.stringify(b).replace(/"/g, '&quot;')})">
                <h3>${escapeHTML(b.fullname || b.username)}</h3>
                <span class="badge" style="background: var(--blue-accent); font-size: 0.75rem;">${escapeHTML(b.branch || 'Branch')}</span>
                <p style="font-size:0.8rem; opacity:0.7; margin-top:8px;">${escapeHTML(b.address || 'Address')}</p>
            </div>
        `).join('');
    } catch (err) {
        grid.innerHTML = `<div class="empty-state">Error loading portals: ${escapeHTML(err.message)}</div>`;
    }
}

async function checkPortalAccess(bankObj) {
    selectedBank = bankObj;
    
    // Show select bank back button
    document.getElementById("btn-exit-portal").classList.remove("hidden");
    document.getElementById("nav-logo").innerHTML = `Nex<span>Bid</span> - ${escapeHTML(bankObj.fullname || bankObj.username)}`;
    document.title = `NexBid - ${escapeHTML(bankObj.fullname || bankObj.username)} Portal`;
    
    try {
        const response = await fetch(`${API_BASE}/banks/${bankObj.id}/access-status`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) throw new Error("Failed to load access status.");
        
        const data = await response.json();
        const status = data.status; // "none", "pending", "allowed", "disallowed"
        
        // Hide directory
        document.getElementById("tenant-directory").classList.add("hidden");
        
        const statusSection = document.getElementById("access-status-section");
        const titleEl = document.getElementById("access-status-title");
        const descEl = document.getElementById("access-status-description");
        const actionContainer = document.getElementById("access-action-container");
        
        statusSection.classList.add("hidden");
        document.getElementById("user-dashboard").classList.add("hidden");
        
        if (status === "allowed") {
            loadDashboard();
        } else if (status === "pending") {
            statusSection.classList.remove("hidden");
            titleEl.innerText = "Access Request Pending";
            descEl.innerText = `Your request to join ${bankObj.fullname || bankObj.username} is currently awaiting bank partner authorization.`;
            actionContainer.innerHTML = `<div class="ws-status" style="justify-content:center;"><span class="status-dot disconnected"></span><span>Waiting for Approval...</span></div>`;
        } else if (status === "disallowed") {
            statusSection.classList.remove("hidden");
            titleEl.innerText = "Access Request Declined";
            descEl.innerText = `Your access request to ${bankObj.fullname || bankObj.username} has been declined. Bidding is restricted.`;
            actionContainer.innerHTML = `<p style="color:var(--neon-red); font-weight:600; text-transform:uppercase;">Access Denied</p>`;
        } else {
            // "none"
            statusSection.classList.remove("hidden");
            titleEl.innerText = "Bidding Access Required";
            descEl.innerText = `You must request access from ${bankObj.fullname || bankObj.username} before you can bid on their listed assets.`;
            actionContainer.innerHTML = `<button class="btn btn-primary" onclick="requestAccess(${bankObj.id})">Request Access</button>`;
        }
    } catch (err) {
        showToast("Error checking access: " + err.message, "error");
    }
}

async function requestAccess(bankId) {
    try {
        const response = await fetch(`${API_BASE}/banks/${bankId}/request-access`, {
            method: "POST",
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) throw new Error("Failed to request access");
        const data = await response.json();
        showToast(data.message, "success");
        checkPortalAccess(selectedBank);
    } catch (err) {
        showToast(err.message, "error");
    }
}

function exitPortal() {
    selectedBank = null;
    loadTenantDirectory();
}

// ----------------- AUTHENTICATION -----------------

function switchAuthTab(tab) {
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");
    const loginTabBtn = document.getElementById("tab-login");
    const regTabBtn = document.getElementById("tab-register");

    if (tab === "login") {
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
        loginTabBtn.classList.add("active");
        regTabBtn.classList.remove("active");
    } else {
        loginForm.classList.add("hidden");
        registerForm.classList.remove("hidden");
        loginTabBtn.classList.remove("active");
        regTabBtn.classList.add("active");
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
            body: JSON.stringify({ email, password })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Authentication failed");
        }

        const data = await response.json();
        const tempToken = data.access_token;
        
        // Fetch profile
        const profileResp = await fetch(`${API_BASE}/users/me`, {
            headers: { "Authorization": `Bearer ${tempToken}` }
        });
        if (!profileResp.ok) throw new Error("Could not retrieve profile");
        
        const tempUser = await profileResp.json();
        if (tempUser.role === "admin" || tempUser.role === "bank") {
            localStorage.setItem("token_bank", tempToken);
            showToast("Redirecting to Bank Console...", "success");
            setTimeout(() => {
                window.location.href = "/bank";
            }, 1000);
            return;
        }

        token = tempToken;
        currentUser = tempUser;
        localStorage.setItem("token", token);
        
        initApp();
        showToast("Authenticated successfully", "success");
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const fullname = document.getElementById("reg-fullname").value;
    const username = document.getElementById("reg-username").value;
    const email = document.getElementById("reg-email").value;
    const mobile_number = document.getElementById("reg-mobile").value;
    const address = document.getElementById("reg-address").value;
    const password = document.getElementById("reg-password").value;
    const errorEl = document.getElementById("auth-error");
    errorEl.classList.add("hidden");

    try {
        const response = await fetch(`${API_BASE}/users/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ fullname, username, email, password, mobile_number, address, role: "user" })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Registration failed");
        }

        // Auto-login
        const loginResponse = await fetch(`${API_BASE}/users/login`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password })
        });
        
        const loginData = await loginResponse.json();
        token = loginData.access_token;
        localStorage.setItem("token", token);

        initApp();
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
    document.getElementById("tenant-directory").classList.add("hidden");
    document.getElementById("user-dashboard").classList.add("hidden");
    document.getElementById("access-status-section").classList.add("hidden");
    document.getElementById("user-profile").classList.add("hidden");
    document.getElementById("btn-exit-portal").classList.add("hidden");
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

// ----------------- USER DASHBOARD & LIVE STREAM -----------------

async function loadDashboard() {
    document.getElementById("tenant-directory").classList.add("hidden");
    document.getElementById("access-status-section").classList.add("hidden");
    document.getElementById("user-dashboard").classList.remove("hidden");
    
    // Set dashboard title
    document.getElementById("dashboard-bank-title").innerText = `${selectedBank.fullname || selectedBank.username} Live Bidding Floor`;

    // Fetch active listings and transaction history
    await fetchAuctions();
    await fetchBidsHistory();
    
    // Ensure we are connected to WebSocket
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWebSocket();
    }

    // Start client-side timer refreshes
    if (countdownInterval) clearInterval(countdownInterval);
    countdownInterval = setInterval(updateAllTimers, 1000);
}

async function fetchAuctions() {
    try {
        const response = await fetch(`${API_BASE}/auctions?bank=${selectedBank.username}`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) {
            throw new Error("Failed to load active liquidation rounds.");
        }
        auctions = await response.json();
        renderUserAuctions();
    } catch (err) {
        showToast(err.message, "error");
    }
}

async function fetchBidsHistory() {
    try {
        const response = await fetch(`${API_BASE}/auctions/bids/recent`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (!response.ok) return;
        const list = await response.json();
        renderRecentBids(list);
    } catch (err) {
        console.error(err);
    }
}

function renderRecentBids(list) {
    const ledger = document.getElementById("ledger-stream");
    if (!ledger) return;
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
                Bidder <span>${escapeHTML(bid.username)}</span> placed bid of <span class="amount">₹${formatMoney(bid.amount)}</span> on Asset <span>"${escapeHTML(bid.auction_title)}"</span>
            </div>
        `;
        ledger.appendChild(ledgerItem);
    });
}

function renderUserAuctions() {
    const grid = document.getElementById("user-auctions-grid");
    if (auctions.length === 0) {
        grid.innerHTML = `<div class="empty-state">No active auctions at this time. Wait for partner bank to list assets.</div>`;
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
                                ₹${formatMoney(auc.current_price)}
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

function updateAllTimers() {
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

    // Detailed Modal timer sync
    if (activeDetailAuctionId) {
        const activeTimer = document.getElementById(`timer-${activeDetailAuctionId}`);
        const detailTimer = document.getElementById("detail-timer");
        const detailInput = document.getElementById("bid-amount-input");
        const detailSubmitBtn = document.querySelector(".bid-panel button");
        
        if (activeTimer && detailTimer) {
            detailTimer.textContent = activeTimer.textContent;
            if (activeTimer.classList.contains("ended")) {
                detailTimer.classList.add("ended");
                if (detailInput) detailInput.setAttribute("disabled", "true");
                if (detailSubmitBtn) detailSubmitBtn.setAttribute("disabled", "true");
            } else {
                detailTimer.classList.remove("ended");
                if (detailInput) detailInput.removeAttribute("disabled");
                if (detailSubmitBtn) detailSubmitBtn.removeAttribute("disabled");
            }
        }
    }
}

// ----------------- WEBSOCKET REAL-TIME SYNC -----------------

function connectWebSocket() {
    if (ws) ws.close();
    
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/auctions?token=${token}`;
    const dot = document.getElementById("ws-dot");
    const statusText = document.getElementById("ws-status-text");

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        if (dot) dot.className = "status-dot connected";
        if (statusText) statusText.textContent = "Live Stream Connected";
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
        if (dot) dot.className = "status-dot disconnected";
        if (statusText) statusText.textContent = "Reconnecting...";
        
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
    if (data.type === "access_approved") {
        showToast(`Access to ${data.bank_name} approved!`, "success");
        if (selectedBank && data.bank_id === selectedBank.id) {
            loadDashboard();
        }
        return;
    }
    if (data.type === "access_declined") {
        showToast(`Access to ${data.bank_name} declined.`, "error");
        if (selectedBank && data.bank_id === selectedBank.id) {
            checkPortalAccess(selectedBank);
        }
        return;
    }

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
        // If not in cache, trigger reload
        fetchAuctions();
        return;
    }

    const priceEl = document.getElementById(`price-${auction_id}`);
    const bidderEl = document.getElementById(`bidder-${auction_id}`);
    const inputEl = document.getElementById(`bid-input-${auction_id}`);

    if (priceEl) {
        priceEl.textContent = `₹${formatMoney(amount)}`;
        priceEl.classList.add("pulse");
        setTimeout(() => priceEl.classList.remove("pulse"), 500);
    }
    
    if (bidderEl) bidderEl.textContent = user_id;
    if (inputEl) inputEl.placeholder = getMinBidPlaceholder(amount);

    // Sync detailed modal view if currently open for this auction
    if (activeDetailAuctionId === auction_id) {
        const detailPriceEl = document.getElementById("detail-current-price");
        const detailBidderEl = document.getElementById("detail-bidder-id");
        const detailInputEl = document.getElementById("bid-amount-input");

        if (detailPriceEl) {
            detailPriceEl.textContent = `₹${formatMoney(amount)}`;
            detailPriceEl.classList.add("pulse");
            setTimeout(() => detailPriceEl.classList.remove("pulse"), 500);
        }
        if (detailBidderEl) detailBidderEl.textContent = user_id;
        if (detailInputEl) detailInputEl.placeholder = getMinBidPlaceholder(amount);

        // Reload the specific audit history table to get correct status & usernames
        loadAuctionBidHistory(auction_id);
    }

    const ledger = document.getElementById("ledger-stream");
    if (!ledger) return;
    const placeholder = ledger.querySelector(".ledger-placeholder");
    if (placeholder) placeholder.remove();

    const time = new Date(timestamp).toLocaleTimeString();
    const ledgerItem = document.createElement("div");
    ledgerItem.className = "ledger-item new-bid";
    ledgerItem.innerHTML = `
        <span class="time">${time}</span>
        <div class="message">
            Bidder <span>#${user_id}</span> placed bid of <span class="amount">₹${amount.toLocaleString()}</span> on Asset <span>"${escapeHTML(auction.title)}"</span>
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
        const detailInput = document.getElementById("bid-amount-input");
        const detailSubmitBtn = document.querySelector(".bid-panel button");
        if (detailTimer) {
            detailTimer.textContent = "Ended";
            detailTimer.classList.add("ended");
        }
        if (detailInput) detailInput.setAttribute("disabled", "true");
        if (detailSubmitBtn) detailSubmitBtn.setAttribute("disabled", "true");
        
        loadAuctionBidHistory(auction_id);
    }
    
    // Append announcement log to activity ledger
    const ledger = document.getElementById("ledger-stream");
    if (!ledger) return;
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
                SOLD: Asset <span>"${escapeHTML(auction_title)}"</span> won by Bidder <span>"${escapeHTML(username)}"</span> (ID: #${highest_bidder_id}) for <span class="amount">₹${formatMoney(price)}</span>!
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
    document.getElementById("bid-amount-input").value = "";

    // Set text elements
    document.getElementById("detail-title").textContent = auction.title;
    document.getElementById("detail-desc").textContent = auction.description || 'No description provided';
    document.getElementById("detail-start-price").textContent = `₹${formatMoney(auction.start_price)}`;
    document.getElementById("detail-current-price").textContent = `₹${formatMoney(auction.current_price)}`;
    document.getElementById("detail-bidder-id").textContent = auction.highest_bidder_id || 'None';

    // Set input placeholder
    document.getElementById("bid-amount-input").placeholder = getMinBidPlaceholder(auction.current_price);

    // Initial check for timer
    const endTimeStr = auction.end_time;
    const cleanEndTimeStr = (endTimeStr.endsWith("Z") || endTimeStr.includes("+")) ? endTimeStr : endTimeStr + "Z";
    const isEnded = new Date(cleanEndTimeStr) <= new Date();

    const timerValEl = document.getElementById("detail-timer");
    const inputEl = document.getElementById("bid-amount-input");
    const submitBtn = document.querySelector(".bid-panel button");

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
            if (activeDetailAuctionId === auctionId) {
                renderAuctionBidHistory(list);
            }
        }
    } catch (e) {
        console.error("Error loading auction bid history", e);
    }
}

function renderAuctionBidHistory(list) {
    const tbody = document.getElementById("detail-bids-list");
    if (list.length === 0) {
        tbody.innerHTML = `<div class="ledger-placeholder">No bids recorded yet. Be the first to place a bid!</div>`;
        return;
    }

    tbody.innerHTML = list.map(bid => {
        const timestampStr = bid.timestamp;
        const cleanTimestampStr = (timestampStr.endsWith("Z") || timestampStr.includes("+")) ? timestampStr : timestampStr + "Z";
        const time = new Date(cleanTimestampStr).toLocaleTimeString();
        
        let badgeStyle = "color: var(--text-muted);";
        if (bid.status === "success") badgeStyle = "color: var(--neon-green); font-weight:600;";
        if (bid.status === "failed") badgeStyle = "color: var(--neon-red);";

        return `
            <div class="ledger-item" style="display:flex; justify-content:space-between; align-items:center; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.05);">
                <div style="display:flex; flex-direction:column;">
                    <strong>${escapeHTML(bid.username || `#${bid.user_id}`)}</strong>
                    <span style="font-size:0.75rem; opacity:0.5;">${time}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-weight:600; font-size:1.05rem;">₹${formatMoney(bid.amount)}</div>
                    <span style="font-size:0.75rem; ${badgeStyle}">${bid.status.toUpperCase()}</span>
                </div>
            </div>
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

async function handlePlaceDetailBid(e) {
    e.preventDefault();
    if (!activeDetailAuctionId) return;

    const input = document.getElementById("bid-amount-input");
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
        return `Min: ₹${minBid.toExponential(4)}`;
    }
    return `Min: ₹${minBid.toFixed(2)}`;
}
