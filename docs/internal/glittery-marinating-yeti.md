# RoboCo Open Core + Kubernetes Migration Plan

## Overview

**Goal:** Restructure RoboCo for commercialization with an Open Core model, then
deploy to Kubernetes with ArgoCD on the UGREEN NAS.

**Order:** Separation first, then K8s migration.

---

## What's Open vs Closed

| Component | Status | Reason |
|-----------|--------|--------|
| Backend API (`roboco/api/`, `roboco/services/`) | **CLOSED** | Core product
value |
| Orchestrator (`roboco/runtime/`) | **CLOSED** | Competitive advantage |
| MCP Servers (`roboco/mcp/`) | **CLOSED** | Business logic |
| Frontend (future dashboard) | **OPEN** | Drives adoption |
| Python/JS SDK | **OPEN** | Ecosystem growth |
| CLI Tool | **OPEN** | Developer experience |
| Agent Blueprints | **OPEN** | Community templates |

---

## Phase 1: API Separation (7-10 days)

### Why This First
- SDK/CLI can be developed independently of infra
- Validates API surface before K8s migration
- Creates public-facing documentation
- Allows community to start building agents

### Step 1.1: Create OSS Repo Structure (1 day)

Create public `roboco-agents` repo on GitHub:

```
roboco-agents/
├── sdk/
│   └── python/
│       ├── roboco_sdk/
│       │   ├── __init__.py
│       │   ├── client.py         # Main RobocoClient class
│       │   ├── tasks.py          # TasksAPI wrapper
│       │   ├── messages.py       # MessagesAPI wrapper
│       │   ├── journals.py       # JournalsAPI wrapper
│       │   ├── notifications.py  # NotificationsAPI wrapper
│       │   └── models.py         # Pydantic models (Task, Message, etc.)
│       ├── pyproject.toml
│       ├── README.md
│       └── tests/
├── cli/
│   └── roboco_cli/
│       ├── __init__.py
│       ├── main.py               # Typer app entry
│       ├── commands/
│       │   ├── task.py           # roboco task <cmd>
│       │   ├── agent.py          # roboco agent <cmd>
│       │   ├── message.py        # roboco message <cmd>
│       │   └── config.py         # roboco config <cmd>
│       └── config.py             # Config file handling
├── blueprints/
│   ├── templates/
│   │   ├── developer.md          # Generic dev blueprint
│   │   ├── qa.md                 # Generic QA blueprint
│   │   ├── pm.md                 # Generic PM blueprint
│   │   └── documenter.md         # Generic documenter blueprint
│   └── examples/
│       └── simple-team/          # Example 3-agent team
├── docker/
│   ├── agent-base.Dockerfile     # Base image for agents
│   └── claude-code.Dockerfile    # Claude Code agent image
├── examples/
│   ├── hello-world/              # Minimal agent example
│   ├── task-worker/              # Task processing example
│   └── multi-agent/              # Team coordination example
├── docs/
│   ├── getting-started.md
│   ├── sdk-reference.md
│   ├── cli-reference.md
│   └── blueprint-guide.md
├── LICENSE                       # MIT or Apache 2.0
├── README.md
└── pyproject.toml                # Workspace/monorepo config
```

**Actions:**
1. `gh repo create roboco-agents --public`
2. Initialize with pyproject.toml workspace
3. Setup GitHub Actions for CI/CD
4. Configure PyPI publishing workflow

---

### Step 1.2: Extract SDK from MCP Utils (2-3 days)

**Source files to extract from:**
- `roboco/mcp/utils.py` - `ApiClient` class
- `roboco/api/schemas/*.py` - Response models
- `roboco/models/*.py` - Core domain models

**SDK Structure:**

```python
# roboco_sdk/client.py
import httpx
from typing import Optional

class RobocoClient:
    """Main client for RoboCo API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/api/v1",
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_role: Optional[str] = None,
    ):
        self.base_url = base_url
        self.headers = {}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        if agent_id:
            self.headers["X-Agent-ID"] = agent_id
        if agent_role:
            self.headers["X-Agent-Role"] = agent_role

        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=self.headers,
            timeout=30.0
        )

    @property
    def tasks(self) -> "TasksAPI":
        return TasksAPI(self._http)

    @property
    def messages(self) -> "MessagesAPI":
        return MessagesAPI(self._http)

    @property
    def journals(self) -> "JournalsAPI":
        return JournalsAPI(self._http)

    @property
    def notifications(self) -> "NotificationsAPI":
        return NotificationsAPI(self._http)

    async def close(self):
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
```

