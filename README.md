# colab-gpu-runner

用 Google Colab 命令列租顯示卡、無人跑完一支腳本、成品經雲端硬碟拿回本機。
附一條實際跑通的範例：一張照片 → MiniMax H3 生成有聲短片 → 放大 → 補幀到每秒 48 格。

Rent a Google Colab GPU from the command line, run a script unattended, and pull the outputs back via Google Drive.
Includes a working example: one photo → MiniMax H3 image-to-video (with audio) → SeedVR2 upscale → RIFE 48 fps.

## 檔案

| 檔案 | 做什麼 |
|---|---|
| `check_env.py` | 相容性檢查：這台電腦能不能遙控 Colab 借卡 |
| `colab_login.py` | 兩段式登入 Colab 命令列：先印登入網址，在手機開完把授權碼貼回來即可（電腦不用開瀏覽器） |
| `g4_probe.py` | 測試配到的顯卡：型號、記憶體、PyTorch 支不支援、bf16 矩陣乘法速度 |
| `film/build_remote_job.py` | 把起始照片、畫面描述、ComfyUI 流程打包成一支 `remote_job.py` |
| `film/remote_job_template.py` | 在 Colab 機器上跑的本體：裝 ComfyUI、從 Hugging Face 下載模型、依序生成／放大／補幀、上傳雲端硬碟 |
| `film/run_g4.ps1` | 本機啟動器：開機器 → 跑腳本 → 自動釋放 → 從雲端硬碟下載並比對雜湊 |
| `film/download_drive_outputs.ps1` | 依遠端紀錄的檔案清單下載成品，逐檔比對 SHA-256 |

## 需要先裝

- [google-colab-cli](https://pypi.org/project/google-colab-cli/)（指令 `colab`，建議 `uv tool install google-colab-cli`；裝完若提示不在系統路徑可以不理，程式會自己去 `~/.local/bin` 找），需要 Colab 付費方案才有 G4／A100
- [gog](https://github.com/openclaw/gogcli)（Google 雲端硬碟命令列），先用自己的帳號登入
- Python 3.10＋Pillow、PowerShell 7

## 先檢查這台電腦

顯示卡是在 Colab 上借的，**你的電腦不需要顯示卡**。要確認的是遙控工具都裝好：

```powershell
python check_env.py
```

每一項會印「通過／不通過」，不通過的會附修法。不安裝東西、不開機器、不花錢。

## 借哪張卡

| Colab 顯示卡 | 顯存 | 能不能跑 H3 這套 |
|---|---|---|
| G4（RTX PRO 6000 Blackwell） | 96GB | **優先借這張**：實測最快（比 A100 快約 1.6 倍），程式預設就是它 |
| A100 | 80GB 或 40GB | 配到 80GB 可以；配到 40GB 會被擋下，重借一次 |
| H100 | 80GB | 顯存夠，未實測 |
| T4／L4 | 16／24GB | 不行，顯存不夠 |

H3 兩段式生成的顯存峰值約 52GB。遠端腳本開機第一步就檢查借到的卡，不到 75,000 MiB 會直接停，不會白燒錢。
借 G4／A100 需要 Colab 付費方案。

## 用法

```powershell
# 1. 登入 Colab（只要一次）
$PY = "$env:APPDATA\uv\tools\google-colab-cli\Scripts\python.exe"
& $PY colab_login.py url          # 印出登入網址
& $PY colab_login.py code <授權碼>

# 2. 先試顯卡（約 1 分鐘）
colab run --gpu G4 g4_probe.py

# 3. 打包工作
python film/build_remote_job.py --image start.png --prompt "a woman smiles and waves at the camera" --out-dir ./job

# 4. 先跑不生成的煙霧測試，確認雲端硬碟上傳下載通
pwsh film/run_g4.ps1 -Root ./job -GoogleAccount you@gmail.com -SmokeTest

# 5. 正式跑
pwsh film/run_g4.ps1 -Root ./job -GoogleAccount you@gmail.com
```

成品在 `./job/out/`：`shot01_B.mp4`（生成）、`shot01_C.mp4`（放大 1.5 倍）、`shot01_C_48fps.mp4`（補幀）。

## 實測數字（2026-10，G4＝RTX PRO 6000 Blackwell 96GB）

- bf16 矩陣乘法：G4 約 423 TFLOPS（2026-11-01 用 `g4_probe.py` 重測 422.8，仍相符），A100 80GB 約 268，G4 快約 1.6 倍
- 5 秒一段（768×1344 → 1152×2016、48 格）：開機到釋放約 9～10 分鐘，約 1.2 運算單元
- 10 秒一段：約 17.5 分鐘、2.1 單元；15 秒一段（單段上限 362 格）：約 24 分鐘、3.1～3.4 單元
- 從 Hugging Face 直接下載 7 個模型約 3 分鐘，比從雲端硬碟複製（約 22 分鐘）快很多
- 運算單元費率與方案會變，以 `colab usage` 實際讀數為準

## 踩過的坑

1. **殘留機器會一直計費。** 看管的本機程序一斷，雲端機器不會自己關。跑完一定 `colab sessions` 看一次，有殘留用 `colab stop -s <名稱>`。
2. **長任務用 `colab run`，不要 `colab new`＋`exec`。** 後者容易在送第一行前就斷線。
3. **成品走雲端硬碟，不要用 base64 印回紀錄檔。** 印回一支幾十 MB 的影片要數十分鐘，而且機器一直在計費。
4. **格數要是 17 的倍數加 5**（124、243、362），否則節點會自動往上對齊。
5. **ComfyUI 流程嵌進 Python 要包成字串再 `json.loads`**，直接貼 JSON 會因 `true/false/null` 出錯。
6. **雲端硬碟權杖一小時過期**，所以範本在生成完、上傳前才換新權杖。
7. 遠端腳本自帶 70 分鐘硬逾時，留 5 分鐘給 `colab run` 釋放機器。
8. **Windows 請固定用 google-colab-cli 0.7.4，並修補一個檔。** 2026-11-01 實測：0.7.5 在 Windows 上機器開好、一跑腳本就斷線（`WebSocketConnectionClosedException: socket is already closed`），連試兩次都一樣；0.7.4 原版則一啟動就因 `import termios`（Linux 才有）失敗。做法：
   ```powershell
   uv tool install --python 3.12 google-colab-cli==0.7.4 --force
   ```
   再打開 `%APPDATA%\uv\tools\google-colab-cli\Lib\site-packages\colab_cli\console.py`，把 `import termios`、`import tty` 各改成：
   ```python
   try:
       import termios
   except ImportError:
       termios = None
   ```
   只影響 `console`／`ssh` 的鍵盤轉送，`run`／`sessions`／`stop`／`usage` 都正常。重裝或升級會蓋掉這個修補。
9. **關掉自動檢查更新**：在 `~/.config/colab-cli/settings.json` 寫 `{"enable_update_check": false}`，免得新版提示混進輸出，或被提示誘導升到 0.7.5。

## 模型與授權

範例用到的模型與外掛都從原作者處下載，請各自遵守其授權：
MiniMax H3（Comfy-Org）、SeedVR2、LBH-123-AI latent upscaler、fal Realism-People LoRA、prithivMLmods Facial-Realism LoRA、ComfyUI-Frame-Interpolation（RIFE）。

本倉庫程式碼採 MIT 授權。
