# CPA-X Admin Panel (v2.1.0)

English | [中文](README_CN.md)

**AI-first repo**: this project is primarily designed to be deployed and operated by AI agents (not humans).

- AI deployment guide: `AI_DEPLOY_CN.md`
- Agent instructions: `AGENTS.md`
- Release notes: `RELEASE_NOTES_v2.1.0.md`

A monitoring and management panel for **CLIProxyAPI**, featuring health checks, resource monitoring, logs, update management, request statistics, and pricing display.

> Current security posture: **all frontend export entries are removed, and main-config writeback is disabled by default**. The config area is read-only / validate-only unless you explicitly set `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` in `.env`.

<img width="2555" height="1230" alt="CPA-X" src="https://github.com/user-attachments/assets/07a2ed19-7a93-4e43-9057-3d12f1737188" />
<img width="2554" height="1228" alt="CPA-X-2" src="https://github.com/user-attachments/assets/0b1d502f-e66d-4a73-a55e-b50c4e6bf760" />

## Requirements
- **Recommended: Linux** (panel includes `systemctl` functionality)
- Python 3.11+
- Access to CLIProxyAPI management interface (default `http://127.0.0.1:8317`)

> Windows is also supported, but service control and auto-update features (systemd-related) are unavailable.

## Quick Installation

### Option 1: One-Click Install (Recommended)
```bash
# Linux (auto-registers systemd service; installer best-effort auto-detects and fills `.env`)
bash scripts/install.sh

# Optional: run auto-detect again (recommended)
python3 scripts/doctor.py --write-env
```

```powershell
# Windows (background start)
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
```

### Option 2: Manual Installation

#### 1) Clone the repository
```bash
git clone https://github.com/ferretgeek/CPA-X.git
cd CPA-X
```

#### 2) Create virtual environment and install dependencies
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

#### 3) Configure environment variables
Copy the example file and modify as needed:
```bash
# Windows
copy .env.example .env
# Linux / macOS
cp .env.example .env
```

Key configurations:
- `CLIPROXY_PANEL_CLIPROXY_DIR` / `CLIPROXY_PANEL_CLIPROXY_CONFIG`
- `CLIPROXY_PANEL_CLIPROXY_LOG`
- `CLIPROXY_PANEL_CLIPROXY_API_BASE` / `CLIPROXY_PANEL_CLIPROXY_API_PORT`
- `CLIPROXY_PANEL_MANAGEMENT_KEY` / `CLIPROXY_PANEL_MODELS_API_KEY` (if upstream keys are enabled)
- `CLIPROXY_PANEL_CLIPROXY_SERVICE` / `CLIPROXY_PANEL_CLIPROXY_BINARY` (required for auto-update)
- `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED` (defaults to `false`; only enable main-config writeback if you explicitly accept that risk)
- `CLIPROXY_PANEL_GITHUB_TOKEN` (optional: higher GitHub rate limit, fewer `latest=unknown`)
- `CLIPROXY_PANEL_PRICING_*` (optional: cost estimation; defaults can be auto-synced from OpenRouter, disable via `CLIPROXY_PANEL_PRICING_AUTO_ENABLED=false`)

#### 4) Start the panel
```bash
python app.py
```

Open your browser and visit:
```
http://127.0.0.1:8080
```

## Docker / Container Deployment (Optional)

Good for: monitoring & read-only operations (status/stats/models/logs/config reads).  
Not good for: auto-update / service control (containers typically don't have systemd or host privileges).

This repo includes:
- `Dockerfile`
- `docker-compose.yml`
- `.env.docker.example`

Shortest path (compose):
```bash
docker compose up -d --build
```

If you need logs/config/auth file features, follow the comments in `docker-compose.yml` to mount host files/directories and point `CLIPROXY_PANEL_*` paths to container paths. Note that config access is read-only by default and does not write back to the host config.

## FAQ

### 1) Page loads but data is empty
Check if CLIProxy is running and verify that `CLIPROXY_PANEL_CLIPROXY_API_BASE/PORT` in `.env` points to the correct address.

### 2) Health check timeout
`/api/status` triggers additional checks and may be slow on first load. Try `/api/resources` first to verify service accessibility.

### 3) systemd features not working
This is a Linux-only feature. On Windows, it will fail gracefully without affecting panel startup.

## Security Notes
- **Do not commit `.env` to the repository** (already in `.gitignore`)
- Keep management keys and model keys only in `.env`
- Default bind host is `0.0.0.0` for LAN-friendly deployment. If you only use the panel locally, set `CLIPROXY_PANEL_BIND_HOST=127.0.0.1`
- For an extra protection layer, set `CLIPROXY_PANEL_PANEL_ACCESS_KEY` (then `/api/*` requires `X-Panel-Key` or URL query `panel_key`)
- All frontend export entries are removed to avoid exposing sensitive data through browser download links
- Main-config writeback is disabled by default; only set `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` if you explicitly accept that risk

## License
MIT License (see `LICENSE`)
