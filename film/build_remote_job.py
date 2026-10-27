"""產生要丟上 Colab 跑的 remote_job.py（一張起始照片 → 一段有聲短片）。

用法：
  python build_remote_job.py --image start.png --prompt "畫面描述" --out-dir ./job
  之後：powershell -File run_g4.ps1 -Root ./job -GoogleAccount you@gmail.com

流程：MiniMax H3 小圖 30 步 → 潛空間放大兩倍 → 重算 10 步 → SeedVR2 放大 1.5 倍 → RIFE 24→48 格。
"""
import argparse
import base64
import json
import re
from pathlib import Path
from PIL import Image, ImageOps

PROJECT = Path(__file__).resolve().parent

ap = argparse.ArgumentParser()
ap.add_argument("--image", required=True, help="起始照片")
ap.add_argument("--prompt", required=True, help="畫面描述（英文效果較穩）")
ap.add_argument("--out-dir", required=True, help="remote_job.py 寫到哪個資料夾")
ap.add_argument("--width", type=int, default=768)
ap.add_argument("--height", type=int, default=1344)
ap.add_argument("--length", type=int, default=124, help="格數，需為 17k+5；124≈5 秒、243≈10 秒、362≈15 秒")
ap.add_argument("--seed", type=int, default=20261012)
ap.add_argument("--no-deoil", action="store_true", help="關掉去油光（兩支寫實外掛＋SigmaShift）")
args = ap.parse_args()

ROOT = Path(args.out_dir)
ROOT.mkdir(parents=True, exist_ok=True)

# 去油光預設值集中在這裡；要停用時加 --no-deoil。
DEOIL_ENABLED = not args.no_deoil
REALISM_LORA = "h3-realism-people-t2v-i2v-r2v.safetensors"
REALISM_STRENGTH = 0.75
FACIAL_LORA = "minimax-h3-facial-realism-closeup-cp2000.safetensors"
FACIAL_STRENGTH = 0.50
SIGMA_VIDEO = 12.0
SIGMA_AUDIO = 3.0
# 若日後加入 turbo 外掛，預設強度必須是 0.85，且仍要經過 MiniMaxH3SigmaShift。

def deoiled_prompt(text):
    text = re.sub(r"\b(?:dewy|glowing)\s+skin\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return "r34l1sm, Facial Realism: " + text + " natural soft matte skin texture, fine pores, zero oily shine"

prompt = deoiled_prompt(args.prompt) if DEOIL_ENABLED else args.prompt
W, H, LENGTH, STEPS, REFINE, SEED = args.width, args.height, args.length, 30, 10, args.seed
ImageOps.fit(Image.open(args.image).convert("RGB"), (W, H), centering=(0.5, 0.5)).save(ROOT / "start.jpg", quality=95)
UPS_FILE = "minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors"

def base():
    wf = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "type": "minimax"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"}},
        "5": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"}},
        "20": {"class_type": "LoadImage", "inputs": {"image": "start.jpg"}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": SEED}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "14": {"class_type": "CreateVideo", "inputs": {"images": ["12", 0], "audio": ["13", 0], "fps": 24.0}},
        "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": "g4/shot01_B", "format": "auto", "codec": "auto"}},
    }
    model = ["1", 0]
    if DEOIL_ENABLED:
        wf["21"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": REALISM_LORA, "strength_model": REALISM_STRENGTH}}
        wf["22"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["21", 0], "lora_name": FACIAL_LORA, "strength_model": FACIAL_STRENGTH}}
        wf["23"] = {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["22", 0], "shift_video": SIGMA_VIDEO, "shift_audio": SIGMA_AUDIO}}
        model = ["23", 0]
    wf["10"] = {"class_type": "BasicScheduler", "inputs": {"model": model, "scheduler": "simple", "steps": STEPS, "denoise": 1.0}}
    wf["6"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": prompt, "width": W, "height": H, "length": LENGTH, "first_frame": ["20", 0]}}
    wf["8"] = {"class_type": "BasicGuider", "inputs": {"model": model, "conditioning": ["6", 0]}}
    return wf, model

wf_b, MODEL = base()
wf_b.update({
    "30": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"clip": ["3", 0], "vae": ["4", 0], "prompt": prompt, "width": W // 2, "height": H // 2, "length": LENGTH, "first_frame": ["20", 0]}},
    "31": {"class_type": "BasicGuider", "inputs": {"model": MODEL, "conditioning": ["30", 0]}},
    "32": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["31", 0], "sampler": ["9", 0], "sigmas": ["10", 0], "latent_image": ["30", 1]}},
    "33": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["32", 0]}},
    "34": {"class_type": "MinimaxH3LatentUpscaler3D", "inputs": {"latent": ["33", 0], "model_name": UPS_FILE, "mode": "scale by multiplier", "mode.scale": 2.0, "align": 32, "enable_temporal_chunking": True, "force_unload": True, "device": "cuda", "precision": "bf16"}},
    "35": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["34", 0], "audio_latent": ["33", 1]}},
    "36": {"class_type": "SplitSigmas", "inputs": {"sigmas": ["10", 0], "step": STEPS - REFINE}},
    "11": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["7", 0], "guider": ["8", 0], "sampler": ["9", 0], "sigmas": ["36", 1], "latent_image": ["35", 0]}},
    "16": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["11", 0]}},
    "12": {"class_type": "VAEDecode", "inputs": {"samples": ["16", 0], "vae": ["4", 0]}},
    "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["33", 1], "vae": ["5", 0]}},
})

