# Image feedback loop

Users rate bird images (👍/👎). Votes are emailed to a Gmail inbox via
**EmailJS** (no backend), and a scheduled GitHub Action reads the vote emails
over IMAP and replaces images that accumulate net downvotes.

```
browser (docs/feedback.js + EmailJS SDK)
  → EmailJS → email to your Gmail (body has a "BIRDVOTE {json}" line)
       → GitHub Action (cron) → scripts/feedback_refresh.py (Gmail IMAP, UNSEEN)
            → blocklist source id (rejects.json) + replace image → commit
```

You can read the raw votes any time in your Gmail inbox.

## Setup

### EmailJS (sending)
1. Create an account at https://www.emailjs.com/ and add an **Email Service**
   connected to your Gmail.
2. Create an **Email Template**. Make sure the body includes the raw token line
   so the pipeline can parse it, e.g.:
   ```
   New bird image vote:
     image:   {{image}}
     vote:    {{vote}}
     species: {{species}}  pose: {{pose}}  lang: {{lang}}
     client:  {{client}}   time: {{ts}}

   {{blob}}
   ```
   (`{{blob}}` expands to `BIRDVOTE {…json…}` — the machine-readable line.)
3. In `docs/index.html`, load the SDK before `feedback.js`:
   ```html
   <script src="https://cdn.jsdelivr.net/npm/@emailjs/browser@4/dist/email.min.js"></script>
   ```
4. In `docs/feedback.js`, set `PUBLIC_KEY`, `SERVICE_ID`, `TEMPLATE_ID` from your
   EmailJS dashboard.

### Gmail IMAP (reading, for the scheduled job)
1. Enable 2-step verification on the Gmail account, then create an
   **App password** (Google Account → Security → App passwords).
2. Add GitHub repo secrets (*Settings → Secrets and variables → Actions*):
   - `GMAIL_USER` — the Gmail address
   - `GMAIL_APP_PASSWORD` — the 16-char app password
3. The workflow `.github/workflows/refresh-images.yml` runs on a schedule (and
   can be triggered manually).

The job reads only **UNSEEN** emails and leaves them marked read, so each vote
is acted on exactly once.

## Run the refresh manually

```bash
# From Gmail (or set GMAIL_USER / GMAIL_APP_PASSWORD in the environment)
python scripts/feedback_refresh.py --gmail-user you@gmail.com --gmail-pass APPPW

# Or from a local CSV for testing (columns: image, vote)
python scripts/feedback_refresh.py --votes-file votes.csv --threshold 1
```
