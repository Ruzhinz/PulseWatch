import os
import time
import threading
import subprocess
import platform
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

# --- CONFIGURATION ---
# Use "." if the log file is in the same folder as this script
LOG_DIR = "log-here" 
INTERVAL = 1.5  # Seconds between reads

app = FastAPI()
# Create static folder if missing
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- GLOBAL STATE ---
latest_stats = {
    "cpu": { "usage": 0, "clock": 0, "power": 0, "temp": 0 },
    "gpu": { "usage": 0, "clock": 0, "power": 0, "temp": 0 },
    "ram": { "usage_percent": 0, "used_gb": 0, "total_gb": 0 },
    "info": { "cpu_name": "Detecting...", "gpu_name": "Detecting...", "ram_type": "DDR-UNK" },
    "raw": { "status": "Starting..." }
}

# --- NEW FUNCTION: DETECT HW NAMES (Run once at start) ---
def get_hardware_names_wmic():
    c_name, g_name = "Generic CPU", "Generic GPU"
    try:
        if platform.system() == "Windows":
            # CPU
            cmd = "wmic cpu get name"
            out = subprocess.check_output(cmd, shell=True).decode().strip().split('\n')
            if len(out) > 1: c_name = out[1].strip()
            
            # GPU
            cmd = "wmic path win32_VideoController get name"
            out = subprocess.check_output(cmd, shell=True).decode().strip().split('\n')
            gpus = [x.strip() for x in out[1:] if x.strip()]
            if gpus: g_name = gpus[0]
    except Exception as e:
        print(f"Name Detect Error: {e}")
    return c_name, g_name

# --- PARSING HELPERS ---
def safe_float(v):
    if not v: return None
    try:
        s = str(v).replace("MHz", "").replace("%", "").replace("°C", "").replace("W", "").strip()
        if "," in s and "." not in s: s = s.replace(",", ".")
        elif "," in s and "." in s:   s = s.replace(",", "")
        return float(s)
    except:
        return None

def find_idx(headers, keyword_sets):
    headers_lower = [h.lower() for h in headers]
    for keywords in keyword_sets:
        for i, h in enumerate(headers_lower):
            if all(k in h for k in keywords): return i
    return -1

def find_latest_csv(log_dir):
    if not os.path.exists(log_dir): return None
    csv_files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.lower().endswith(".csv")]
    if not csv_files: return None
    return max(csv_files, key=os.path.getmtime)

