# polyforge-t4-probe

Evidence for `WAVE4_FREE_ROUTE.md` 0b: `GPU T4 x2` is settable from the API.

Session 42 concluded it was UI-gated after four `--accelerator` spellings all
normalised to the generic `Gpu`. All four were invalid enum values. The value
`kagglesdk` documents is `NvidiaTeslaT4`, and it works.

Reproduce:

```sh
kaggle kernels push -p .          # machine_shape is in kernel-metadata.json
kaggle kernels status mohaimin08/polyforge-t4-probe
kaggle kernels output mohaimin08/polyforge-t4-probe -p /tmp/out
```

Verify the server kept the value rather than normalising it:

```sh
kaggle kernels pull mohaimin08/polyforge-t4-probe -m -p /tmp/meta
# -> "machine_shape": "NvidiaTeslaT4"
```

Measured 2026-09-07:

```
torch 2.10.0+cu128
arch list: ['sm_70','sm_75','sm_80','sm_86','sm_90','sm_100','sm_120']
device count: 2
GPU0 Tesla T4 -- cuda capability 7.5
GPU1 Tesla T4 -- cuda capability 7.5
GPU0 matmul ok, trace=514.4395
GPU1 matmul ok, trace=199.1757
```

The matmuls matter: session 42's failure was
`cudaErrorNoKernelImageForDevice`, so enumerating the cards proves nothing on
its own. One matmul runs per card.
