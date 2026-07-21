# CPA-X Admin Panel (v2.2.0)

English | [中文](README_CN.md)

**AI-first repo**: this project is primarily designed to be deployed and operated by AI agents (not humans).

- AI deployment guide: `AI_DEPLOY_CN.md`
- Agent instructions: `AGENTS.md`
- Release notes: `RELEASE_NOTES_v2.2.0.md`
- Ready-to-download packages: [Latest GitHub Release](https://github.com/ferretgeek/CPA-X/releases/latest)

A monitoring and management panel for **CLIProxyAPI**, featuring health checks, resource monitoring, logs, update management, request statistics, and pricing display.

v2.2.0 focuses on cross-region deployment and long-running stability. Offset-less CLIProxy log timestamps are normalized to UTC API timestamps; `auto` mode infers the log offset even when the panel container and host use different time zones. Updates are downloaded/verified or built in isolation before a short atomic stop/replace/start window, with count, age, and total-size backup limits.

> Current security posture: **all frontend export entries are removed, and main-config writeback is disabled by default**. The config area is read-only / validate-only unless you explicitly set `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` in `.env`.

## Highlights

| Capability | What it provides |
| --- | --- |
| Cross-time-zone reliability | Infers offset-less log time, emits UTC/RFC 3339 APIs, and supports split host/container zones. |
| Safe auto-update | Online preparation, SHA-256 verification, atomic replacement, consecutive health checks, and rollback. |
| Usage and cost insights | Supports CLIProxyAPI v6 cumulative usage and the v7 queue with durable token, cache, reasoning, and cost totals. |
| Long-running stability | Incremental log parsing, atomic persistence, outage backoff, and count/age/size backup caps. |
| Readable interface | Self-contained responsive dark/light UI for desktop, tablet, mobile, and keyboard users. |
| Flexible deployment | Linux/systemd, Windows, and Docker monitoring modes with auto-detection installers. |

## Preview

### Dark Theme
![CPA-X Dark Preview](docs/images/preview-dark.png)

### Light Theme
![CPA-X Light Preview](docs/images/preview-light.png)

### Mobile Layout
![CPA-X Mobile Preview](docs/images/preview-mobile.png)

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
- `CLIPROXY_PANEL_LOG_TIMEZONE` (defaults to `auto`; also accepts `UTC`, `+08:00`, or IANA zones such as `Asia/Shanghai`)
- `CLIPROXY_PANEL_BACKUP_*` / `CLIPROXY_PANEL_UPDATE_REQUIRE_CHECKSUM` (backup caps and update verification)
- `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED` (defaults to `false`; only enable main-config writeback if you explicitly accept that risk)
- `CLIPROXY_PANEL_GITHUB_TOKEN` (optional: higher GitHub rate limit, fewer `latest=unknown`)
- `CLIPROXY_PANEL_PRICING_*` (optional: cost estimation; defaults can be auto-synced from OpenRouter, disable via `CLIPROXY_PANEL_PRICING_AUTO_ENABLED=false`)
- `CLIPROXY_PANEL_QUOTES_PATH` (optional supplemental library; repository-root `X.txt` is always loaded and currently contains 181 entries)

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

CLIProxyAPI v7 uses the short-lived `/v0/management/usage-queue`. CPA-X detects it automatically, consumes it every 15 seconds, and persists the deltas. Set `usage-statistics-enabled: true` in CLIProxyAPI's `config.yaml`, or no token records will be emitted. The cumulative v6 endpoint remains supported.

### 2) Health check timeout
Use the unauthenticated minimal `/api/healthz` endpoint for container/load-balancer liveness checks. Use `/api/health` for full diagnostics.

### 3) systemd features not working
This is a Linux-only feature. On Windows, it will fail gracefully without affecting panel startup.

## Security Notes
- **Do not commit `.env` to the repository** (already in `.gitignore`)
- Keep management keys and model keys only in `.env`
- Default bind host is `0.0.0.0` for LAN-friendly deployment. If you only use the panel locally, set `CLIPROXY_PANEL_BIND_HOST=127.0.0.1`
- For an extra protection layer, set `CLIPROXY_PANEL_PANEL_ACCESS_KEY` (`/api/*` accepts only the `X-Panel-Key` header; the browser URL parameter is consumed only for one-time local setup and immediately removed)
- Cross-origin API access is disabled by default; only set the comma-separated `CLIPROXY_PANEL_CORS_ORIGINS` when needed
- All frontend export entries are removed to avoid exposing sensitive data through browser download links
- Main-config writeback is disabled by default; only set `CLIPROXY_PANEL_CONFIG_WRITE_ENABLED=true` if you explicitly accept that risk

## Development checks

```bash
pip install -r requirements-dev.txt
python -m pytest
ruff check app.py scripts tests
```

## Community

- [Contributing guide](CONTRIBUTING.md)
- [Support](SUPPORT.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Report an issue](https://github.com/ferretgeek/CPA-X/issues/new/choose)
- [Discussions](https://github.com/ferretgeek/CPA-X/discussions)

## License
MIT License (see `LICENSE`)