# --- THE 0% CPU MONITOR (With your logic) ---
def monitor_persistent():
    # 1. Detect Names (Outside the loop, 0 cost)
    c_name, g_name = get_hardware_names_wmic()
    latest_stats["info"]["cpu_name"] = c_name
    latest_stats["info"]["gpu_name"] = g_name
    print(f"--- HARDWARE: {c_name} | {g_name} ---")

    print(f"--- WAITING FOR CSV FILE IN: {os.path.abspath(LOG_DIR)} ---")
    CSV_PATH = None

    while CSV_PATH is None:
        CSV_PATH = find_latest_csv(LOG_DIR)
        time.sleep(2)

    print(f"--- USING LOG FILE: {CSV_PATH} ---")

    # 2. READ HEADERS (Open -> Read -> Close)
    headers = []
    delimiter = ","
    try:
        with open(CSV_PATH, "r", encoding="utf-8", errors="ignore") as f:
            header_line = f.readline().strip()
            if header_line.count(";") > header_line.count(","): delimiter = ";"
            headers = header_line.split(delimiter)
            print(f"--- HEADERS LOADED ({len(headers)} cols) ---")
    except Exception as e:
        print(f"Header Error: {e}")
        return

    # 3. MAP COLUMNS (Added 'ram_spd' to your list)
    idx = {
        'cpu_use': find_idx(headers, [["total", "cpu", "usage"], ["cpu", "total"], ["cpu", "usage"]]),
        'cpu_tmp': find_idx(headers, [["cpu", "tctl"], ["cpu", "package"], ["core", "max"], ["cpu", "temp"]]),
        'cpu_clk': find_idx(headers, [["core", "clock"], ["bus", "clock"]]),
        'cpu_pwr': find_idx(headers, [["cpu", "package", "power"], ["cpu", "power"]]),
        
        'gpu_use': find_idx(headers, [["gpu", "core", "load"], ["gpu", "utilization"], ["gpu", "usage"]]),
        'gpu_tmp': find_idx(headers, [["gpu", "temperature"], ["gpu", "temp"]]),
        'gpu_clk': find_idx(headers, [["gpu", "clock"], ["gpu", "core", "clock"]]),
        'gpu_pwr': find_idx(headers, [["gpu", "power"], ["gpu", "ppt"]]),
        
        'ram_load': find_idx(headers, [["physical", "memory", "load"], ["memory", "usage"]]),
        'ram_used': find_idx(headers, [["physical", "memory", "used"], ["memory", "used"]]),
        'ram_spd':  find_idx(headers, [["memory", "clock"], ["dram", "frequency"]]) # New
    }

    # 4. FAST LOOP (Your exact logic)
    f = None
    while True:
        try:
            # Open file if closed
            if f is None:
                f = open(CSV_PATH, "rb")
                f.seek(0, 2)

            # SLEEP FIRST (Crucial for 0% CPU)
            time.sleep(INTERVAL)

            # Check rotation
            try:
                if os.fstat(f.fileno()).st_size < f.tell():
                    f.close(); f = None; continue
            except OSError:
                f = None; continue

            # TAIL LOGIC
            f.seek(0, 2)
            file_len = f.tell()
            read_len = min(file_len, 4096) # Read last 4kb
            f.seek(-read_len, 1)

            raw_block = f.read(read_len)
            text_block = raw_block.decode("utf-8", errors="ignore")
            lines = text_block.split('\n')

            # Get valid last line
            line = ""
            if len(lines) > 1 and lines[-1].strip(): line = lines[-1]
            elif len(lines) > 2: line = lines[-2]
            
            if not line.strip(): continue

            parts = line.split(delimiter)
            if len(parts) < 3: continue

            # --- UPDATE GLOBALS ---
            # Helper to reduce repetition
            def get_val(key):
                i = idx.get(key, -1)
                if i > -1 and i < len(parts): return safe_float(parts[i])
                return 0.0

            latest_stats["cpu"]["usage"] = get_val('cpu_use')
            latest_stats["cpu"]["temp"]  = get_val('cpu_tmp')
            latest_stats["cpu"]["clock"] = get_val('cpu_clk')
            latest_stats["cpu"]["power"] = get_val('cpu_pwr')

            latest_stats["gpu"]["usage"] = get_val('gpu_use')
            latest_stats["gpu"]["temp"]  = get_val('gpu_tmp')
            latest_stats["gpu"]["clock"] = get_val('gpu_clk')
            latest_stats["gpu"]["power"] = get_val('gpu_pwr')

            r_load = get_val('ram_load')
            r_used = get_val('ram_used')
            
            latest_stats["ram"]["usage_percent"] = r_load
            
            # HWiNFO sometimes gives MB, sometimes GB
            if r_used > 512: r_used /= 1024 
            latest_stats["ram"]["used_gb"] = r_used
            
            if r_used and r_load:
                latest_stats["ram"]["total_gb"] = r_used / (r_load / 100.0)

            # NEW: RAM Speed/DDR Calc
            r_clk = get_val('ram_spd')
            if r_clk > 0:
                mt = int(r_clk * 2) # DDR = Double Rate
                ddr = "DDR5" if mt > 4600 else "DDR4"
                latest_stats["info"]["ram_type"] = f"{ddr}-{mt}"

            latest_stats["raw"]["status"] = "LIVE"

        except Exception as e:
            print(f"Loop Error: {e}")
            if f: 
                try: f.close()
                except: pass
            f = None
            time.sleep(1)

# --- SERVER ---
@app.on_event("startup")
def start():
    t = threading.Thread(target=monitor_persistent, daemon=True)
    t.start()

@app.get("/stats")
def get_stats():
    return JSONResponse(latest_stats)

@app.get("/")
def index():
    return FileResponse("static/index.html")