```python
# roboco_sdk/tasks.py
from typing import Optional, List
from .models import Task, TaskCreate, TaskUpdate

class TasksAPI:
    def __init__(self, http: httpx.AsyncClient):
        self._http = http

    async def list(
        self,
        status: Optional[str] = None,
        team: Optional[str] = None,
        assigned_to: Optional[str] = None,
        limit: int = 50,
    ) -> List[Task]:
        params = {"limit": limit}
        if status:
            params["status"] = status
        if team:
            params["team"] = team
        if assigned_to:
            params["assigned_to"] = assigned_to

        resp = await self._http.get("/tasks", params=params)
        resp.raise_for_status()
        return [Task(**t) for t in resp.json()["items"]]

    async def get(self, task_id: str) -> Task:
        resp = await self._http.get(f"/tasks/{task_id}")
        resp.raise_for_status()
        return Task(**resp.json())

    async def create(self, data: TaskCreate) -> Task:
        resp = await self._http.post("/tasks", json=data.model_dump())
        resp.raise_for_status()
        return Task(**resp.json())

    async def claim(self, task_id: str) -> Task:
        resp = await self._http.post(f"/tasks/{task_id}/claim")
        resp.raise_for_status()
        return Task(**resp.json())

    async def start(self, task_id: str) -> Task:
        resp = await self._http.post(f"/tasks/{task_id}/start")
        resp.raise_for_status()
        return Task(**resp.json())

    async def progress(
        self, task_id: str, message: str, percentage: int
    ) -> Task:
        resp = await self._http.post(
            f"/tasks/{task_id}/progress",
            json={"message": message, "percentage": percentage}
        )
        resp.raise_for_status()
        return Task(**resp.json())

    async def submit_for_qa(self, task_id: str) -> Task:
        resp = await self._http.post(f"/tasks/{task_id}/submit-qa")
        resp.raise_for_status()
        return Task(**resp.json())

    # ... more methods
```

```python
# roboco_sdk/models.py
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    AWAITING_QA = "awaiting_qa"
    AWAITING_DOCUMENTATION = "awaiting_documentation"
    AWAITING_PM_REVIEW = "awaiting_pm_review"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class Task(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    status: TaskStatus
    priority: int = 3
    team: Optional[str] = None
    assigned_to: Optional[str] = None
    parent_task_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # ... more fields

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    priority: int = 3
    team: Optional[str] = None
    parent_task_id: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
```

**Testing:**
```python
# tests/test_tasks.py
import pytest
from roboco_sdk import RobocoClient

@pytest.mark.asyncio
async def test_list_tasks():
    async with RobocoClient() as client:
        tasks = await client.tasks.list(status="pending")
        assert isinstance(tasks, list)
```

---

### Step 1.3: Create CLI Tool (2-3 days)

**CLI using Typer:**

```python
# roboco_cli/main.py
import typer
from roboco_cli.commands import task, agent, message, config

app = typer.Typer(
    name="roboco",
    help="RoboCo CLI - Manage AI agent workflows"
)

app.add_typer(task.app, name="task")
app.add_typer(agent.app, name="agent")
app.add_typer(message.app, name="message")
app.add_typer(config.app, name="config")

if __name__ == "__main__":
    app()
```

```python
# roboco_cli/commands/task.py
import typer
from rich.console import Console
from rich.table import Table
from roboco_sdk import RobocoClient
import asyncio

app = typer.Typer(help="Task management commands")
console = Console()

@app.command("list")
def list_tasks(
    status: str = typer.Option(None, "--status", "-s"),
    team: str = typer.Option(None, "--team", "-t"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """List tasks with optional filters."""
    async def _list():
        async with RobocoClient() as client:
            return await client.tasks.list(
                status=status, team=team, limit=limit
            )

    tasks = asyncio.run(_list())

    table = Table(title="Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Status", style="green")
    table.add_column("Team")
    table.add_column("Assigned")

    for t in tasks:
        table.add_row(
            t.id[:8],
            t.title[:40],
            t.status.value,
            t.team or "-",
            t.assigned_to[:8] if t.assigned_to else "-"
        )

    console.print(table)

@app.command("get")
def get_task(task_id: str):
    """Get task details."""
    async def _get():
        async with RobocoClient() as client:
            return await client.tasks.get(task_id)

    task = asyncio.run(_get())
    console.print(task)

@app.command("claim")
def claim_task(task_id: str):
    """Claim a task."""
    async def _claim():
        async with RobocoClient() as client:
            return await client.tasks.claim(task_id)

    task = asyncio.run(_claim())
    console.print(f"✓ Claimed task: {task.title}")

# ... more commands
```

**Config Management:**
```python
# roboco_cli/config.py
from pathlib import Path
import toml

CONFIG_PATH = Path.home() / ".roboco" / "config.toml"

def get_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"api_url": "http://localhost:8000/api/v1"}
    return toml.load(CONFIG_PATH)

def set_config(key: str, value: str):
    config = get_config()
    config[key] = value
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        toml.dump(config, f)
```

**CLI Commands:**
```bash
# Task management
roboco task list --status pending --team backend
roboco task get abc123
roboco task claim abc123
roboco task start abc123
roboco task progress abc123 "50% done" --percent 50

# Agent info
roboco agent list
roboco agent status be-dev-1

# Messages
roboco message send --channel backend-cell --content "Hello team"
roboco message history backend-cell

# Config
roboco config set api-url https://api.roboco.io
roboco config set api-key sk-xxx
roboco config show
```

---

### Step 1.4: Package Blueprints (1 day)

**Genericize blueprints for open source:**

