# NexBid Deployment & Hosting Guide

This guide details three modern methods for hosting the containerized **NexBid** platform (FastAPI, Redis, PostgreSQL, WebSockets) on public cloud infrastructure.

---

## Method 1: Self-Hosted VPS (DigitalOcean, AWS EC2, Linode) - RECOMMENDED

Using a virtual private server (VPS) is the most robust and cost-effective method to run NexBid under high transaction loads, as it lets Docker Compose manage internal networks with maximum isolation.

### Step 1: Install Docker & Docker Compose on the VPS
Connect to your VPS via SSH and install Docker:
```bash
# Update package index
sudo apt-get update

# Install Docker
sudo apt-get install -y docker.io docker-compose

# Start and enable Docker daemon
sudo systemctl start docker
sudo systemctl enable docker
```

### Step 2: Clone Repository & Create Environment Configuration
```bash
# Clone the repository
git clone <your-repo-url> /opt/nexbid
cd /opt/nexbid

# Create a production .env file
cp .env.template .env
```
Open `.env` using `nano .env` and update credentials. Set `JWT_SECRET_KEY` to a cryptographically secure random string:
```bash
openssl rand -hex 32
```

### Step 3: Launch Stack
Build the FastAPI application container and start all services in detached background mode:
```bash
sudo docker-compose up -d --build
```
Verify all containers started successfully:
```bash
sudo docker-compose ps
```

### Step 4: Configure Reverse Proxy (Nginx) & SSL Certificate
To access the platform securely via HTTPS and WebSockets, install Nginx and Certbot:
```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

Configure Nginx to reverse proxy ports to the FastAPI docker container (`8000`). Create a configuration file at `/etc/nginx/sites-available/nexbid`:
```nginx
server {
    server_name nexbid.yourdomain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Support WebSockets connection upgrades
    location /ws/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }
}
```
Enable the site and reload Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/nexbid /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```
Request a free SSL certificate from Let's Encrypt:
```bash
sudo certbot --nginx -d nexbid.yourdomain.com
```

---

## Method 2: Platform-as-a-Service (Railway.app)

Railway provides a modern developer dashboard that hosts web services, databases, and Redis caches with zero server configuration.

### Step 1: Deploy PostgreSQL & Redis
1. Log in to [Railway.app](https://railway.app/) and start a **New Project**.
2. Select **Provision PostgreSQL** from the template library.
3. Click **New** > **Provision Redis**.

### Step 2: Deploy the FastAPI Web Service
1. Click **New** > **GitHub Repo** and link your NexBid repository.
2. Railway will automatically detect the `Dockerfile` in the root of the project and begin building the web service.

### Step 3: Configure Environment Variables
On the **web service** dashboard, navigate to **Variables** and link the database and cache internal credentials provided by Railway:
* `DATABASE_URL`: Set to `${{Postgres.DATABASE_URL}}` (converts dynamically to the asyncpg connection string. Note: you may need to append `+asyncpg` manually to the beginning of the protocol: `postgresql+asyncpg://${{POSTGRES_USER}}:${{POSTGRES_PASSWORD}}@${{POSTGRES_HOST}}:${{POSTGRES_PORT}}/${{POSTGRES_DB}}`).
* `REDIS_URL`: Set to `redis://${{REDIS_HOST}}:${{REDIS_PORT}}`.
* `JWT_SECRET_KEY`: Set to a strong secret string.
* `JWT_ALGORITHM`: `HS256`
* `ACCESS_TOKEN_EXPIRE_MINUTES`: `60`

Railway will build, start, and assign a public `https://...` domain to your web container. WebSockets connection support is enabled automatically.

---

## Method 3: Platform-as-a-Service (Render.app)

Render is another popular developer hosting provider offering free and paid plans.

### Step 1: Deploy Database & Redis
1. Log in to [Render.app](https://render.com/).
2. Click **New** > **PostgreSQL** to create a managed database. Copy the internal database connection URL.
3. Click **New** > **Redis** to spin up a Redis cache instance. Copy the internal Redis connection URL.

### Step 2: Deploy the FastAPI App Service
1. Click **New** > **Web Service**.
2. Connect your GitHub repository.
3. Choose **Docker** as the Runtime.
4. Set the build command to use the local `Dockerfile`.
5. Under **Environment Variables**, configure:
   - `DATABASE_URL`: `<your-asyncpg-db-url>` (make sure to replace `postgresql://` with `postgresql+asyncpg://`).
   - `REDIS_URL`: `<your-redis-url>`.
   - `JWT_SECRET_KEY`: `<your-secret-key>`.
6. Click **Deploy Web Service**. Render will build and deploy the container and generate a public URL.
