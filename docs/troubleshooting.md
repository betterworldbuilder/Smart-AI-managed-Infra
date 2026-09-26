# Troubleshooting

Start here:

```bash
./health.sh        # POC: every service, plus inventory/policies/scenarios
./status.sh        # which mode is running, and what is real in it
./logs.sh backend  # application logs
```

---

## Port already in use

The POC binds `3000` (UI), `8000` (API), `3001` (Grafana), `9090` (Prometheus),
`8181` (OPA) and `8080` (mock openCenter).

```bash
ss -ltnp | grep -E ':(3000|8000|3001|9090|8181|8080)'   # Linux / WSL
lsof -nP -iTCP -sTCP:LISTEN | grep -E '3000|8000'        # macOS
netstat -ano | findstr "3000 8000"                       # Windows
```

Change them in `.env` and restart:

```env
FRONTEND_PORT=3100
BACKEND_PORT=8100
```

```bash
./restart.sh
```

**The POC and the MVP cannot run at the same time** — both want ports
3000/8000/9090. `startmvp.sh` detects this and offers to stop the POC;
`startpoc.sh` after `stopmvp.sh` gets them back.

---

## Docker daemon is not running

```text
FAIL The Docker daemon is not reachable.
```

* macOS / Windows: start Docker Desktop and wait for the whale to settle.
* Linux: `sudo systemctl start docker`
* WSL: either enable Docker Desktop's WSL integration for your distro, or run
  `sudo service docker start` inside the distro.

Verify with `docker info`.

---

## PostgreSQL initialisation fails or is slow

First start runs `initdb`, which can take **two minutes or more** on a slow
volume (WSL 9p, Docker Desktop on macOS). The compose health check allows for
it (`start_period: 180s`).

```bash
./logs.sh postgres
docker compose -p gpuinfra-poc ps
```

If the volume is corrupt (an interrupted first run):

```bash
./reset.sh          # deletes the volume and starts clean
```

The platform also runs perfectly **without** PostgreSQL — clear `DATABASE_URL`
in `.env` and it uses an in-memory store.

---

## Backend cannot connect / is unhealthy

```bash
./logs.sh backend
```

| Symptom | Cause | Fix |
|---|---|---|
| `could not translate host name "postgres"` | started outside compose | use `./start.sh` |
| `connection refused` to postgres | database still initialising | wait, it retries |
| startup hangs ~40 s then works | first-run schema creation | normal |
| `redis unavailable` warning | Redis not up | harmless: the in-process event bus is authoritative |
| `OPA unreachable ... falling back` | OPA container down | harmless: the Python rules are authoritative |

The backend never hard-fails because an optional dependency is missing — it
degrades and says so on the Integrations page.

---

## Frontend is a blank page

1. Hard-reload (Ctrl/Cmd+Shift+R) — a stale service worker or cached bundle.
2. Check the API is reachable **through** the frontend:
   `curl http://localhost:3000/api/system/capabilities`
   If that fails, nginx cannot reach the backend: `./logs.sh frontend`.
3. Rebuild after changing frontend code:
   `docker compose -p gpuinfra-poc build frontend && ./restart.sh`
4. In dev mode (`npm run dev`), the Vite proxy expects the backend on
   `http://localhost:8000`; override with `VITE_BACKEND_URL`.

---

## OPA unavailable

```bash
curl http://localhost:8181/health
curl http://localhost:8181/v1/policies | head
```

The OPA image is distroless, so `docker exec` gives you no shell — probe it over
HTTP. A rego syntax error shows up in `./logs.sh opa` and leaves the container
running with no policies loaded.

Fall back to the built-in engine at any time:

```env
POLICY_ENGINE=internal
```

The Python rules are always authoritative; OPA only adds decisions on top.

---

## Reset the simulation

```bash
./reset.sh            # delete all POC data, reload the simulated datacenter
./clean.sh            # also remove images, volumes and generated config
```

The simulated estate is read from `simulation/inventory.yaml` at start-up, so a
fresh backend container is a fresh datacenter.

---

## kind: "context deadline exceeded" while starting the control plane

Almost always **inotify limits**. A multi-node kind cluster needs more instances
than the default 128:

```bash
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
sudo sysctl -w fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=1048576
```

Make it permanent:

```bash
echo -e 'fs.inotify.max_user_instances=1024\nfs.inotify.max_user_watches=1048576' \
  | sudo tee /etc/sysctl.d/99-kind.conf
sudo sysctl --system
```

`startmvp.sh` checks this before touching kind and offers to fix it.

Other kind failures:

```bash
kind delete cluster --name gpu-native-mvp
./startmvp.sh --recreate
docker logs gpu-native-mvp-control-plane | tail -50
```

Give Docker Desktop at least 4 CPUs and 8 GB of memory for a three-node
cluster.