Current (RoboCo-specific):
```markdown
# Backend Developer 1 (be-dev-1)
You are Backend Developer 1 in the RoboCo organization...
Channel: #backend-cell
```

Generic (OSS template):
```markdown
# Developer Agent Blueprint

## Role
You are a Developer agent responsible for implementing features and fixing bugs.

## Capabilities
- Claim and work on assigned tasks
- Write and test code
- Submit work for QA review
- Communicate progress via channels

## Workflow
1. SCAN - Check for pending/assigned tasks
2. CLAIM - Lock and take ownership
3. PLAN - Break down into subtasks
4. EXECUTE - Write code, commit frequently
5. VERIFY - Self-check against acceptance criteria
6. SUBMIT - Send for QA review

## Required Tools
- roboco_task_scan
- roboco_task_claim
- roboco_task_start
- roboco_task_progress
- roboco_task_submit_qa
- roboco_message_send
- roboco_journal_entry

## Configuration
```yaml
agent:
role: developer
team: ${TEAM}
channel: ${TEAM}-cell
```
```

**Files to move:**
- `agents/blueprints/backend/be-dev.md` → `blueprints/templates/developer.md`
- `agents/blueprints/backend/be-qa.md` → `blueprints/templates/qa.md`
- `agents/blueprints/backend/be-pm.md` → `blueprints/templates/pm.md`
- `agents/blueprints/backend/be-documenter.md` →
`blueprints/templates/documenter.md`

---

### Step 1.5: Restructure Private Repo (1-2 days)

**After extraction, private repo becomes:**

```
roboco/                           # PRIVATE - Commercial product
├── roboco/
│   ├── api/                      # FastAPI backend (unchanged)
│   ├── services/                 # Business logic (unchanged)
│   ├── runtime/                  # Orchestrator (unchanged)
│   ├── mcp/                      # MCP servers (unchanged)
│   ├── models/                   # Domain models (unchanged)
│   ├── db/                       # Database layer (unchanged)
│   └── config.py
├── agents/
│   └── blueprints/               # RoboCo-specific blueprints (keep)
│       ├── backend/
│       ├── frontend/
│       ├── ux_ui/
│       └── board/
├── deploy/                       # K8s manifests (Phase 2)
│   ├── base/
│   └── overlays/
├── docker/
│   ├── api.Dockerfile
│   ├── orchestrator.Dockerfile
│   └── docker-compose.yml
├── tests/
├── pyproject.toml
└── README.md
```

**Changes needed:**
1. Remove SDK extraction from `mcp/utils.py` (keep minimal internal client)
2. Update imports if needed
3. Document private repo setup

---

## Phase 2: Kubernetes + ArgoCD (12-16 days)

### Why K8s Over Docker Compose
- **Auto-healing**: Pods restart automatically on failure
- **Scaling**: Easy horizontal scaling of API/orchestrator
- **GitOps**: ArgoCD enables push-to-deploy workflow
- **Resource limits**: Proper CPU/memory management for agents
- **Secrets management**: Sealed Secrets for git-safe secrets
- **Observability**: Built-in metrics, logs aggregation

---

### Step 2.1: Setup K8s on NAS (1-2 days)

**Prerequisites on UGREEN NAS:**
```bash
# Disable swap (required for K8s)
sudo swapoff -a
sudo sed -i '/ swap / s/^/#/' /etc/fstab

# Enable required kernel modules
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
sudo modprobe overlay
sudo modprobe br_netfilter

# Sysctl settings
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sudo sysctl --system
```

**Install kubeadm, kubelet, kubectl:**
```bash
# Add K8s apt repo
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg
--dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg]
https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /' | sudo tee
/etc/apt/sources.list.d/kubernetes.list

sudo apt-get update
sudo apt-get install -y kubelet kubeadm kubectl
sudo apt-mark hold kubelet kubeadm kubectl
```

**Initialize cluster:**
```bash
# Initialize single-node cluster
sudo kubeadm init \
--pod-network-cidr=10.244.0.0/16 \
--apiserver-advertise-address=192.168.50.111

# Setup kubectl for current user
mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
sudo chown $(id -u):$(id -g) $HOME/.kube/config

# Allow scheduling on control plane (single-node)
kubectl taint nodes --all node-role.kubernetes.io/control-plane-

# Install Flannel CNI
kubectl apply -f
https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml

# Verify
kubectl get nodes
kubectl get pods -n kube-system
```

---

### Step 2.2: Install ArgoCD (1 day)

```bash
# Create namespace and install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f
https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for pods
kubectl wait --for=condition=Ready pods --all -n argocd --timeout=300s

# Get initial admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o
jsonpath="{.data.password}" | base64 -d

# Expose via NodePort (for local access)
kubectl patch svc argocd-server -n argocd -p '{"spec": {"type": "NodePort"}}'

# Or use port-forward
kubectl port-forward svc/argocd-server -n argocd 8080:443

# Access at https://192.168.50.111:8080
# Username: admin, Password: (from above)
```

