# Lessons

## Killing intelenv/uv-launched generation jobs (2026-06-28)
**Mistake:** Launched `python regen_flagged.py --codes rerswa8` and tracked the
PID from `$!`. Later "killed" that PID — but the job kept running and starved
the main batch (two 36 GB FLUX models → memory thrashing; the main batch's log
froze for ~2 h while I thought rerswa8 was dead).

**Why:** The intelenv `python.exe` is a *launcher* that spawns the real worker
as a `uv` child process and then exits. The PID from `$!` (or `nohup … &`) is
the launcher/bash shell, which is gone almost immediately. `kill <that-pid>`
does nothing to the actual worker.

**How to apply:** To stop a generation job, find the *real* worker by its large
working set / command line, not the launcher PID:
`Get-CimInstance Win32_Process -Filter "Name='python.exe'"` → sort by
`WorkingSetSize` (the FLUX worker is tens of GB) and check `CommandLine`. Kill
that PID's tree. After killing, verify memory is actually released and the other
job's log resumes writing before assuming success. Don't run two FLUX processes
on this box at once — they thrash.
