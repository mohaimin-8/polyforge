"""Probe: does machine_shape=NvidiaTeslaT4 actually allocate T4 x2?

Session 42 tested four spellings (gpu-t4x2, GpuT4x2, gpu-t4-x2,
TPU_OR_GPU_T4X2); all normalised to the generic `Gpu` and landed a P100.
None of them was the enum the SDK documents. This tries NvidiaTeslaT4.
"""
import torch

print("torch", torch.__version__)
print("arch list:", torch.cuda.get_arch_list())
print("device count:", torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    cap = torch.cuda.get_device_capability(i)
    print(f"GPU{i} {torch.cuda.get_device_name(i)} -- cuda capability {cap[0]}.{cap[1]}")

# The decisive check: session 42's failure was cudaErrorNoKernelImageForDevice,
# so confirm a kernel actually executes rather than just enumerating the card.
for i in range(torch.cuda.device_count()):
    x = torch.randn(512, 512, device=f"cuda:{i}")
    print(f"GPU{i} matmul ok, trace={float((x @ x).diagonal().sum()):.4f}")
