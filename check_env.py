"""檢查這台電腦能不能遙控 Colab 借顯示卡跑本倉庫（相容性檢查）。

顯示卡是在 Colab 上借的，所以這台電腦本身不需要顯示卡；要檢查的是遙控用的工具。
借到的卡夠不夠力，由 film/remote_job_template.py 開機後第一步自己檢查。

用法：python check_env.py
只讀不寫：不安裝任何東西、不開 Colab 機器、不花錢。
"""
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

RESULTS = []


def report(status, item, detail, fix=""):
    RESULTS.append(status)
    mark = {"PASS": "通過  ", "FAIL": "不通過", "WARN": "警告  "}[status]
    print(f"[{mark}] {item}：{detail}")
    if fix and status != "PASS":
        print(f"          → {fix}")


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)


def version_of(text):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


print(f"作業系統：{platform.system()} {platform.release()}（{platform.machine()}）\n")

v = sys.version_info
if v >= (3, 10):
    report("PASS", "Python", f"{v.major}.{v.minor}.{v.micro}")
else:
    report("FAIL", "Python", f"{v.major}.{v.minor}，需要 3.10 以上", "到 python.org 裝新版")

try:
    import PIL
    report("PASS", "Pillow", PIL.__version__)
except ImportError:
    report("FAIL", "Pillow", "沒裝", "pip install pillow")

pwsh = shutil.which("pwsh")
if pwsh:
    rc, out = run([pwsh, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"])
    ver = version_of(out)
    if ver and ver >= (7, 0):
        report("PASS", "PowerShell", out)
    else:
        report("FAIL", "PowerShell", f"版本讀到 {out!r}", "裝 PowerShell 7：https://aka.ms/powershell")
else:
    report("FAIL", "PowerShell 7", "找不到 pwsh", "裝 PowerShell 7：https://aka.ms/powershell（Windows 內建的 5.1 不夠）")
if platform.system() != "Windows":
    report("WARN", "run_g4.ps1 預設路徑", "非 Windows 時 -GogCredentials 預設值抓不到",
           "執行時自己指定 -GogCredentials <gog 的 credentials.json 路徑>")

colab = shutil.which("colab")
if colab:
    rc, out = run([colab, "run", "--help"])
    if rc == 0 and "--env" in out and "--gpu" in out:
        report("PASS", "Colab 命令列", f"{colab}（colab run 有 --gpu 與 --env）")
    else:
        report("FAIL", "Colab 命令列", "colab run 沒有 --gpu 或 --env", "升級：uv tool upgrade google-colab-cli")
    if (Path.home() / ".config" / "colab-cli" / "token.json").exists():
        report("PASS", "Colab 登入", "已有登入權杖")
        rc, out = run([colab, "usage"], timeout=60)
        if rc == 0:
            report("PASS", "Colab 帳戶", out.splitlines()[0][:120] if out else "讀得到")
            print("          ※ 借 G4／A100 需要 Colab 付費方案；免費方案只有 T4，跑不動 H3。")
        else:
            report("FAIL", "Colab 帳戶", out[:200], "重新登入：python colab_login.py url")
    else:
        report("FAIL", "Colab 登入", "還沒登入", "python colab_login.py url（照 README 第 1 步）")
else:
    report("FAIL", "Colab 命令列", "找不到 colab 指令", "uv tool install google-colab-cli（或 pipx install google-colab-cli）")

gog = shutil.which("gog")
if gog:
    rc, out = run([gog, "auth", "list"])
    if rc == 0 and "@" in out:
        report("PASS", "gog", "已安裝且有登入的 Google 帳號")
    else:
        report("FAIL", "gog 登入", "沒有登入的帳號", "gog auth add <你的 Gmail>，並開啟 Drive 權限")
else:
    report("FAIL", "gog", "找不到 gog 指令", "安裝：https://github.com/steipete/gogcli")

try:
    import urllib.request
    urllib.request.urlopen("https://www.googleapis.com/discovery/v1/apis", timeout=10)
    report("PASS", "連 Google", "通")
except Exception as e:
    report("FAIL", "連 Google", str(e)[:120], "檢查網路或公司防火牆")

# 成品一段 15 秒約數十 MB，留 2GB 就夠；模型都在 Colab 上下載，不佔這台電腦
free_gb = shutil.disk_usage(Path.cwd()).free / 2**30
report("PASS" if free_gb >= 2 else "WARN", "磁碟空間", f"剩 {free_gb:.1f}GB", "清出至少 2GB")

fails = RESULTS.count("FAIL")
print()
if fails:
    print(f"總結：{fails} 項不通過，先照箭頭修好再跑。")
    sys.exit(1)
print("總結：全部通過，可以照 README 開跑。")
