# POC deployment (Docker Compose)

The compose file for the simulated POC lives at the repository root
(`../../docker-compose.yml`) so that a plain `docker compose up` works from a
fresh clone. This directory holds everything else the POC needs:

| File | Purpose |
|---|---|
| `.env.poc` | POC environment, created from `.env.example` by `startpoc.sh` |
| `prometheus.yml` | scrape config for the bundled Prometheus |
| `grafana/` | provisioned datasource and the GPU dashboard |
| `seed/` | optional seed data (empty by default -- the demo seeds through the API) |

Start it with:

```bash
./startpoc.sh        # or ./install.sh && ./start.sh
```

Compose project name: `gpuinfra-poc`. The MVP uses `gpuinfra-mvp` and a kind
cluster, so the two can never collide.
