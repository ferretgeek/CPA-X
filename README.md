# CPA-XXX Admin Panel

English | [中文](README_CN.md)

(This panel is entirely a product of vibe coding. Initial build by opus-4.5, subsequent updates by gpt5.2-codex-high)

A monitoring and management panel for **CLIProxyAPI**, with light/dark dual themes, featuring health checks, resource monitoring, log viewing, update management, request statistics, and pricing display.

<img width="2555" height="1230" alt="CPA-XXX" src="https://github.com/user-attachments/assets/07a2ed19-7a93-4e43-9057-3d12f1737188" />
<img width="2554" height="1228" alt="CPA-XXX-2" src="https://github.com/user-attachments/assets/0b1d502f-e66d-4a73-a55e-b50c4e6bf760" />

## Requirements
- **Recommended: Linux** (panel includes `systemctl` functionality)
- Python 3.11+
- Access to CLIProxyAPI management interface (default `http://127.0.0.1:8317`)

> Windows is also supported, but service control and auto-update features (systemd-related) are unavailable.

## Quick Installation

### Option 1: One-Click Install (Recommended)
```bash
# Linux (auto-registers systemd service)
bash scripts/install.sh
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
- `CLIPROXY_PANEL_MANAGEMENT_KEY` (if CLIProxy API has a management key)

#### 4) Start the panel
```bash
python app.py
```

Open your browser and visit:
```
http://127.0.0.1:8080
```

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

## License
MIT License (see `LICENSE`)