**Configure ArgoCD for private repo:**
```bash
# Add SSH key for repo access
argocd repo add git@github.com:renzof/roboco.git \
--ssh-private-key-path ~/.ssh/id_ed25519

# Or via UI: Settings → Repositories → Connect Repo
```

---

### Step 2.3: Setup NFS Storage (1 day)

**On NAS - Create NFS share:**
```bash
# Create data directories
sudo mkdir -p /volume1/roboco/k8s-data/{postgres,redis,qdrant}
sudo chmod 777 /volume1/roboco/k8s-data/*

# Add to /etc/exports
echo "/volume1/roboco/k8s-data *(rw,sync,no_subtree_check,no_root_squash)" |
sudo tee -a /etc/exports
sudo exportfs -ra
```

**Install NFS CSI Driver:**
```bash
# Install NFS CSI driver
helm repo add csi-driver-nfs
https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts
helm install csi-driver-nfs csi-driver-nfs/csi-driver-nfs \
--namespace kube-system \
--set driver.mountPermissions=0777
```

**StorageClass:**
```yaml
# deploy/base/storage/storageclass.yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
name: nfs-roboco
provisioner: nfs.csi.k8s.io
parameters:
server: 192.168.50.111
share: /volume1/roboco/k8s-data
reclaimPolicy: Retain
volumeBindingMode: Immediate
mountOptions:
- nfsvers=4.1
```

---

### Step 2.4: Create K8s Manifests (2-3 days)

**Full manifest structure:**
```
deploy/
├── base/
│   ├── namespace.yaml
│   ├── storage/
│   │   └── storageclass.yaml
│   ├── postgres/
│   │   ├── statefulset.yaml
│   │   ├── service.yaml
│   │   ├── pvc.yaml
│   │   └── configmap.yaml
│   ├── redis/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── qdrant/
│   │   ├── statefulset.yaml
│   │   ├── service.yaml
│   │   └── pvc.yaml
│   ├── api/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── configmap.yaml
│   ├── orchestrator/
│   │   ├── deployment.yaml
│   │   ├── serviceaccount.yaml
│   │   ├── role.yaml
│   │   ├── rolebinding.yaml
│   │   └── configmap.yaml
│   └── kustomization.yaml
├── overlays/
│   ├── production/
│   │   ├── kustomization.yaml
│   │   ├── secrets.yaml          # Sealed secrets
│   │   └── patches/
│   │       └── api-replicas.yaml
│   └── development/
│       └── kustomization.yaml
└── argocd/
    └── apps/
        └── roboco.yaml
```

**Namespace:**
```yaml
# deploy/base/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
name: roboco
```

**PostgreSQL StatefulSet:**
```yaml
# deploy/base/postgres/statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
name: postgres
namespace: roboco
spec:
serviceName: postgres
replicas: 1
selector:
    matchLabels:
    app: postgres
template:
    metadata:
    labels:
        app: postgres
    spec:
    containers:
    - name: postgres
        image: pgvector/pgvector:pg16
        ports:
        - containerPort: 5432
        env:
        - name: POSTGRES_USER
        valueFrom:
            secretKeyRef:
            name: roboco-secrets
            key: postgres-user
        - name: POSTGRES_PASSWORD
        valueFrom:
            secretKeyRef:
            name: roboco-secrets
            key: postgres-password
        - name: POSTGRES_DB
        value: roboco
        volumeMounts:
        - name: postgres-data
        mountPath: /var/lib/postgresql/data
        resources:
        requests:
            memory: "512Mi"
            cpu: "250m"
        limits:
            memory: "2Gi"
            cpu: "1000m"
volumeClaimTemplates:
- metadata:
    name: postgres-data
    spec:
    accessModes: ["ReadWriteOnce"]
    storageClassName: nfs-roboco
    resources:
        requests:
        storage: 10Gi
```

**API Deployment:**
```yaml
# deploy/base/api/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
name: roboco-api
namespace: roboco
spec:
replicas: 2
selector:
    matchLabels:
    app: roboco-api
template:
    metadata:
    labels:
        app: roboco-api
    spec:
    containers:
    - name: api
        image: ghcr.io/renzof/roboco-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
        valueFrom:
            secretKeyRef:
            name: roboco-secrets
            key: database-url
        - name: REDIS_URL
        value: redis://redis:6379
        - name: QDRANT_URL
        value: http://qdrant:6333
        - name: ENVIRONMENT
        value: production
        resources:
        requests:
            memory: "256Mi"
            cpu: "100m"
        limits:
            memory: "1Gi"
            cpu: "500m"
        livenessProbe:
        httpGet:
            path: /health
            port: 8000
        initialDelaySeconds: 10
        periodSeconds: 10
        readinessProbe:
        httpGet:
            path: /health
            port: 8000
        initialDelaySeconds: 5
        periodSeconds: 5
```