---

## kind: control plane never starts on Docker 29+ (`overlayfs` driver)

```bash
docker info --format '{{.Driver}}'      # -> overlayfs
```

Docker 29 introduced a new `overlayfs` storage driver. kind runs its own
containerd *inside* the node container, and that nested snapshotter cannot
commit layers on top of it. The symptom is a kubeadm timeout, and underneath:

```bash
kind create cluster --name probe --retain
docker exec probe-control-plane journalctl -u containerd | grep 'commit snapshot'
# error unpacking image: failed to commit snapshot ... aborted aborted aborted
```

No control-plane container is ever created, so the API server refuses or resets
connections. `startmvp.sh` detects this and prints the same guidance.

**Fixes, best first:**

1. Switch Docker back to `overlay2`:

   ```bash
   sudo tee /etc/docker/daemon.json <<'EOF'
   { "storage-driver": "overlay2" }
   EOF
   sudo systemctl restart docker     # WSL: sudo service docker restart
   ```

   Changing the storage driver **hides images and containers created under the
   old driver** (they are not deleted, but they disappear from `docker images`
   until you switch back). Back up anything you need first — this is why the
   installer never does it for you.

2. Use Docker Desktop's built-in Kubernetes, or a Linux VM, for the MVP.

3. Stay on the POC. `./startpoc.sh` exercises the entire workflow — Copilot,
   Governor, policy, both approval gates, DeploymentSpec, openCenter, GitOps,
   monitoring and post-deployment advice — with a simulated Kubernetes layer.
   Only the final "real pods" step is missing.

Setting the containerd snapshotter to `native` inside the node
(`containerdConfigPatches`) gets past the unpack error but is not sufficient on
its own; the storage driver is the real fix.

---

## MVP: pods stay Pending

```bash
export KUBECONFIG=deployments/mvp/kubeconfig
kubectl get pods -A
kubectl -n ai-<workload> describe pod <pod>
```

| Message | Meaning |
|---|---|
| `Insufficient cpu/memory` | the workload does not fit; Docker Desktop needs more resources |
| `pod has unbound immediate PersistentVolumeClaims` | the local-path provisioner binds on first consumer — usually transient |
| `0/3 nodes are available: 3 Insufficient nvidia.com/gpu` | kind has no GPUs; this is expected and the UI says so |
| `ImagePullBackOff` | the generated workload names an image the cluster cannot pull; set a reachable image |

---

## MVP: GPU shows as UNAVAILABLE

That is the truth, not a bug. kind nodes are containers and do not get GPUs by
default. The platform reports host GPUs (via `nvidia-smi`) and cluster GPUs
(via `nvidia.com/gpu`) **separately** and never conflates them.

To make cluster GPUs real you need the NVIDIA Container Toolkit configured for
the kind node runtime plus the GPU operator or device plugin in the cluster.

---

## Windows / Docker Desktop

* Use **WSL 2** as the backend (Settings → General).
* Enable integration for your distro (Settings → Resources → WSL Integration).
* Clone the repository **inside** the WSL filesystem (`/home/you/...`), not
  under `/mnt/c` — bind mounts over 9p are slow enough to time out builds.
* Give Docker Desktop 4+ CPUs and 8+ GB of RAM.
* Line endings: if a script fails with `bad interpreter: /usr/bin/env bash^M`,
  run `git config --global core.autocrlf input` and re-clone, or
  `sed -i 's/\r$//' *.sh`.

---

## WSL networking

* `localhost:3000` works from Windows thanks to WSL 2 localhost forwarding. If
  it does not, find the distro address with `hostname -I` and use that.
* The backend reaches the host (for Ollama) through
  `host.docker.internal`, which compose maps to `host-gateway`.
* A corporate VPN can break Docker's DNS: `docker network prune` and restart
  Docker Desktop.
* `sudo: unable to resolve host <name>` is harmless noise — add your hostname to
  `/etc/hosts` to silence it.

---

## Scripts fail with "Permission denied"

```bash
chmod +x *.sh scripts/*.sh
```

(Git preserves the executable bit; copying files through Windows tools does
not.)

---

## Ollama

The Copilot falls back to the deterministic mock provider whenever the
configured model is unreachable — the demo never breaks. Check which provider is
live on the Integrations page, or:

```bash
curl -s http://localhost:8000/api/system/capabilities | grep llm_provider
```

From a container, Ollama must be reachable at
`http://host.docker.internal:11434`, and `ollama serve` must be running on the
host.

---

## Still stuck

```bash
docker compose -p gpuinfra-poc ps
docker compose -p gpuinfra-poc logs --tail=100
./health.sh
```

Include that output when reporting a problem, along with `docker version`,
`uname -a` and whether you are on WSL, Docker Desktop or native Linux.
