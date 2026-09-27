# Hosting the demo on AWS EC2

The POC runs unchanged on an EC2 instance. The scripts detect that they are on
EC2 and adapt:

* **URLs** printed by `install.sh`, `start.sh`, `startpoc.sh` and `demo.sh` use
  the instance's **public address**, found through the instance metadata
  service (IMDSv2) — public IPv4 first, then public DNS.
* **No app login.** The UI opens straight into the app (`AUTH_ENABLED=false`),
  so **the security group is the access control**: open port 3000 to the IPs
  you invite, not `0.0.0.0/0`. Grafana's default `admin` password is still
  replaced with a random one on a public host.
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
| 3000 | your IP and the IPs you invite — there is no login | the UI |
| 8000 | your IP (optional) | API docs at `/docs`. The UI does **not** need it — it goes through 3000 |
| 3001 | your IP (optional) | Grafana |

**Do not open** 8181 (OPA), 8080 (mock openCenter), 9090 (Prometheus),
5432 or 6379. They are loopback-bound anyway; the security group is the second
lock.

## 3. Install Docker (automatic)

On Ubuntu/Debian you can skip this: `startpoc.sh`, `install.sh`, `start.sh`
and `startmvp.sh` notice Docker is missing and offer to install it with the
official script, add you to the `docker` group, and carry on in the same run
— no logout needed. Pass `-y` to accept without a prompt.

To do it by hand instead:

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
Login:       none -- the app opens directly (AUTH_ENABLED=false)
             anyone who can reach the UI can use it: limit the
             security group to your own IP
```

That address is what you share — with people whose IP you allowed.

### The script says it's ready, but the browser shows nothing

The script only reports ready after the UI answered **on the instance**, so
the problem is between your browser and the instance. Check, in this order:

1. **Security group.** The usual cause. Add an inbound rule: Custom TCP,
   port 3000, source *My IP*. On a public host the script prints this
   instance's security-group ID and a ready-to-run command:

   ```bash
   aws ec2 authorize-security-group-ingress --region <region> --group-id <sg-id> \
     --protocol tcp --port 3000 --cidr "$(curl -s https://checkip.amazonaws.com)/32"
   ```

2. **The address.** Open `http://<public-ip>:3000`. `localhost:3000` on your
   laptop is your laptop.
3. **HTTPS.** Browsers sometimes upgrade to `https://` on their own; these
   ports are plain HTTP. Type `http://` explicitly (or add TLS, section 6).

Tell them apart from your laptop:

```bash
curl -m 5 -s -o /dev/null -w '%{http_code}\n' http://<public-ip>:3000/healthz
```

| Result | Meaning |
|---|---|
| `200` | reachable — it was the address or HTTPS |
| timeout / `000` | blocked — security group (or a network ACL / corporate proxy) |
| connection refused | nothing listening — run `./health.sh` on the instance |

Ubuntu's `ufw` is inactive on EC2 images, and Docker's published ports bypass
it anyway, so it is rarely the culprit.

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

The app has no login. To bring back the single-account sign-in (POC grade),
start with it switched on — on a public host a random password is generated
into `.env` and printed:

```bash
AUTH_ENABLED=true ./startpoc.sh
```

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
