# Deploying TapTrace

TapTrace is one Docker container that serves the REST API and the demo UI together.
- The embedder (23 MB INT8 ONNX) and the pre-warmed cache are baked into the image.
- It runs on a free CPU instance and needs about 150 MB of RAM.
- It listens on `$PORT` (default 8000), so any Docker host works.

## Option A: Render (recommended, free, deploys from GitHub)

1. Push the repository to GitHub.
2. Sign in at https://render.com with GitHub.
3. Click **New + → Blueprint** and select the repository. Render reads `render.yaml`, which sets Docker, the free plan and `/health` as the health check.
   - Or use **New + → Web Service** → pick the repo → Language **Docker** → Instance **Free** → Health Check Path `/health`.
4. When asked for environment variables, set `GROQ_API_KEY` to your key.
   - Without a key, set `TAPTRACE_OFFLINE=1` instead. The 20 scenarios are still served from the cache, and new complaints use the deterministic path.
5. Click **Apply** / **Create**. The first build takes about 3–5 minutes.
6. You get a URL like `https://taptrace-xxxx.onrender.com`:
   - `/` is the demo;
   - `/docs` is the interactive API;
   - `POST /v1/troubleshoot` is the endpoint.

Notes:
- Free instances sleep after 15 minutes idle, and the first request after that takes about a minute. Open the URL once before a demo or judging.
- Plans cached at runtime are lost on restart or redeploy. The pre-warmed cache shipped in the image always remains.
- The public URL spends your Groq free-tier quota. It is rate-limited and not billed, and the engine falls back to the deterministic path when the limit is hit.

## Option B: Hugging Face Spaces (free, always on while in use, 16 GB RAM)

1. Create a new Space: SDK **Docker**, template **Blank**, hardware **CPU basic (free)**.
2. Under Settings → Variables and secrets, add the secret `GROQ_API_KEY`.
3. Push this repository's files to the Space's git repo.
4. At the top of the Space's `README.md`, keep this header:
   ```yaml
   ---
   title: TapTrace
   sdk: docker
   app_port: 8000
   ---
   ```
5. The Space builds the Dockerfile. The app is then at `https://<user>-taptrace.hf.space`.

## Option C: any VM or Docker host (Railway, Fly.io, Google Cloud Run, an EC2 box)

```bash
docker build -t taptrace .
docker run -d -p 80:8000 -e GROQ_API_KEY=... --restart unless-stopped taptrace
```

- Railway and Cloud Run inject `PORT` themselves. The Dockerfile honours it, so no change is needed.
- To check a deployment:
  - `curl https://<host>/health` should return `{"status":"ok"}`.
  - Then open `https://<host>/` for the demo.
