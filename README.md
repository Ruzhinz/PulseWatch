# PulseWatch

**PulseWatch** is a real-time hardware monitoring web app that reads system statistics from a continuously updated CSV log file and serves live CPU, GPU, and RAM data via a FastAPI backend and web interface.

---

## 🚀 Features

- 📊 Live CPU, GPU, and RAM monitoring  
- 📄 Reads data directly from CSV log files [HWiNFO](https://www.hwinfo.com)
- ⚡ Light weight  
- 🌐 Web dashboard support  
- 🔌 JSON API endpoint for external integrations  
- 🔄 Automatically detects CSV delimiter (`,` or `;`)  
- 🧠 Smart column detection (no hardcoded column index)  

---

## 🧱 Architecture Overview

```
CSV Log File (HWiNFO)
↓
Persistent File Tail Reader (Thread)
↓
Global Shared State (latest_stats)
↓
FastAPI Server
├── /stats → JSON API
└── / → Web UI (static/index.html)
```

---

## 📁 Project Structure
```
PulseWatch/
│
├── main.py
├── static/
│ └── index.html
└── README.md
```

---

## 📊 Data Collected

### CPU
- Usage (%)
- Clock speed (MHz)
- Power consumption (W)
- Temperature (°C)

### GPU
- Usage (%)
- Clock speed (MHz)
- Power consumption (W)
- Temperature (°C)

### RAM
- Usage percentage (%)
- Used memory (GB)
- Total memory (GB, calculated)

---

## ⚙️ Configuration

Edit these values in `main.py`:

```python
LOG_DIR = "log-here"
INTERVAL = 1.5  # seconds
```
PulseWatch read the log-here/ directory and *automatically* uses any CSV log file found inside it.

- The app waits until a CSV file appears
- If multiple CSV files exist, the newest one is used
- No hard-coded file paths are required

Supports both , and ; CSV delimiters and safely handles log rotation.

---

📦 Requirements
Python

Python 3.9+ recommended

Dependencies

```
pip install fastapi uvicorn
```

## ▶️ How to Run

1. Open [HWiNFO](https://www.hwinfo.com)
2. Click **Start Logging**
3. Set the log output location to your repository folder, for example:
```
PulseWatch/log-here/log.csv
```

4. Open a terminal in the PulseWatch repository
5. Start the server:
```
uvicorn main:app --host 0.0.0.0 --port 8000
```


6. Open in browser:
Web UI:
```
http://localhost:8000
```
Or on another device (PC's IP Address)
```
http://192.168.0.250:8000
```

JSON API:
```
http://localhost:8000/stats
```

---

🔌 API Example Response

```
{
  "cpu": {
    "usage": 12.5,
    "clock": 4200,
    "power": 45.3,
    "temp": 58
  },
  "gpu": {
    "usage": 32,
    "clock": 2500,
    "power": 120,
    "temp": 65
  },
  "ram": {
    "usage_percent": 48,
    "used_gb": 15.4,
    "total_gb": 32
  },
  "raw": {
    "status": "OK"
  }
}
```
🧠 Design Decisions

Persistent file handle to avoid CPU spikes

Threaded monitor loop separated from FastAPI

Heuristic column matching instead of fixed indexes

Safe float parsing for mixed units (MHz, %, °C, W)