**Kustomization:**
```yaml
# deploy/base/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: roboco

resources:
- namespace.yaml
- storage/storageclass.yaml
- postgres/statefulset.yaml
- postgres/service.yaml
- redis/deployment.yaml
- redis/service.yaml
- qdrant/statefulset.yaml
- qdrant/service.yaml
- api/deployment.yaml
- api/service.yaml
- orchestrator/deployment.yaml
- orchestrator/serviceaccount.yaml
- orchestrator/role.yaml
- orchestrator/rolebinding.yaml

images:
- name: ghcr.io/renzof/roboco-api
    newTag: latest
- name: ghcr.io/renzof/roboco-orchestrator
    newTag: latest
```

---

### Step 2.5: Orchestrator RBAC (part of 2.4)

**ServiceAccount:**
```yaml
# deploy/base/orchestrator/serviceaccount.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
name: roboco-orchestrator
namespace: roboco
```

**Role (for spawning agent Jobs):**
```yaml
# deploy/base/orchestrator/role.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
name: roboco-orchestrator
namespace: roboco
rules:
# Create/manage agent Jobs
- apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "delete", "get", "list", "watch", "patch"]
# Watch pods for job status
- apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
# Read pod logs for debugging
- apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
# Manage ConfigMaps for agent configs
- apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["create", "delete", "get", "list"]
```

**RoleBinding:**
```yaml
# deploy/base/orchestrator/rolebinding.yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
name: roboco-orchestrator
namespace: roboco
subjects:
- kind: ServiceAccount
    name: roboco-orchestrator
    namespace: roboco
roleRef:
kind: Role
name: roboco-orchestrator
apiGroup: rbac.authorization.k8s.io
```

---

### Step 2.6: Modify Orchestrator for K8s (2-3 days)

**Critical file:** `roboco/runtime/orchestrator.py`

**Current Docker approach (to replace):**
```python
# Lines ~430-500 in spawn_agent method
cmd = [
    "docker", "run", "-d",
    "--name", container_name,
    "-e", f"AGENT_ID={agent_id}",
    ...
]
subprocess.run(cmd)
```

**New K8s approach:**
```python
# roboco/runtime/orchestrator.py

from kubernetes import client, config
from kubernetes.client.rest import ApiException

class AgentOrchestrator:
    def __init__(self):
        # Load in-cluster config (when running in K8s)
        # or kubeconfig for local dev
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.batch_v1 = client.BatchV1Api()
        self.core_v1 = client.CoreV1Api()
        self.namespace = settings.k8s_namespace or "roboco"

    async def spawn_agent(
        self,
        agent_id: str,
        task_id: str,
        initial_prompt: str,
    ) -> str:
        """Spawn an agent as a K8s Job."""
        job_name = f"agent-{agent_id}-{task_id[:8]}"

        # Get agent config (blueprint, role, etc.)
        agent_config = self._get_agent_config(agent_id)

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app": "roboco-agent",
                    "agent-id": agent_id,
                    "task-id": task_id[:8],
                },
            ),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=3600,  # Cleanup after 1 hour
                backoff_limit=0,  # No retries
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={
                            "app": "roboco-agent",
                            "agent-id": agent_id,
                        },
                    ),
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        service_account_name="roboco-agent",
                        containers=[
                            client.V1Container(
                                name="agent",
                                image=f"ghcr.io/renzof/roboco-agent:{agent_id}",
                                env=[
                                    client.V1EnvVar(
                                        name="AGENT_ID",
                                        value=agent_id,
                                    ),
                                    client.V1EnvVar(
                                        name="TASK_ID",
                                        value=task_id,
                                    ),
                                    client.V1EnvVar(
                                        name="INITIAL_PROMPT",
                                        value=initial_prompt,
                                    ),
                                    client.V1EnvVar(
                                        name="API_URL",
                                        value=settings.internal_api_url,
                                    ),
                                    client.V1EnvVar(
                                        name="ANTHROPIC_API_KEY",
                                        value_from=client.V1EnvVarSource(

secret_key_ref=client.V1SecretKeySelector(
                                                name="roboco-secrets",
                                                key="anthropic-api-key",
                                            ),
                                        ),
                                    ),
                                ],
                                resources=client.V1ResourceRequirements(
                                    requests={"memory": "256Mi", "cpu": "100m"},
                                    limits={"memory": "1Gi", "cpu": "500m"},
                                ),
                                volume_mounts=[
                                    client.V1VolumeMount(
                                        name="blueprints",
                                        mount_path="/app/agents/blueprints",
                                        read_only=True,
                                    ),
                                ],
                            ),
                        ],
                        volumes=[
                            client.V1Volume(
                                name="blueprints",
                                config_map=client.V1ConfigMapVolumeSource(
                                    name="roboco-blueprints",
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        )

        try:
            self.batch_v1.create_namespaced_job(
                namespace=self.namespace,
                body=job,
            )
            logger.info(
                "Spawned agent job",
                job_name=job_name,
                agent_id=agent_id,
                task_id=task_id,
            )
            return job_name
        except ApiException as e:
            logger.error(
                "Failed to spawn agent job",
                error=str(e),
                agent_id=agent_id,
            )
            raise

    async def stop_agent(self, agent_id: str) -> None:
        """Stop an agent by deleting its Job."""
        # Find jobs for this agent
        jobs = self.batch_v1.list_namespaced_job(
            namespace=self.namespace,
            label_selector=f"agent-id={agent_id}",
        )

        for job in jobs.items:
            self.batch_v1.delete_namespaced_job(
                name=job.metadata.name,
                namespace=self.namespace,
                propagation_policy="Background",
            )
            logger.info("Deleted agent job", job_name=job.metadata.name)

    async def get_agent_status(self, agent_id: str) -> AgentState:
        """Get agent status from Job status."""
        jobs = self.batch_v1.list_namespaced_job(
            namespace=self.namespace,
            label_selector=f"agent-id={agent_id}",
        )

        if not jobs.items:
            return AgentState.STOPPED

        job = jobs.items[-1]  # Most recent
        if job.status.succeeded:
            return AgentState.COMPLETED
        elif job.status.failed:
            return AgentState.FAILED
        elif job.status.active:
            return AgentState.ACTIVE
        else:
            return AgentState.PENDING
```

