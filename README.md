# Filament Tracker

A local-network web dashboard for tracking filament spool usage on Bambu Lab printers. Connects to Bambu Cloud MQTT to monitor your AMS in real time, persists spool history in SQLite including remaining fillament, and serves a clean web UI accessible from any device on your network.

## Pictures

<img width="2550" height="1287" alt="Screenshot 2026-03-09 175327" src="https://github.com/user-attachments/assets/43d4736e-9fa8-4f49-82fb-b66c7b9a9747" />
<img width="1739" height="386" alt="Screenshot 2026-03-08 140504" src="https://github.com/user-attachments/assets/599e8883-7ad9-4ffc-90e9-bd2ab6eae46b" />
<img width="658" height="1105" alt="Screenshot 2026-03-09 175349" src="https://github.com/user-attachments/assets/253a99f6-eb8f-4463-975c-00ec768a2d82" />


## How It Works

```
Bambu Printer ──MQTT──> Filament Tracker (Python) ──> Web UI (http://your-server:5000)
                              │
                              └── SQLite database (spool history, weights, alerts)
```

1. **Filament Tracker** connects to Bambu Cloud MQTT and monitors AMS data
2. Every spool that enters the AMS is recorded with its type, color, weight, and RFID data
3. Remaining filament is tracked over time as prints consume material
4. A **web dashboard** shows current AMS status, full spool inventory, usage charts, and low-stock alerts

## Features

