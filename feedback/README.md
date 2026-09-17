# Image feedback loop

Users rate bird images (👍/👎). Each vote is filed into a **Google Drive folder**
as one small JSON file, and the refresh job reads that folder and replaces
images that accumulate net downvotes.

```
browser (docs/feedback.js)
  → POST to an Apps Script web app (feedback/appsscript/Code.gs)
       → one JSON file per vote in Drive/birds_today_feedback/
            → scripts/feedback_refresh.py (reads the synced folder)
                 → blocklist source id (rejects.json) + replace image → commit
```

You can read the raw votes any time by opening the folder in Drive. Processed
files are moved to `birds_today_feedback/processed/`, so what's left at the top
level is what hasn't been acted on yet.

Why an Apps Script in the middle: GitHub Pages serves a static page, so the
page cannot hold a Drive credential (anything shipped to the browser is public)
and we don't want visitors signing into Google to vote. The script runs as
*you*, accepts a POST from anyone, and is the only thing that touches Drive.

## Setup

### 1. The sink (Apps Script → Drive)

1. Go to https://script.google.com → **New project**, and paste
   `feedback/appsscript/Code.gs` over the default file.
2. **Deploy → New deployment → Web app**:
   - **Execute as:** Me
   - **Who has access:** Anyone
3. Copy the deployment's `…/exec` URL.
4. Open that URL once in a browser. It answers with the folder it will write to
   and creates `birds_today_feedback` in My Drive if it isn't there yet.
5. Paste the URL into `ENDPOINT` at the top of `docs/feedback.js`, then commit
   and push.

"Anyone" means anyone may POST a vote — the same exposure the old EmailJS public
key had. Nobody can read your Drive through the URL: `doGet` returns a status
line and nothing else, and the script ignores a body that isn't a small JSON
vote.

### 2. The reader (Drive → the refresh job)

Install **Google Drive for desktop** and let it sync `birds_today_feedback`.
The job then just reads files — no API credentials, no tokens to rotate:

```bash
python scripts/feedback_refresh.py --votes-dir "G:/My Drive/birds_today_feedback"
```

Set `BIRD_VOTES_DIR` instead of passing `--votes-dir` if you prefer. Add
`--keep` to look at what is waiting without consuming it.

Because each processed file is moved into `processed/`, a vote is acted on
exactly once — the same property the old "unseen email" read had. That also
means the scheduled GitHub Action can no longer do the reading: a runner has no
access to your Drive. Run the refresh on the machine that syncs the folder (the
same one that draws the images).

## Run the refresh manually

```bash
# From the synced Drive folder
python scripts/feedback_refresh.py --votes-dir "G:/My Drive/birds_today_feedback"

# Or from a local CSV for testing (columns: image, vote)
python scripts/feedback_refresh.py --votes-file votes.csv --threshold 1
```

## What a vote looks like

`20260917T193004_downvote_gretit1_3f9c1a20.json`:

```json
{
  "image": "gretit1/sitting_0.png",
  "vote": "downvote",
  "hash": "9f2c…",
  "species": "gretit1",
  "sci": "Parus major",
  "common": "Great Tit",
  "pose": "sitting",
  "lang": "en",
  "src": "ai",
  "client": "k3f9a1x2mj",
  "ts": "2026-09-17T19:30:04.123Z",
  "received": "2026-09-17T19:30:05.001Z"
}
```

`hash` is the SHA-256 of the image bytes the voter actually saw, so the job can
tell a vote about the current image from a vote about one already replaced.

## Delivery caveat

Apps Script web apps don't answer a CORS preflight, so the page sends the vote
as a no-cors request: the browser will not let the page read the response, so a
vote the script rejects is indistinguishable from one it accepted. A vote that
fails at the network level (offline, blocked, DNS) is kept in a localStorage
outbox and retried on the next page load, and when the browser reports it is
back online.