**Config changes:**
```python
# roboco/config.py
class Settings(BaseSettings):
    # ... existing settings ...

    # K8s settings
    k8s_namespace: str = "roboco"
    k8s_agent_image: str = "ghcr.io/renzof/roboco-agent"
    k8s_agent_memory_request: str = "256Mi"
    k8s_agent_memory_limit: str = "1Gi"
    k8s_job_ttl: int = 3600  # seconds
```

---

### Step 2.7: Secrets with Sealed Secrets (1 day)

**Install kubeseal CLI:**
```bash
# macOS
brew install kubeseal

# Linux
wget https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.24.5/ku
beseal-0.24.5-linux-amd64.tar.gz
tar -xvf kubeseal-*.tar.gz
sudo mv kubeseal /usr/local/bin/
```

**Install controller:**
```bash
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/downloa
d/v0.24.5/controller.yaml
```

**Create secrets:**
```yaml
# secrets.yaml (NOT committed to git)
apiVersion: v1
kind: Secret
metadata:
name: roboco-secrets
namespace: roboco
type: Opaque
stringData:
postgres-user: roboco
postgres-password: your-secure-password
database-url: postgresql://roboco:your-secure-password@postgres:5432/roboco
anthropic-api-key: sk-ant-xxx
redis-url: redis://redis:6379
```

**Seal the secrets:**
```bash
kubeseal --format yaml < secrets.yaml >
deploy/overlays/production/sealed-secrets.yaml
rm secrets.yaml  # Don't keep plaintext!
```

---

### Step 2.8: ArgoCD Applications (1 day)

```yaml
# deploy/argocd/apps/roboco.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
name: roboco
namespace: argocd
finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
project: default

source:
    repoURL: git@github.com:renzof/roboco.git
    targetRevision: main
    path: deploy/overlays/production

destination:
    server: https://kubernetes.default.svc
    namespace: roboco

syncPolicy:
    automated:
    prune: true
    selfHeal: true
    allowEmpty: false
    syncOptions:
    - CreateNamespace=true
    - PrunePropagationPolicy=foreground
    retry:
    limit: 5
    backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m

# Health checks
ignoreDifferences:
    - group: apps
    kind: Deployment
    jsonPointers:
        - /spec/replicas  # Allow HPA to manage
```

**Apply:**
```bash
kubectl apply -f deploy/argocd/apps/roboco.yaml
```

---

### Step 2.9: Testing & Validation (2-3 days)

**Validation checklist:**

```bash
# 1. Verify all pods running
kubectl get pods -n roboco
# Expected: postgres-0, redis-xxx, qdrant-0, roboco-api-xxx (x2),
roboco-orchestrator-xxx

# 2. Check PostgreSQL
kubectl exec -it postgres-0 -n roboco -- psql -U roboco -c "SELECT 1"

# 3. Check Redis
kubectl exec -it deploy/redis -n roboco -- redis-cli ping

# 4. Check API health
kubectl port-forward svc/roboco-api -n roboco 8000:8000 &
curl http://localhost:8000/health

# 5. Check API endpoints
curl http://localhost:8000/api/v1/agents
curl http://localhost:8000/api/v1/tasks

# 6. Test agent spawning
# Trigger a task that spawns an agent
curl -X POST http://localhost:8000/api/v1/tasks \
-H "Content-Type: application/json" \
-d '{"title": "Test task", "team": "backend"}'

# Watch for agent job
kubectl get jobs -n roboco -w

# 7. Check ArgoCD sync
argocd app get roboco
argocd app sync roboco

# 8. Test GitOps flow
git commit --allow-empty -m "test: trigger deploy"
git push
# Watch ArgoCD sync
```

**Monitoring setup:**
```bash
# Install metrics-server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/late
st/download/components.yaml

# View resource usage
kubectl top pods -n roboco
kubectl top nodes
```

---

## Critical Files to Modify

### Phase 1 Files:
| File | Change |
|------|--------|
| `roboco/mcp/utils.py` | Extract `ApiClient` → SDK |
| `roboco/api/schemas/*.py` | Copy models to SDK |
| `agents/blueprints/` | Genericize and move to OSS |

