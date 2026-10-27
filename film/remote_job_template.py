import base64, hashlib, json, mimetypes, os, shutil, signal, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

T0 = time.time()
TIMES = {}
EVENTS = []
DRIVE_ACCESS_TOKEN = os.environ.get("GDRIVE_ACCESS_TOKEN", "")
DRIVE_REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
DRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
DRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
DRIVE_FOLDER = os.environ.get("GDRIVE_FOLDER", "colab_outputs")
# 借到的卡顯存下限（MiB）。兩段式生成峰值約 52GB，所以 T4／L4／40GB 版 A100 都會擋下。
MIN_VRAM_MB = int(os.environ.get("MIN_VRAM_MB", "75000"))
DRIVE_RUN_ID = os.environ.get("GDRIVE_RUN_ID", time.strftime("g4-%Y%m%d-%H%M%S"))
EMIT_BASE64 = os.environ.get("G4_EMIT_BASE64", "0") == "1"
SMOKE_TEST = os.environ.get("G4_SMOKE_TEST", "0") == "1"

def hard_timeout(signum, frame):
    raise TimeoutError("遠端硬逾時：已到 70 分鐘，保留 5 分鐘供 colab run 自動釋放")

signal.signal(signal.SIGALRM, hard_timeout)
signal.alarm(70 * 60)

def stamp(name, start=None):
    now = time.time()
    if start is not None:
        TIMES[name] = round(now - start, 3)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {name}" + (f" {TIMES[name]} 秒" if start is not None else "")
    EVENTS.append(line); print(line, flush=True)

def run(cmd, cwd=None):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)

def drive_request(url, method="GET", body=None, headers=None):
    headers = dict(headers or {})
    headers["Authorization"] = "Bearer " + DRIVE_ACCESS_TOKEN
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:2000]
        raise RuntimeError(f"Drive API {method} 失敗 HTTP {e.code}: {detail}") from e

def drive_refresh_access_token():
    global DRIVE_ACCESS_TOKEN
    if not all((DRIVE_REFRESH_TOKEN, DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET)):
        if DRIVE_ACCESS_TOKEN:
            return
        raise RuntimeError("缺少可續期的 Drive OAuth 憑證")
    body = urllib.parse.urlencode({
        "client_id": DRIVE_CLIENT_ID,
        "client_secret": DRIVE_CLIENT_SECRET,
        "refresh_token": DRIVE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
    try:
        response = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:1000]
        raise RuntimeError(f"Drive OAuth 續期失敗 HTTP {e.code}: {detail}") from e
    DRIVE_ACCESS_TOKEN = response["access_token"]
    print("DRIVE_TOKEN_REFRESHED=1", flush=True)

def drive_find_or_create_folder(name, parent=None):
    safe = name.replace("'", "\\'")
    clauses = [f"name='{safe}'", "mimeType='application/vnd.google-apps.folder'", "trashed=false"]
    if parent:
        clauses.append(f"'{parent}' in parents")
    query = urllib.parse.urlencode({"q": " and ".join(clauses), "fields": "files(id,name)", "pageSize": 10})
    found = json.load(drive_request("https://www.googleapis.com/drive/v3/files?" + query))["files"]
    if found:
        return found[0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        metadata["parents"] = [parent]
    body = json.dumps(metadata).encode()
    return json.load(drive_request("https://www.googleapis.com/drive/v3/files?fields=id", "POST", body, {"Content-Type": "application/json"}))["id"]

def drive_upload(path, parent_id):
    path = Path(path)
    metadata = json.dumps({"name": path.name, "parents": [parent_id]}).encode()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,size"
    response = drive_request(url, "POST", metadata, {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime,
        "X-Upload-Content-Length": str(size),
    })
    upload_url = response.headers["Location"]
    completed = subprocess.run([
        "curl", "--fail-with-body", "--silent", "--show-error", "--request", "PUT",
        "--header", "Authorization: Bearer " + DRIVE_ACCESS_TOKEN,
        "--header", f"Content-Type: {mime}", "--upload-file", str(path), upload_url,
    ], check=True, capture_output=True, text=True)
    result = json.loads(completed.stdout)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    digest = digest.hexdigest()
    print("DRIVE_FILE=" + json.dumps({**result, "sha256": digest}, ensure_ascii=False), flush=True)
    return result

def drive_publish(outputs):
    # 上傳前才換短效權杖，避免長時間生成使一小時 access token 過期。
    drive_refresh_access_token()
    root_folder = drive_find_or_create_folder(DRIVE_FOLDER)
    run_folder = drive_find_or_create_folder(DRIVE_RUN_ID, root_folder)
    for output in outputs:
        drive_upload(output, run_folder)
    print("DRIVE_RUN_FOLDER_ID=" + run_folder, flush=True)
    return run_folder

if SMOKE_TEST:
    smoke = Path("/content/g4_drive_smoke.txt")
    smoke.write_text("G4 Drive smoke test\n", encoding="utf-8")
    print("GPU_CHECK=" + subprocess.check_output(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], text=True).strip(), flush=True)
    drive_publish([smoke])
    signal.alarm(0)
    raise SystemExit(0)

def post_wait(wf, label):
    req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=json.dumps({"prompt": wf}).encode(), headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("送出失敗：" + e.read().decode(errors="replace")[:5000])
    while True:
        h = json.load(urllib.request.urlopen(f"http://127.0.0.1:8188/history/{pid}"))
        if pid in h:
            st = h[pid]["status"]
            if st.get("status_str") != "success":
                raise RuntimeError(label + " 失敗：" + json.dumps(st.get("messages", []), ensure_ascii=False)[-7000:])
            files = []
            for node in h[pid]["outputs"].values():
                for key in ("images", "videos", "gifs"):
                    for f in node.get(key, []):
                        if f.get("type", "output") == "output":
                            files.append(Path("/content/ComfyUI/output") / f.get("subfolder", "") / f["filename"])
            if not files: raise RuntimeError(label + " 成功但找不到輸出檔")
            return files[-1]
        time.sleep(3)

stamp("開機／腳本開始")
gpu = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], text=True).strip()
print("GPU_CHECK=" + gpu, flush=True)
vram_mb = int(gpu.split(",")[1].strip().split()[0])
if vram_mb < MIN_VRAM_MB:
    raise RuntimeError(f"借到的卡顯存只有 {vram_mb} MiB，不到 {MIN_VRAM_MB}（請改借 G4 或 80GB 版 A100）：" + gpu)

