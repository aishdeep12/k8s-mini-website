# k8s-mini-website

A tiny Flask website, plus an **OCR feature that runs in separate worker pods**, taken all the way from `git push` to pods running on your own Kubernetes cluster.

## What runs in the cluster

```
Browser
  │  GET /                 POST /api/ocr (image)          GET /api/jobs/<id>  (polls)
  ▼
Service mini-web (NodePort 30080)   ── load-balances across ──┐
                                                               ▼
 ┌──────────── web pods ×3 (gunicorn + Flask) ─────────────┐
 │ 1. save image in Redis   2. queue job   3. reply job_id │
 └──────────────────────────────┬──────────────────────────┘
                                ▼
                   Redis pod  (Service "redis:6379")
                   ├─ queue "ocr"       ← jobs waiting
                   └─ job:<id> hashes   ← status + results
                                ▲
                                │  pull job, run Tesseract, save text
 ┌──────── OCR worker pods ×2 (dramatiq tasks) ────────────┐
 │ same Docker image as the web pods, different command     │
 │ no Service: nobody calls them, they pull from the queue  │
 └──────────────────────────────────────────────────────────┘
```

On the page you'll see **which web pod queued your job** and **which OCR pod processed it**. They're different pods, which shows the work really moved across the cluster.

## How code gets there

```
 you: git push
      │
      ▼
 GitHub Actions
  1. test            pytest (incl. a real Tesseract OCR test)
  2. build & smoke   docker build → kind cluster → deploy everything → curl site + OCR a test image → push image
  3. update-manifest bump image tag in k8s/kustomization.yaml, commit to Git
      │                                  │
      ▼                                  ▼
 ghcr.io (image)               GitHub repo (k8s/ folder)
      │                                  │
      │        Argo CD (inside your cluster) watches the repo
      └────────── pulls image ◄── applies the new manifests ──► rolling update of web + OCR pods
```

GitHub's cloud runners can't reach a cluster on your laptop. So the pipeline only writes to Git and the registry (**CI**), and Argo CD inside your cluster *pulls* the change (**CD**). That's the GitOps pattern.

## What's in the repo

| Path | What it is |
|---|---|
| `app/main.py` | Flask web app: the page, `/healthz`, `/api/info`, **`POST /api/ocr`**, **`GET /api/jobs/<id>`** |
| `app/tasks.py` | The Dramatiq **`run_ocr` job**, executed by the OCR worker pods |
| `app/ocr_engine.py` | The OCR itself (Tesseract). Swap in Mistral OCR, PyMuPDF, a VLM… |
| `app/store.py` | Redis connection, file storage and job status helpers |
| `app/templates/index.html` | Page with version/pod info and the OCR upload box |
| `tests/` | pytest: web, OCR flow with an in-memory queue and fake Redis, real Tesseract on `fixtures/hello.png` |
| `Dockerfile` | One image for both roles (Python + Tesseract). Default command = gunicorn web server |
| `docker-compose.yaml` | Run web + worker + Redis locally without Kubernetes |
| `k8s/deployment.yaml` | Web Deployment: 3 replicas, probes, limits |
| `k8s/service.yaml` | Web Service: NodePort 30080 |
| `k8s/redis.yaml` | Redis Deployment + Service (the queue) |
| `k8s/ocr-worker.yaml` | **OCR worker Deployment**: 2 replicas, command `dramatiq tasks`, no Service |
| `k8s/kustomization.yaml` | Bundles everything and sets the image tag (the pipeline edits this) |
| `.github/workflows/ci-cd.yaml` | The pipeline |
| `argocd/application.yaml` | Tells Argo CD to keep the cluster in sync with `k8s/` |

### How one OCR request flows

1. You upload `scan.png`. The Service sends the request to, say, **web pod A**.
2. Web pod A stores the image in Redis (`file:<id>`, expires in 1 hour), writes `job:<id> = queued`, calls `run_ocr.send(id)`, and **immediately** replies `202 {job_id}`. It doesn't wait for OCR.
3. `send()` only pushed a small message onto the Redis queue `ocr`.
4. **OCR worker pod X** is waiting on that queue. It takes the message, sets `running`, reads the image from Redis, runs Tesseract, saves `done` + text, and deletes the image.
5. The browser polls `GET /api/jobs/<id>` every second. These polls may hit web pods B or C; that's fine, because they all read the same Redis.
6. If the worker crashes mid-job, Dramatiq re-queues it (`max_retries=3`), and another worker picks it up.

> Why is the image stored in Redis? **Pods don't share disks.** A file saved on web pod A's disk is invisible to OCR pod X. Redis is a quick shared store for small files (max 5 MB here). In production, upload to S3/MinIO/blob storage and pass only the key in the job.

---

## Prerequisites

- Docker Desktop with **Kubernetes enabled**. `kubectl get nodes` should show `docker-desktop   Ready`.
- Git, and a GitHub account (`aishdeep12`).
- PowerShell. All commands below are PowerShell.

> If your GitHub username or repo name differs, change it in **3 places**: `Dockerfile` (LABEL), `k8s/kustomization.yaml` (`newName`), and `argocd/application.yaml` (`repoURL`).

---

## Step 0 (optional): run it locally

**Quickest, the whole system with Docker Compose** (web + 2 OCR workers + Redis):

```powershell
docker compose up --build --scale worker=2
```