### Phase 2 Files:
| File | Change |
|------|--------|
| `roboco/runtime/orchestrator.py` | Docker CLI → K8s API |
| `roboco/config.py` | Add K8s settings |
| `docker/orchestrator.Dockerfile` | Add `kubernetes` package |
| NEW: `deploy/` | All K8s manifests |

---

## Git Strategy

**Monorepo approach:**
```
roboco/                     # Private repo
├── roboco/                 # Python source
├── deploy/                 # K8s manifests (ArgoCD watches this)
│   ├── base/
│   └── overlays/production/
└── docker/

roboco-agents/              # Public repo (separate)
├── sdk/
├── cli/
├── blueprints/
└── docs/
```

ArgoCD flow: `git push` → ArgoCD detects → auto-sync → pods updated

---

## Timeline Summary

| Phase | Task | Days |
|-------|------|------|
| **1.1** | Create OSS repo | 1 |
| **1.2** | Extract SDK | 2-3 |
| **1.3** | Create CLI | 2-3 |
| **1.4** | Package blueprints | 1 |
| **1.5** | Restructure private repo | 1-2 |
| | **Phase 1 Total** | **7-10** |
| **2.1** | Setup K8s on NAS | 1-2 |
| **2.2** | Install ArgoCD | 1 |
| **2.3** | NFS storage | 1 |
| **2.4** | K8s manifests | 2-3 |
| **2.5** | RBAC setup | (included) |
| **2.6** | Orchestrator K8s migration | 2-3 |
| **2.7** | Sealed Secrets | 1 |
| **2.8** | ArgoCD apps | 1 |
| **2.9** | Testing & validation | 2-3 |
| | **Phase 2 Total** | **12-16** |
| | **Grand Total** | **19-26 days** |

---

## Dependencies & Prerequisites

### Before Phase 1:
- [ ] GitHub account for `roboco-agents` repo
- [ ] PyPI account for publishing SDK
- [ ] Decide on license (MIT vs Apache 2.0)

### Before Phase 2:
- [ ] UGREEN NAS accessible via SSH
- [ ] NAS has Docker removed or disabled
- [ ] SSH key for GitHub access from NAS
- [ ] Anthropic API key for production

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| K8s learning curve | Start with minimal manifests, iterate |
| Data loss during migration | Keep Docker Compose as fallback |
| Network issues between pods | Use ClusterIP services, test connectivity |
| Secret leakage | Use Sealed Secrets from day 1 |

---

## Success Criteria

### Phase 1 Complete When:
- [ ] `pip install roboco-sdk` works from PyPI
- [ ] `roboco task list` CLI works
- [ ] Community can build agents with templates
- [ ] Private repo has no SDK dependencies

### Phase 2 Complete When:
- [ ] `kubectl get pods -n roboco` shows all healthy
- [ ] Agent jobs spawn and complete successfully
- [ ] `git push` triggers ArgoCD deploy
- [ ] API accessible from outside cluster
- [ ] Zero downtime during rolling updates

---

## Phase 3: Production Hardening

### Step 3.1: Ingress & External Access

```yaml
# deploy/base/ingress/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
name: roboco-ingress
namespace: roboco
annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
spec:
ingressClassName: nginx
tls:
- hosts:
    - api.roboco.local
    secretName: roboco-tls
rules:
- host: api.roboco.local
    http:
    paths:
    - path: /
        pathType: Prefix
        backend:
        service:
            name: roboco-api
            port:
            number: 8000
```

**Install nginx ingress:**
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
--namespace ingress-nginx --create-namespace \
--set controller.service.type=NodePort \
--set controller.service.nodePorts.http=30080 \
--set controller.service.nodePorts.https=30443
```

---

### Step 3.2: Monitoring Stack (Prometheus + Grafana)

```bash
# Install kube-prometheus-stack
helm repo add prometheus-community
https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack \
--namespace monitoring --create-namespace \
--set grafana.adminPassword=your-password \
--set prometheus.prometheusSpec.retention=30d \
--set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageCl
assName=nfs-roboco \
--set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources
.requests.storage=20Gi
```

**ServiceMonitor for RoboCo API:**
```yaml
# deploy/base/monitoring/servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
name: roboco-api
namespace: roboco
spec:
selector:
    matchLabels:
    app: roboco-api
endpoints:
- port: http
    path: /metrics
    interval: 30s
```

**Grafana dashboards to create:**
- API request latency & throughput
- Agent job success/failure rates
- Task lifecycle metrics
- Database connection pool
- Memory/CPU per component

---

### Step 3.3: Log Aggregation (Loki)

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm install loki grafana/loki-stack \
--namespace monitoring \
--set promtail.enabled=true \
--set loki.persistence.enabled=true \
--set loki.persistence.storageClassName=nfs-roboco \
--set loki.persistence.size=20Gi
```

**Add to Grafana datasources** for querying logs like:
```
{namespace="roboco", app="roboco-api"} |= "error"
{namespace="roboco"} | json | level="ERROR"
```

---

