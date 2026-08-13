# WP14 soak — power settings to restore afterwards

The 24 h soak requires the machine not to sleep; `PREREG_LIVE_SOAK.md` makes
a sleep event VOID the run. The prior values were recorded **before** any
change, per the roadmap, and are restored with:

```powershell
powercfg /change standby-timeout-ac 300
powercfg /change standby-timeout-dc 300
powercfg /change monitor-timeout-ac 10   # set to taste; was not recorded
```

| setting | value before the soak | value during |
|---|---:|---:|
| `standby-timeout-ac` | `0x4650` = 18000 s = **300 min** | 0 (never) |
| `standby-timeout-dc` | `0x4650` = 18000 s = **300 min** | 0 (never) |
| `hibernate-timeout-ac` | `0x0` (already disabled) | unchanged |
| `monitor-timeout-ac` | not recorded before the change | 0 (never) |

**Disclosure:** the monitor timeout was changed without its prior value being
captured first — an error, recorded here rather than papered over. Restore it
to whatever you prefer; it has no bearing on the run.
