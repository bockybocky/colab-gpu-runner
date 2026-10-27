# G4 probe: card model, torch/CUDA support for Blackwell, bf16 matmul speed.
# Run: colab run --gpu G4 g4_probe.py   (VM released automatically)
import subprocess, time

print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                      "--format=csv"], capture_output=True, text=True).stdout)
print(subprocess.run(["free", "-g"], capture_output=True, text=True).stdout)

import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("available", torch.cuda.is_available())
cap = torch.cuda.get_device_capability(0)
print("capability", cap, "arch_list", torch.cuda.get_arch_list())

n = 8192
a = torch.randn(n, n, device="cuda", dtype=torch.bfloat16)
b = torch.randn(n, n, device="cuda", dtype=torch.bfloat16)
for _ in range(3):
    a @ b
torch.cuda.synchronize()
t = time.time()
iters = 50
for _ in range(iters):
    a @ b
torch.cuda.synchronize()
dt = time.time() - t
print("bf16_matmul_TFLOPS %.1f" % (2 * n ** 3 * iters / dt / 1e12))
print("PROBE_DONE")