### Step 3.4: CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/build-and-push.yaml
name: Build and Push Images

on:
push:
    branches: [main]
    paths:
    - 'roboco/**'
    - 'docker/**'

env:
REGISTRY: ghcr.io
IMAGE_NAME: ${{ github.repository }}

jobs:
build-api:
    runs-on: ubuntu-latest
    permissions:
    contents: read
    packages: write
    steps:
    - uses: actions/checkout@v4

    - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
        registry: ${{ env.REGISTRY }}
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    - name: Build and push API image
        uses: docker/build-push-action@v5
        with:
        context: .
        file: docker/api.Dockerfile
        push: true
        tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}-api:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}-api:latest

    - name: Update Kustomize image tag
        run: |
        cd deploy/overlays/production
        kustomize edit set image ghcr.io/renzof/roboco-api:${{ github.sha }}
        git config user.name "GitHub Actions"
        git config user.email "actions@github.com"
        git add .
        git commit -m "chore: update image to ${{ github.sha }}"
        git push

build-orchestrator:
    runs-on: ubuntu-latest
    # Similar to above...

build-agent:
    runs-on: ubuntu-latest
    # Build base agent image...
```

---

### Step 3.5: Backup & Restore

**PostgreSQL backup CronJob:**
```yaml
# deploy/base/postgres/backup-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
name: postgres-backup
namespace: roboco
spec:
schedule: "0 2 * * *"  # Daily at 2 AM
jobTemplate:
    spec:
    template:
        spec:
        containers:
        - name: backup
            image: postgres:16
            command:
            - /bin/sh
            - -c
            - |
            pg_dump -h postgres -U roboco roboco | gzip >
/backups/roboco-$(date +%Y%m%d).sql.gz
            # Keep last 7 days
            find /backups -name "*.sql.gz" -mtime +7 -delete
            env:
            - name: PGPASSWORD
            valueFrom:
                secretKeyRef:
                name: roboco-secrets
                key: postgres-password
            volumeMounts:
            - name: backups
            mountPath: /backups
        volumes:
        - name: backups
            persistentVolumeClaim:
            claimName: postgres-backups
        restartPolicy: OnFailure
```

**Restore procedure:**
```bash
# 1. Scale down API and orchestrator
kubectl scale deployment roboco-api --replicas=0 -n roboco
kubectl scale deployment roboco-orchestrator --replicas=0 -n roboco

# 2. Restore from backup
kubectl exec -it postgres-0 -n roboco -- bash -c \
"gunzip -c /backups/roboco-20241225.sql.gz | psql -U roboco roboco"

# 3. Scale back up
kubectl scale deployment roboco-api --replicas=2 -n roboco
kubectl scale deployment roboco-orchestrator --replicas=1 -n roboco
```

---

### Step 3.6: Rollback Procedures

**ArgoCD rollback:**
```bash
# View history
argocd app history roboco

# Rollback to previous version
argocd app rollback roboco <revision-number>

# Or via kubectl
kubectl rollout undo deployment/roboco-api -n roboco
kubectl rollout undo deployment/roboco-orchestrator -n roboco
```

**Emergency rollback script:**
```bash
#!/bin/bash
# scripts/emergency-rollback.sh
set -e

REVISION=${1:-1}  # Default: rollback 1 revision

echo "Rolling back roboco-api..."
kubectl rollout undo deployment/roboco-api -n roboco --to-revision=$REVISION

echo "Rolling back roboco-orchestrator..."
kubectl rollout undo deployment/roboco-orchestrator -n roboco
--to-revision=$REVISION

echo "Waiting for rollout..."
kubectl rollout status deployment/roboco-api -n roboco
kubectl rollout status deployment/roboco-orchestrator -n roboco

echo "Rollback complete!"
```

---

## Transition Strategy (Docker → K8s)

### Week 1: Parallel Running
1. Deploy K8s stack alongside existing Docker Compose
2. Point K8s to SAME database (careful!)
3. Test API endpoints, verify responses match
4. Run shadow traffic (duplicate requests to both)

### Week 2: Gradual Cutover
1. Update DNS/ingress to point 10% traffic to K8s
2. Monitor error rates and latency
3. Increase to 50%, then 100%
4. Keep Docker Compose as fallback

### Week 3: Cleanup
1. Verify all agents running on K8s
2. Shut down Docker Compose services
3. Migrate remaining data if any
4. Remove Docker Compose files (keep in git history)

---

## Cost Estimation (Self-Hosted)

| Resource | Usage | Notes |
|----------|-------|-------|
| UGREEN NAS | Already owned | 36TB storage |
| Electricity | ~50W avg | ~$5/month |
| Anthropic API | Variable | Based on agent usage |
| Domain (optional) | $12/year | For external access |
| **Total** | **~$5-10/month** | Excluding API costs |

---

## What's NOT Included (Future)

- [ ] Multi-node K8s cluster (HA)
- [ ] External database (RDS/Cloud SQL)
- [ ] CDN for static assets
- [ ] Rate limiting / API gateway
- [ ] Multi-region deployment
- [ ] Disaster recovery site