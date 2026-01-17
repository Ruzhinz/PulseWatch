import os
import time
import threading
import subprocess
import platform
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse

# --- CONFIGURATION ---
LOG_DIR = "log-here"  # "." means current folder. Change if needed.
INTERVAL = 1.5  # Seconds between reads (Higher = Lower CPU)

app = FastAPI()
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- GLOBAL STATE ---
latest_stats = {
    "cpu": { "usage": 0, "clock": 0, "power": 0, "temp": 0 },
    "gpu": { "usage": 0, "clock": 0, "power": 0, "temp": 0 },
    "ram": { "usage_percent": 0, "used_gb": 0, "total_gb": 0 },
    "info": { "cpu_name": "Scanning...", "gpu_name": "Scanning...", "ram_type": "DDR-UNK" },
    "raw": { "status": "Starting..." }
}

# --- 1. CPU NAME (Registry Method - Instant & Accurate) ---
def get_cpu_name():
    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            return name.strip()
        else:
            return platform.processor()
    except:
        return "Generic CPU"

# --- 2. GPU NAME (Smart PowerShell Method - Prioritizes Dedicated GPU) ---
def get_gpu_name():
    try:
        # PowerShell command to get all video controllers
        cmd = ["powershell", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"]
        
        # Run silently (no window popping up)
        output = subprocess.check_output(
            cmd, 
            creationflags=0x08000000, 
            stderr=subprocess.DEVNULL
        ).decode().strip()
        
        # Create list of GPUs found
        gpus = [line.strip() for line in output.split('\n') if line.strip()]
        
        if not gpus: return "Generic GPU"
        
        # PRIORITY LIST: Pick these first if found
        priority_keys = ["NVIDIA", "GeForce", "Radeon", "RTX", "GTX", "Arc", "RX"]
        
        for gpu in gpus:
            for key in priority_keys:
                if key.lower() in gpu.lower():
                    return gpu # Found a dedicated card!
        
        # Fallback to the first one (usually Intel UHD)
        return gpus[0]
            
    except:
        return "Generic GPU"

# --- PARSING HELPERS ---
def safe_float(v):
    if not v: return 0.0
    try:
        s = str(v).upper().replace("MHZ", "").replace("%", "").replace("°C", "").replace("W", "").strip()
        if "," in s and "." not in s: s = s.replace(",", ".")
        elif "," in s and "." in s:   s = s.replace(",", "")
        return float(s)
    except:
        return 0.0

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

# --- THE MONITOR LOOP ---
def monitor_persistent():
    # A. DETECT HARDWARE ONCE (0% CPU Impact on loop)
    c_name = get_cpu_name()
    g_name = get_gpu_name()
    latest_stats["info"]["cpu_name"] = c_name
    latest_stats["info"]["gpu_name"] = g_name
    print(f"--- DETECTED: {c_name} | {g_name} ---")

    print(f"--- WAITING FOR LOGS IN: {os.path.abspath(LOG_DIR)} ---")
    CSV_PATH = None

    while CSV_PATH is None:
        CSV_PATH = find_latest_csv(LOG_DIR)
        time.sleep(2)

    print(f"--- TRACKING: {CSV_PATH} ---")

    # B. READ HEADERS ONCE
    idx = {}
    delimiter = ","
    try:
        with open(CSV_PATH, "r", encoding="utf-8", errors="ignore") as f:
            header_line = f.readline().strip()
            if header_line.count(";") > header_line.count(","): delimiter = ";"
            headers = header_line.split(delimiter)
            
            # Map columns
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
                'ram_spd':  find_idx(headers, [["memory", "clock"], ["dram", "frequency"]])
            }
            print(f"--- HEADERS MAPPED ---")
    except Exception as e:
        print(f"Header Error: {e}")
        return

    # C. 0% CPU LOOP
    f = None
    while True:
        try:
            if f is None:
                f = open(CSV_PATH, "rb")
                f.seek(0, 2) # Jump to end

            # SLEEP FIRST
            time.sleep(INTERVAL)

            # Check for file rotation
            try:
                if os.fstat(f.fileno()).st_size < f.tell():
                    f.close(); f = None; continue
            except OSError:
                f = None; continue

            # TAIL READ (Last 4KB only)
            f.seek(0, 2)
            file_len = f.tell()
            read_len = min(file_len, 4096)
            f.seek(-read_len, 1)

            raw_block = f.read(read_len)
            text_block = raw_block.decode("utf-8", errors="ignore")
            lines = text_block.split('\n')

            line = ""
            if len(lines) > 1 and lines[-1].strip(): line = lines[-1]
            elif len(lines) > 2: line = lines[-2]
            
            if not line.strip(): continue

            parts = line.split(delimiter)
            if len(parts) < 3: continue

            # UPDATE STATS
            def get_val(k):
                i = idx.get(k, -1)
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
            
            # Auto-detect GB/MB
            if r_used > 512: r_used /= 1024 
            latest_stats["ram"]["used_gb"] = r_used
            
            if r_used and r_load:
                latest_stats["ram"]["total_gb"] = r_used / (r_load / 100.0)

            # RAM DDR Calc
            r_clk = get_val('ram_spd')
            if r_clk > 0:
                mt = int(r_clk * 2)
                ddr = "DDR5" if mt > 4600 else "DDR4"
                latest_stats["info"]["ram_type"] = f"{ddr}-{mt}"

            latest_stats["raw"]["status"] = "LIVE"

        except Exception as e:
            print(f"Error: {e}")
            if f: f.close()
            f = None
            time.sleep(1)

# --- START SERVER ---
@app.on_event("startup")
def start():
    threading.Thread(target=monitor_persistent, daemon=True).start()

@app.get("/stats")
def get_stats(): return JSONResponse(latest_stats)

@app.get("/")
def index(): return FileResponse("static/index.html")