- **Real-time AMS view** — see what's loaded in each AMS slot with live remaining weight
- **Spool inventory** — every spool ever loaded is tracked, sortable by weight, material, or last seen
- **Usage history charts** — per-spool usage graphed over time
- **Low filament alerts** — configurable threshold with web UI and optional FCM push notifications
- **Weight offset** — apply a +/- gram correction per spool if the printer's reported weight doesn't match reality
- **Non-RFID spool support** — third-party spools without RFID tags are detected and tracked
- **Custom names & notes** — label your spools and add notes via the web UI
- **Test mode** — preview the full UI with mock data, no printer or MQTT needed
- **Standalone or integrated** — runs on its own, or alongside [BambuNowBar](https://github.com/YOUR_USERNAME/BambuNowBar) sharing a single MQTT connection

## Requirements

- **Printer**: Any Bambu Lab printer with AMS connected to Bambu Cloud
- **Server**: Any always-on machine — Raspberry Pi, home server, WSL, VPS, etc.
- **Python**: 3.9+
- **Network**: Server and browser device on the same local network

## Quick Start

```bash
# Clone the repo
git clone https://github.com/EBTEAM3/FilamentTracker.git
cd FilamentTracker

# Install dependencies
pip3 install -r requirements.txt

# Create your config
cp config.example.py config.py
nano config.py  # fill in your Bambu credentials
```

### Finding Your Bambu Credentials

You need your **User ID**, **Access Token**, and **Printer Serial Number**.

```bash
pip3 install requests
python3 get_credentials.py
```

This will prompt for your Bambu Lab email and password, handle 2FA, and output your credentials ready to paste into `config.py`.

Alternatively, find them manually in Bambu Studio's config files — see the [BambuNowBar README](https://github.com/YOUR_USERNAME/BambuNowBar#part-1-finding-your-bambu-credentials) for details.

### Configure

Edit `config.py` with your values:

```python
BAMBU_USER_ID = "YOUR_NUMERIC_USER_ID"
BAMBU_ACCESS_TOKEN = "YOUR_AAD_TOKEN_HERE"
BAMBU_PRINTER_SERIAL = "YOUR_PRINTER_SERIAL"
```

Optional settings:

```python
FILAMENT_TRACKER_PORT = 5000         # Web UI port
FILAMENT_LOW_ALERT_GRAMS = 150      # Low stock threshold (0 to disable)
```

### Run

```bash
python3 filament_tracker.py
```

Open `http://your-server-ip:5000` in any browser on your network.

### Test Mode (No Printer Needed)

```bash
python3 filament_tracker.py --test
```

Starts the web UI with mock spool data so you can preview the dashboard without an MQTT connection.

## Weight Offset

The AMS reports spool weight based on the RFID chip data and rotation tracking. This can sometimes be inaccurate. The weight offset feature lets you apply a permanent +/- gram correction to any spool.

1. Click a spool in the web UI to open its detail view
2. Set the **Weight Offset** field (e.g., `-50` if the printer over-reports by 50g, or `+30` if it under-reports)
3. Click **Save Changes**

The offset is stored per spool ID and applied to all weight calculations, including low-stock alerts.

## BambuNowBar Integration (Optional)

If you also use [BambuNowBar](https://github.com/YOUR_USERNAME/BambuNowBar) for push notifications, you can run both services on a single MQTT connection.

Clone both repos as sibling folders:

```
YourFolder/
  BambuNowBar/
  FilamentTracker/
```

**Option A** — Run from BambuNowBar (recommended if you already have it set up):

Set `ENABLE_FILAMENT_TRACKER = True` in BambuNowBar's `config.py` and run `bambu_fcm_bridge.py`.

**Option B** — Run from FilamentTracker:

Set `ENABLE_NOTIFICATIONS = True` in FilamentTracker's `config.py`, fill in the Firebase/FCM settings, copy `firebase-service-account.json` into this folder, and run `filament_tracker.py`.

The config file format is the same in both projects, so you can copy `config.py` directly between them.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/spools` | All tracked spools |
| GET | `/api/spools/active` | Spools currently in AMS |
| GET | `/api/spools/<id>` | Spool detail with usage history |
| GET | `/api/spools/<id>/history` | Usage history for a spool |
| PATCH | `/api/spools/<id>` | Update custom_name, notes, remain_percent, or weight_offset |
| DELETE | `/api/spools/<id>` | Delete a spool and its history |
| GET | `/api/status` | Printer connection status |
| GET | `/api/alerts` | Active low-stock alerts |
| DELETE | `/api/alerts/<id>` | Dismiss an alert |
| GET | `/api/settings/alert_threshold` | Current alert threshold |
| POST | `/api/settings/alert_threshold` | Update alert threshold |

## Running as a System Service

For 24/7 operation, create a systemd service:

```ini
# /etc/systemd/system/filament-tracker.service
[Unit]
Description=Filament Tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/FilamentTracker
ExecStart=/usr/bin/python3 filament_tracker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp filament-tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable filament-tracker
sudo systemctl start filament-tracker
```

## Project Structure

```
FilamentTracker/
├── filament_tracker.py       # Main application (Flask + SQLite + AMS processing)
├── bambu_mqtt.py             # Shared MQTT module (printer state + callbacks)
├── get_credentials.py        # Bambu credential helper
├── config.example.py         # Configuration template
├── config.py                 # Your config (NOT in repo)
├── requirements.txt          # Python dependencies
├── templates/
│   └── index.html            # Web UI template
├── static/
│   ├── app.js                # Frontend JavaScript
│   └── style.css             # Dark theme stylesheet
├── filament_tracker.db       # SQLite database (created at runtime, NOT in repo)
└── README.md
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "config.py not found!" | Copy `config.example.py` to `config.py` and fill in your values |
| "No module named 'flask'" | Run `pip3 install -r requirements.txt` |
| Port 5000 blocked | Change `FILAMENT_TRACKER_PORT` in config.py (try 5001) |
| MQTT connection failed | Check your Bambu credentials. Ensure port 8883 is not blocked |
| No spools appearing | Load filament into the AMS — spools appear when the printer reports AMS data |
| Non-RFID spools show 100% | The AMS cannot measure weight without RFID. Only RFID spools track remaining % |

## Security Notes

- `config.py` and `filament_tracker.db` are `.gitignore`'d and never committed
- Never share your Bambu access token — it grants full access to your printer
- The web UI has no authentication — it's designed for trusted local networks only



