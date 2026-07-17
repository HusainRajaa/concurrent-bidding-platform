let token = localStorage.getItem("token_admin") || null;
let currentUser = null;
let countdownInterval = null;

const API_BASE = window.location.origin;

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    if (token) {
        try {
            await fetchUserProfile();
            if (currentUser.role !== "admin" && currentUser.role !== "bank") {
                showToast("Access denied: Regular users must log in via index.html", "error");
                logout();
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

// ----------------- AUTHENTICATION -----------------

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
        
        // Fetch profile to verify role before saving token
        const profileResp = await fetch(`${API_BASE}/users/me`, {
            headers: { "Authorization": `Bearer ${tempToken}` }
        });
        if (!profileResp.ok) throw new Error("Could not retrieve profile");
        
        const tempUser = await profileResp.json();
        if (tempUser.role !== "admin" && tempUser.role !== "bank") {
            throw new Error("Unauthorized: Bidders must log in via the Bidding Portal.");
        }

        token = tempToken;
        currentUser = tempUser;
        localStorage.setItem("token_admin", token);
        
        loadDashboard();
        showToast("Authenticated Console successfully", "success");
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
    document.getElementById("admin-dashboard").classList.add("hidden");
    document.getElementById("user-profile").classList.add("hidden");
}

function logout() {
    token = null;
    currentUser = null;
    localStorage.removeItem("token_admin");
    if (countdownInterval) clearInterval(countdownInterval);
    showAuthView();
    showToast("Console locked", "warning");
}

// ----------------- DASHBOARD MANAGEMENT -----------------

function loadDashboard() {
    document.getElementById("auth-section").classList.add("hidden");
    
    // Update navbar profile
    document.getElementById("nav-role").textContent = currentUser.role;
    document.getElementById("nav-role").className = `role-badge ${currentUser.role}`;
    document.getElementById("nav-username").textContent = currentUser.username;
    document.getElementById("user-profile").classList.remove("hidden");

    document.getElementById("admin-dashboard").classList.remove("hidden");
    loadAdminAuctions();
}

// ----------------- ADMIN DASHBOARD -----------------

async function loadAdminAuctions() {
    try {
        const response = await fetch(`${API_BASE}/auctions`, {
            headers: { "Authorization": `Bearer ${token}` }
        });
        if (response.ok) {
            const list = await response.json();
            renderAdminAuctions(list);
            startCountdownTimers();
        }
    } catch (e) {
        console.error("Error loading admin auctions", e);
    }
}

function renderAdminAuctions(list) {
    const container = document.getElementById("admin-auctions-list");
    if (list.length === 0) {
        container.innerHTML = `<div class="empty-state">No active auctions listed. Use the form to list one!</div>`;
        return;
    }
    
    container.innerHTML = list.map(auc => {
        const endStr = auc.end_time;
        const cleanEndStr = (endStr.endsWith("Z") || endStr.includes("+")) ? endStr : endStr + "Z";
        const isEnded = new Date(cleanEndStr) <= new Date();
        return `
            <div class="admin-auc-row" id="auc-row-${auc.id}">
                <div>
                    <h4>${escapeHTML(auc.title)}</h4>
                    <div style="display: flex; align-items: center; gap: 8px; margin-top: 4px;">
                        <span class="auc-timer ${isEnded ? 'ended' : ''}" id="timer-${auc.id}" data-endtime="${auc.end_time}">
                            ${isEnded ? 'Ended' : 'Calculating...'}
                        </span>
                        <span class="role-badge ${auc.bank_id ? 'bank' : 'admin'}" style="font-size: 0.65rem; padding: 2px 8px; text-transform: none; font-weight: 500;">
                            Listed by: ${escapeHTML(auc.bank_username || 'System')}
                        </span>
                    </div>
                </div>
                <div class="admin-auc-meta">
                    <div class="price">₹${formatMoney(auc.current_price)}</div>
                    <p>Version ID: ${auc.version_id}</p>
                </div>
            </div>
        `;
    }).join('');
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
            
            if (diff <= 0) {
                timer.textContent = "Ended";
                timer.classList.add("ended");
            } else {
                const hrs = Math.floor(diff / 3600000);
                const mins = Math.floor((diff % 3600000) / 60000);
                const secs = Math.floor((diff % 60000) / 1000);
                
                timer.textContent = `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
            }
        });
    }, 1000);
}

async function handleCreateAuction(e) {
    e.preventDefault();
    const title = document.getElementById("auc-title").value;
    const description = document.getElementById("auc-desc").value;
    const start_price = parseFloat(document.getElementById("auc-price").value);
    const duration_minutes = parseInt(document.getElementById("auc-duration").value);

    try {
        const response = await fetch(`${API_BASE}/auctions`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${token}`
            },
            body: JSON.stringify({ title, description, start_price, duration_minutes })
        });

        if (!response.ok) {
            const data = await response.json();
            throw new Error(data.detail || "Failed to create auction");
        }

        showToast("Auction listed successfully!", "success");
        e.target.reset();
        loadAdminAuctions();
    } catch (err) {
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

// ----------------- HELPERS -----------------

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