Open **http://localhost:5000** and upload an image with some text (try `tests/fixtures/hello.png`). `Ctrl+C` to stop, then `docker compose down`.

**Run the unit tests on Windows** (no Redis or Tesseract needed; the real-OCR test is skipped if Tesseract isn't installed):

```powershell
pip install -r app/requirements.txt -r requirements-dev.txt
pytest -q
```

**Run as pods on Docker Desktop Kubernetes, without GitHub:**

```powershell
kubectl config use-context docker-desktop
docker build -t mini-web:local --build-arg APP_VERSION=local .
kubectl apply -k k8s/
kubectl -n mini-web set image deployment/mini-web web=mini-web:local
kubectl -n mini-web set image deployment/ocr-worker worker=mini-web:local
kubectl -n mini-web get pods -w          # wait for redis, 3 mini-web and 2 ocr-worker pods: Running
```

Open **http://localhost:30080**. When done: `kubectl delete namespace mini-web`. Do this before Step 5 so Argo CD starts clean.

## Step 1: Create the GitHub repo and push

1. On GitHub, create a **new public repo** named `k8s-mini-website`. Leave it empty.
2. In the project folder:

```powershell
git init
git add .
git commit -m "Mini website + OCR workers + CI/CD + k8s"
git branch -M main
git remote add origin https://github.com/aishdeep12/k8s-mini-website.git
git push -u origin main
```

## Step 2: Watch the pipeline

Open the repo, go to the **Actions** tab, and open **CI/CD**:

1. **test**: unit tests, including real Tesseract OCR on the test image.
2. **build-and-smoke-test**: builds the image, starts a throwaway Kubernetes cluster (kind) on the runner, deploys Redis + web + OCR workers, checks the site, **uploads `hello.png` and waits until an OCR pod returns "HELLO KUBERNETES"**, then pushes the image to `ghcr.io`.
3. **update-manifest**: commits the new `newTag:` to `k8s/kustomization.yaml`. That's the deploy signal.

If `update-manifest` fails with a permission error, go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions**, save, and re-run.

## Step 3: Make the image public (one time)

Go to your GitHub profile, then **Packages → k8s-mini-website → Package settings → Change visibility → Public**.

## Step 4: Install Argo CD in your cluster

```powershell
kubectl config use-context docker-desktop
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd rollout status deployment/argocd-server
kubectl -n argocd port-forward svc/argocd-server 8443:443      # leave this running
```

In a **new** terminal, get the admin password:

```powershell
$p = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}"
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
```

Go to https://localhost:8443 (accept the certificate warning) and log in as `admin`.

## Step 5: Connect Argo CD to your repo

```powershell
kubectl apply -f argocd/application.yaml
kubectl -n mini-web get pods -o wide
```

In Argo CD you'll see the tree: **redis**, **mini-web** (3 pods) and **ocr-worker** (2 pods). Open **http://localhost:30080** 🎉 and upload an image.

## Step 6: The end-to-end loop

1. Change the headline in `app/templates/index.html`, or the OCR code in `app/ocr_engine.py`.
2. `git add . ; git commit -m "change" ; git push`
3. Watch it roll out: `kubectl -n mini-web get pods -w`. Both web and OCR pods are replaced, since they share the image.
4. Click **Refresh** in Argo CD to sync immediately, instead of waiting about 3 minutes.

---

## Things to try

| Try this | How | What you'll see |
|---|---|---|
| Work moves between pods | Upload an image | "Queued by" and "Processed by" show different pod names |
| Load balancing | `1..10 \| % { (curl.exe -s localhost:30080/api/info \| ConvertFrom-Json).pod }` | Different web pods answering |
| Watch a worker take a job | `kubectl -n mini-web logs -l app=ocr-worker -f`, then upload | The worker's log lines as it processes |
| Scale OCR only | `kubectl -n mini-web scale deploy/ocr-worker --replicas=4` | 4 OCR pods; web pods untouched. (Argo CD's selfHeal puts it back to 2. To keep it, change `replicas` in `k8s/ocr-worker.yaml` and push.) |
| Queue backs up | `kubectl -n mini-web scale deploy/ocr-worker --replicas=0`, upload, then scale back to 2 | Job sits on "queued", then gets processed as soon as workers return |
| Peek inside Redis | `kubectl -n mini-web exec deploy/redis -- redis-cli keys "*"` | `job:…` hashes and the `dramatiq:ocr` queue |
| Self-healing | `kubectl -n mini-web delete pod <ocr-worker-pod>` | A replacement starts immediately |
| Inspect a pod | `kubectl -n mini-web describe pod <name>` | Node, IP, command, resources, events |
| Rollback | `git revert HEAD ; git push` | The previous version is redeployed |
| Break the build | Make a test fail and push | The pipeline stops; nothing is deployed |

## Clean up

```powershell
kubectl delete -f argocd/application.yaml
kubectl delete namespace mini-web
kubectl delete namespace argocd
```

## Next steps

- **Autoscale workers on queue length** with KEDA: 0 OCR pods when idle, more when the `dramatiq:ocr` queue grows.
- **Real file storage:** add MinIO to the cluster and pass object keys instead of storing images in Redis.
- **Swap the OCR engine:** replace `ocr_engine.extract_text` with Mistral OCR or a VLM; nothing else changes.
- **Helm**, **Ingress**, or a **Jenkins** version of the same pipeline.