Path("/content/start.jpg").write_bytes(base64.b64decode(__IMAGE_B64__))

t = time.time()
run(["git", "clone", "-q", "--depth", "1", "https://github.com/comfyanonymous/ComfyUI", "/content/ComfyUI"])
run([sys.executable, "-m", "pip", "install", "-q", "-r", "/content/ComfyUI/requirements.txt"])
run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub<2.0", "hf_transfer"])
nodes = Path("/content/ComfyUI/custom_nodes")
run(["git", "clone", "-q", "https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler", str(nodes / "h3_latent_upscaler")])
run(["git", "-C", str(nodes / "h3_latent_upscaler"), "checkout", "-q", "d7c01b9011f2e8439493f6c02c29995a27df276f"])
run(["git", "clone", "-q", "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation", str(nodes / "ComfyUI-Frame-Interpolation")])
run(["git", "-C", str(nodes / "ComfyUI-Frame-Interpolation"), "checkout", "-q", "0d4525b"])
run([sys.executable, "-m", "pip", "install", "-q", "-r", str(nodes / "ComfyUI-Frame-Interpolation/requirements-no-cupy.txt")])
stamp("裝環境", t)

t = time.time(); os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
from huggingface_hub import hf_hub_download
files = [
 ("Comfy-Org/MiniMax-H3", "diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "diffusion_models"),
 ("Comfy-Org/MiniMax-H3", "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "text_encoders"),
 ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_video_vae_fp16.safetensors", "vae"),
 ("Comfy-Org/MiniMax-H3", "vae/minimax_h3_audio_vae_fp32.safetensors", "vae"),
 ("LBH-123-AI/Minimax_h3_latent_Upscaler", "minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors", "latent_upscale_models"),
 ("Comfy-Org/SeedVR2", "diffusion_models/seedvr2_7b_int8_convrot.safetensors", "diffusion_models"),
 ("Comfy-Org/SeedVR2", "vae/seedvr2_ema_vae_fp16.safetensors", "vae"),
 ("fal/MiniMax-H3-Realism-People-LoRA", "h3-realism-people-t2v-i2v-r2v.safetensors", "loras"),
 ("prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp", "minimax-h3-facial-realism-closeup-cp2000.safetensors", "loras"),
]
for repo, path, sub in files:
    p = hf_hub_download(repo, path, local_dir="/content/hf")
    dst = Path("/content/ComfyUI/models") / sub / Path(path).name
    dst.parent.mkdir(parents=True, exist_ok=True); shutil.move(p, dst)
    print(f"MODEL {dst.name} {dst.stat().st_size/1e9:.2f} GB", flush=True)
stamp("取模型", t)

inp = Path("/content/ComfyUI/input"); inp.mkdir(exist_ok=True)
shutil.copy("/content/start.jpg", inp / "start.jpg")
log = open("/content/comfy.log", "w")
srv = subprocess.Popen([sys.executable, "main.py", "--listen", "127.0.0.1", "--port", "8188"], cwd="/content/ComfyUI", stdout=log, stderr=subprocess.STDOUT)
for _ in range(180):
    try: urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2); break
    except Exception: time.sleep(2)
else: raise RuntimeError("ComfyUI 未就緒：" + Path("/content/comfy.log").read_text(errors="replace")[-7000:])

info = json.load(urllib.request.urlopen("http://127.0.0.1:8188/object_info"))
sigma_info = info.get("MiniMaxH3SigmaShift")
print("SIGMA_OBJECT_INFO=" + json.dumps(sigma_info, ensure_ascii=False), flush=True)
if not sigma_info:
    raise RuntimeError("ComfyUI /object_info 查不到 MiniMaxH3SigmaShift")
wf_b=json.loads(__WF_B__); wf_c=json.loads(__WF_C__); wf_rife=json.loads(__WF_RIFE__)
need = sorted({n["class_type"] for w in (wf_b,wf_c,wf_rife) for n in w.values()})
miss=[x for x in need if x not in info]
if miss: raise RuntimeError("缺節點："+repr(miss))
print("NODE_CHECK_OK="+str(len(need)), flush=True)

t=time.time(); b=post_wait(wf_b,"C 畫質生成"); shutil.copy(b, inp/"shot01_B.mp4"); shutil.copy(b,"/content/shot01_B.mp4"); stamp("C 畫質生成（30 步＋潛空間放大＋1344p 重算 10 步）",t)
t=time.time(); c=post_wait(wf_c,"SeedVR2"); shutil.copy(c,inp/"shot01_C.mp4"); shutil.copy(c,"/content/shot01_C.mp4"); stamp("SeedVR2",t)
t=time.time(); f=post_wait(wf_rife,"RIFE"); shutil.copy(f,"/content/shot01_C_48fps.mp4"); stamp("RIFE 24→48",t)
TIMES["總腳本時間"]=round(time.time()-T0,3)
Path("/content/times.json").write_text(json.dumps({"times":TIMES,"gpu":gpu,"events":EVENTS},ensure_ascii=False,indent=2),encoding="utf-8")
print("ALL_DONE="+json.dumps(TIMES,ensure_ascii=False),flush=True)

log.flush()
comfy_text = Path("/content/comfy.log").read_text(encoding="utf-8", errors="replace")
warn_lines = [line for line in comfy_text.splitlines() if "lora" in line.lower() and ("not loaded" in line.lower() or "missing" in line.lower() or "unexpected" in line.lower())]
Path("/content/lora_warnings.txt").write_text("\n".join(warn_lines), encoding="utf-8")
print("LORA_WARNING_COUNT=" + str(len(warn_lines)), flush=True)
for line in warn_lines:
    print("LORA_WARNING=" + line, flush=True)

def emit_file(path):
    path = Path(path)
    print(f"FILE_BEGIN={path.name}:{path.stat().st_size}", flush=True)
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    for i in range(0, len(data), 7600):
        print("FILE_DATA=" + data[i:i + 7600], flush=True)
    print(f"FILE_END={path.name}", flush=True)

outputs = ["/content/shot01_B.mp4", "/content/shot01_C.mp4", "/content/shot01_C_48fps.mp4", "/content/times.json", "/content/comfy.log", "/content/lora_warnings.txt"]
t=time.time()
drive_refresh_access_token()
root_folder = drive_find_or_create_folder(DRIVE_FOLDER)
run_folder = drive_find_or_create_folder(DRIVE_RUN_ID, root_folder)
for output in outputs:
    if output != "/content/times.json":
        drive_upload(output, run_folder)
stamp("Drive 上傳", t)
# 最後才寫入並上傳 times.json，讓本機報告含完整分段。
TIMES["總腳本時間"]=round(time.time()-T0,3)
Path("/content/times.json").write_text(json.dumps({"times":TIMES,"gpu":gpu,"events":EVENTS},ensure_ascii=False,indent=2),encoding="utf-8")
drive_upload("/content/times.json", run_folder)
print("DRIVE_RUN_FOLDER_ID=" + run_folder, flush=True)
if EMIT_BASE64:
    for output in outputs:
        emit_file(output)
signal.alarm(0)
