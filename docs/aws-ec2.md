# Hosting the demo on AWS EC2

The POC runs unchanged on an EC2 instance. The scripts detect that they are on
EC2 and adapt:

* **URLs** printed by `install.sh`, `start.sh`, `startpoc.sh` and `demo.sh` use
  the instance's **public address**, found through the instance metadata
  service (IMDSv2) — public IPv4 first, then public DNS.
* **Default passwords are replaced.** On a public host, any `admin`/`admin`
  still in `.env` is swapped for a random 20-character password (and a random
  signing secret), printed once at the end of the install, and kept in `.env`.
* **Unauthenticated internal services stay private.** OPA, the mock openCenter
  and Prometheus bind to `127.0.0.1` on the host, so they are unreachable from
  the internet even if a security group is too permissive.

The application itself needed no change: the UI calls the API through relative
`/api` paths behind nginx, so it works on any address.

> **What does *not* change — on purpose.** Health checks and service-to-service
> calls keep using `localhost`: they run *on* the instance. Pointing them at
> the public IP would send traffic out through the internet gateway and back
> in through the security group.

---

## 1. Launch the instance

| Setting | Recommendation |
|---|---|
| AMI | Ubuntu Server 24.04 LTS (or 22.04) |
| Type | `t3.large` (2 vCPU, 8 GB) minimum · `t3.xlarge` comfortable |
| Storage | 30 GB gp3 |
| Public IP | enabled — or attach an **Elastic IP** so the address survives stop/start |
| IMDS | IMDSv2 (the default) — the scripts use it |

No GPU instance is needed: the POC simulates the GPU estate.

## 2. Security group

| Port | Source | Why |
|---|---|---|
| 22 | **your IP only** | SSH |
| 3000 | your IP, or `0.0.0.0/0` for a public demo | the UI |
| 8000 | your IP (optional) | API docs at `/docs`. The UI does **not** need it — it goes through 3000 |
| 3001 | your IP (optional) | Grafana |

**Do not open** 8181 (OPA), 8080 (mock openCenter), 9090 (Prometheus),
5432 or 6379. They are loopback-bound anyway; the security group is the second
lock.

## 3. Install Docker

```bash
ssh ubuntu@<public-ip>

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker            # or log out and back in
docker compose version   # confirms the compose plugin
```

## 4. Run it

```bash
git clone https://github.com/betterworldbuilder/Smart-AI-managed-Infra.git
cd Smart-AI-managed-Infra
./demo.sh
```

The end of the output looks like:

```text
==================================================
GPU NATIVE INFRA POC READY
==================================================

Open:
http://54.210.12.34:3000
...
Login:       admin / Xq7Lp2mRk9WvTz4Hn8Bd
             (stored in .env; change AUTH_PASSWORD there)
```

That address and password are what you share.

## 5. Custom domain, load balancer or pinned address

Set the address yourself and the scripts use it verbatim — no metadata lookup:

```bash
echo 'PUBLIC_HOST=demo.example.com' >> .env
./restart.sh
```

You need this when:

* the instance sits in a **private subnet behind an ALB** (the metadata service
  reports no public IP);
* you front it with a **domain name**;
* you want links to stay stable across restarts **without** an Elastic IP.

## 6. HTTPS (recommended for anything beyond a quick demo)

The stack serves plain HTTP. For a shared demo, put TLS in front:

* **Easiest:** an Application Load Balancer with an ACM certificate,
  forwarding to the instance on 3000. Set `PUBLIC_HOST` to the ALB's name.
* **Single instance:** Caddy on 443 in front of port 3000 — it obtains the
  certificate automatically:

  ```bash
  sudo apt install -y caddy
  echo 'demo.example.com {
    reverse_proxy localhost:3000
  }' | sudo tee /etc/caddy/Caddyfile
  sudo systemctl reload caddy
  ```

  Then open only 80/443 (and 22) in the security group, and close 3000.

## 7. Operating it

```bash
./health.sh        # one-screen status
./selftest.sh      # 10 end-to-end checks
./logs.sh backend
./stop.sh          # keeps data
./reset.sh         # fresh simulation
```

Credentials live in `.env`. To change them:

```bash
sed -i 's/^AUTH_PASSWORD=.*/AUTH_PASSWORD=choose-something-long/' .env
./restart.sh
```

To keep `admin/admin` on a public host (not recommended), set
`AUTH_PASSWORD=admin` again **after** install — hardening only replaces
defaults it finds, it never overwrites a value you chose.

## Cost

A `t3.large` in `us-east-1` is roughly **$0.08/hour** (about $60/month if left
running). Stop the instance between demos; with an Elastic IP the address
survives, and `./start.sh` brings everything back with data intact.

## The kind MVP on EC2

A fresh EC2 instance is actually the *easiest* place to fix the kind problem
described in
[troubleshooting.md](troubleshooting.md#kind-control-plane-never-starts-on-docker-29-overlayfs-driver):
a current `get.docker.com` install may default to the same `overlayfs` driver
that stops kind, but on a brand-new instance there are no images to lose, so
switch to `overlay2` **before** building anything:

```bash
docker info --format '{{.Driver}}'          # if this says overlayfs:
echo '{ "storage-driver": "overlay2" }' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

Then use at least a `t3.xlarge` (4 vCPU, 16 GB) for the three-node cluster
and let `startmvp.sh` raise the inotify limits. This path is not yet verified
end to end — if you run it, please report back.