tile = {"tile_size": 1024, "overlap": 128, "temporal_size": 64, "temporal_overlap": 8}
wf_c = {
    "1": {"class_type": "LoadVideo", "inputs": {"file": "shot01_B.mp4"}},
    "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
    "3": {"class_type": "ImageScaleBy", "inputs": {"image": ["2", 0], "upscale_method": "lanczos", "scale_by": 1.5}},
    "4": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["3", 0]}},
    "5": {"class_type": "VAELoader", "inputs": {"vae_name": "seedvr2_ema_vae_fp16.safetensors"}},
    "6": {"class_type": "UNETLoader", "inputs": {"unet_name": "seedvr2_7b_int8_convrot.safetensors", "weight_dtype": "default"}},
    "7": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["4", 0], "vae": ["5", 0], **tile}},
    "8": {"class_type": "SeedVR2TemporalChunk", "inputs": {"latent": ["7", 0], "temporal_overlap": 1, "chunking_mode": "auto"}},
    "9": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["6", 0], "vae_conditioning": ["8", 0]}},
    "10": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "seed": SEED, "steps": 1, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["9", 0], "negative": ["9", 1], "latent_image": ["8", 0], "denoise": 1.0}},
    "11": {"class_type": "SeedVR2TemporalMerge", "inputs": {"latents": ["10", 0], "temporal_overlap": ["8", 1]}},
    "12": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["11", 0], "vae": ["5", 0], **tile}},
    "13": {"class_type": "SeedVR2PostProcessing", "inputs": {"images": ["12", 0], "original_resized_images": ["3", 0], "color_correction_method": "none"}},
    "14": {"class_type": "CreateVideo", "inputs": {"images": ["13", 0], "audio": ["2", 1], "fps": 24.0}},
    "15": {"class_type": "SaveVideo", "inputs": {"video": ["14", 0], "filename_prefix": "g4/shot01_C", "format": "auto", "codec": "auto"}},
}

wf_rife = {
    "1": {"class_type": "LoadVideo", "inputs": {"file": "shot01_C.mp4"}},
    "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
    "3": {"class_type": "RIFE VFI", "inputs": {"frames": ["2", 0], "ckpt_name": "rife47.pth", "clear_cache_after_n_frames": 10, "multiplier": 2, "fast_mode": True, "ensemble": True, "scale_factor": 1.0, "dtype": "float32", "torch_compile": False, "batch_size": 1}},
    "4": {"class_type": "CreateVideo", "inputs": {"images": ["3", 0], "audio": ["2", 1], "fps": 48.0}},
    "5": {"class_type": "SaveVideo", "inputs": {"video": ["4", 0], "filename_prefix": "g4/shot01_C_48fps", "format": "auto", "codec": "auto"}},
}

template = (PROJECT / "remote_job_template.py").read_text(encoding="utf-8")
template = template.replace("__WF_B__", repr(json.dumps(wf_b, ensure_ascii=False)))
template = template.replace("__WF_C__", repr(json.dumps(wf_c, ensure_ascii=False)))
template = template.replace("__WF_RIFE__", repr(json.dumps(wf_rife, ensure_ascii=False)))
template = template.replace("__IMAGE_B64__", repr(base64.b64encode((ROOT / "start.jpg").read_bytes()).decode("ascii")))
(ROOT / "remote_job.py").write_text(template, encoding="utf-8")
print("built", ROOT / "remote_job.py")
