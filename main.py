import os
import time
import threading
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

# --- CONFIGURATION ---
CSV_PATH = r"E:\[TOOLS]\Web-Monitoring\log-hw\1.CSV"
INTERVAL = 1.5  # Seconds between reads

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- GLOBAL STATE ---
latest_stats = {
    "cpu": { "usage": None, "clock": None, "power": None, "temp": None },
    "gpu": { "usage": None, "clock": None, "power": None, "temp": None },
    "ram": { "usage_percent": None, "used_gb": None, "total_gb": None },
    "raw": { "status": "Starting..." }
}

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

# --- THE 0% CPU MONITOR ---
def monitor_persistent():
    print(f"--- WAITING FOR FILE: {CSV_PATH} ---")
    while not os.path.exists(CSV_PATH):
        time.sleep(2)

    # 1. READ HEADERS (Open -> Read -> Close)
    # We do this only ONCE at startup.
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

    # 2. MAP COLUMNS
    idx = {
        'cpu_use': find_idx(headers, [["total", "cpu", "usage"], ["cpu", "total"], ["cpu", "usage"]]),
        'cpu_tmp': find_idx(headers, [["cpu", "tctl"], ["cpu", "package"], ["core", "max"]]),
        'cpu_clk': find_idx(headers, [["core", "clock"], ["bus", "clock"]]),
        'cpu_pwr': find_idx(headers, [["cpu", "package", "power"], ["cpu", "power"]]),
        'gpu_use': find_idx(headers, [["gpu", "core", "load"], ["gpu", "utilization"], ["gpu", "usage"]]),
        'gpu_tmp': find_idx(headers, [["gpu", "temperature"], ["gpu", "temp"]]),
        'gpu_clk': find_idx(headers, [["gpu", "clock"], ["gpu", "core", "clock"]]),
        'gpu_pwr': find_idx(headers, [["gpu", "power"], ["gpu", "ppt"]]),
        'ram_load': find_idx(headers, [["physical", "memory", "load"], ["memory", "usage"]]),
        'ram_used': find_idx(headers, [["physical", "memory", "used"], ["memory", "used"]])
    }

    # 3. FAST LOOP (Keep File Open)
    # 'rb' mode is faster (no encoding overhead)
    f = None
    while True:
        try:
            # Open the file handle if it's closed
            if f is None:
                f = open(CSV_PATH, "rb")
                f.seek(0, 2) # Jump to end immediately

            # SLEEP FIRST (Throttle CPU)
            time.sleep(INTERVAL)

            # Check if file rotated (shrank) or was deleted
            try:
                if os.fstat(f.fileno()).st_size < f.tell():
                    f.close()
                    f = None
                    continue
            except OSError:
                f = None
                continue

            # --- TAIL LOGIC ---
            # 1. Go to End
            f.seek(0, 2)
            file_len = f.tell()
            
            # 2. Go back max 2048 bytes (enough for 1-2 lines)
            read_len = min(file_len, 2048)
            f.seek(-read_len, 1) # 1 = Seek relative to current position (which is end)

            # 3. Read raw bytes
            raw_block = f.read(read_len)
            
            # 4. Decode & Split
            # We ignore errors because we might have cut a multi-byte character in half at the start
            text_block = raw_block.decode("utf-8", errors="ignore")
            lines = text_block.split('\n')

            # 5. Get valid last line
            # The last element is often empty if file ends with \n
            line = ""
            if len(lines) > 1 and lines[-1].strip():
                line = lines[-1]
            elif len(lines) > 2:
                line = lines[-2]
            
            if not line.strip(): continue

            parts = line.split(delimiter)
            if len(parts) < 3: continue

            # --- UPDATE GLOBALS ---
            if idx['cpu_use'] > -1: latest_stats["cpu"]["usage"] = safe_float(parts[idx['cpu_use']])
            if idx['cpu_tmp'] > -1: latest_stats["cpu"]["temp"]  = safe_float(parts[idx['cpu_tmp']])
            if idx['cpu_clk'] > -1: latest_stats["cpu"]["clock"] = safe_float(parts[idx['cpu_clk']])
            if idx['cpu_pwr'] > -1: latest_stats["cpu"]["power"] = safe_float(parts[idx['cpu_pwr']])

            if idx['gpu_use'] > -1: latest_stats["gpu"]["usage"] = safe_float(parts[idx['gpu_use']])
            if idx['gpu_tmp'] > -1: latest_stats["gpu"]["temp"]  = safe_float(parts[idx['gpu_tmp']])
            if idx['gpu_clk'] > -1: latest_stats["gpu"]["clock"] = safe_float(parts[idx['gpu_clk']])
            if idx['gpu_pwr'] > -1: latest_stats["gpu"]["power"] = safe_float(parts[idx['gpu_pwr']])

            r_load = None
            r_used = None
            if idx['ram_load'] > -1:
                r_load = safe_float(parts[idx['ram_load']])
                latest_stats["ram"]["usage_percent"] = r_load
            if idx['ram_used'] > -1:
                r_used = safe_float(parts[idx['ram_used']])
                if r_used and r_used > 100: r_used /= 1024 # Convert MB to GB
                latest_stats["ram"]["used_gb"] = r_used
            if r_used and r_load:
                latest_stats["ram"]["total_gb"] = r_used / (r_load / 100.0)

            latest_stats["raw"]["status"] = f"OK"

        except Exception as e:
            # If any IO error, close handle and retry next loop
            print(f"IO Error: {e}")
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