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

## Wikimedia Commons downloads: keep them serial (2026-08-21)
**Mistake:** Started a 4-thread helper to speed up `fetch_vonwright.py`'s
~8 s/file Commons thumbnail downloads. Commons answered 429 (Retry-After 600)
for ~10 minutes, so both the helper and the main script lost files.

**Why:** Commons throttles per client; thumbnail renditions are rendered on
demand and are slow by design. Parallel fetches don't go faster, they get cut off.

**How to apply:** One request at a time with the polite sleep in `EX._get`, run
in the background and wait. If files are missing after a run, wait 10 min and
re-run the (idempotent) fetch; it skips what's on disk and rewrites the CSV.

## Never write regexes through a shell heredoc (2026-08-21)
**Mistake:** Patched `distill_field_id.py` with `python - <<'PY'` containing
`r"(is|are...)"`. The backslash-b reached Python as a literal backspace byte
(0x08), so the compiled regex was `(is|...)` — it never matched, and a
sentence filter silently did nothing. Three rounds of debugging blamed the
logic; `od -c` on the line found the real cause.

**Why:** Backslash escapes get eaten by the heredoc/`python -c` layer, and the
resulting file still compiles, so nothing fails loudly.

**How to apply:** Use the Edit/Write tools for any code containing regexes or
escapes — never `sed`/heredoc string surgery. If a filter "cannot" be failing,
dump the raw bytes (`od -c`, `open(...,'rb')`) before re-reasoning about logic.
Also: `python -B` / clear `__pycache__` when a module seems stale.

## Test scripts that publish (2026-08-25)
**Mistake:** Ran `apply_choices.py` on a throwaway choices.json to check a new
code path. That script ends by running `git add docs`, committing and pushing —
so it published my half-finished working tree under the message "Apply review
feedback", and pushed it to origin.

**Why:** Pipeline scripts in this repo are end-to-end: they apply state AND
publish. Nothing about the invocation says so.

**How to apply:** Before running any repo script as a test, grep it for `git`,
`push`, `subprocess` and for writes to tracked paths. Prefer a `--dry-run`
(apply_choices.py now has one, `-n`); otherwise run it on a scratch copy. If a
script does publish, either finish and stage deliberately first, or stash.

## Photo quality: prefer a curated pick over an API's default order
iNaturalist's `taxon_photos` is community-curated but still full of record
shots; the app's Great Tit was a soft phone photo where BirdsWhere showed a
portrait. The species' **Wikipedia article lead image** is a stronger curation
signal — an editor chose it, and it is on Commons with a licence and a named
photographer. All 522 curated species resolved to one; none needed the
iNaturalist fallback. When an image source looks weak, ask who curated the
order before tuning the query (`order_by=votes` had already been tried and
reverted: it pulls aberrant and arty shots).

Corollary: `extmetadata.Artist` is sometimes missing while
`AttributionRequired` is true — fall back to the file's uploader rather than
publishing "unknown".

## Verify as a returning visitor, not only in a fresh browser
After switching the photo source to Wikimedia the grid looked right in every
Playwright run — each run starts with an empty profile. The user still saw the
old photos, because `refPhoto` read the persisted `bc_refs` localStorage cache
*before* `photos.json`, and that cache never expired. Anything the app caches
in localStorage/IndexedDB outlives a deploy, so:

- data that ships with the app (photos.json, manifests) is the authority — the
  cache may only answer for what the shipped data does not cover;
- version the storage key whenever the meaning of its contents changes, and
  delete the old key on load;
- reproduce user-visible bugs with the old state seeded (`add_init_script` to
  set localStorage) before concluding a deploy is good.
