# GitOps tree (MVP)

```text
flux/
├── clusters/        per-cluster Flux entry points
├── infrastructure/  platform components Flux owns
└── workloads/
    └── generated/   ← the Governor writes here, and commits
```

## How it works

When you approve a deployment in MVP mode, `KubernetesDeploymentBackend`:

1. renders the manifests from the approved `DeploymentSpec` (the same
   generator the POC uses),
2. writes them under `workloads/generated/clusters/<cluster>/workloads/<name>/`,
3. **commits** them to the local git repository in this directory,
4. applies them to the cluster.

`generated/` is git-ignored in the *outer* repository — it is its own little
git repo, created on first use, so the deployment history lives with the
manifests rather than polluting the product repo.

## Local versus remote

| `GITOPS_REMOTE_URL` | What happens | Engine label in the UI |
|---|---|---|
| empty (default) | commit locally, apply through the Kubernetes API | `GitOps working tree + Kubernetes server-side apply` |
| set to a repo Flux can reach | commit and push; Flux reconciles | `FluxCD (GitOps)` |

The label never says "FluxCD" unless Flux actually did the work. Flux is
genuinely installed either way, and its Kustomization health is reported on the
Integrations page.

## Inspecting what was committed

```bash
cd deployments/mvp/flux/workloads/generated
git log --oneline
git show --stat HEAD
```

or through the API:

```bash
curl -s localhost:8000/api/gitops -H "Authorization: Bearer $TOKEN" | jq
```

## Adding a real Git remote

```bash
cd deployments/mvp/flux/workloads/generated
git remote add origin git@git.example.com:infra/gitops.git
git push -u origin main
```

then set `GITOPS_REMOTE_URL` and create a Flux `GitRepository` pointing at it.
The `LocalGitOpsRepo` interface is deliberately small so a GitHub/GitLab
implementation drops in without touching the deployment backend.
