#!/usr/bin/env bash
# After the main --all coverage batch finishes:
#   1. commit the id_features.json prompt corrections
#   2. apply choices_5 under the new iterative model (keep champions live,
#      enqueue challenger/regen jobs into gen_queue.json)
#   3. backfill the editable-prompt field into existing review entries
#   4. start the continuous generation worker (drains feedback jobs first)
# Logs to post_batch.log; the worker logs to gen_worker.log.
set -u
cd "$(dirname "$0")"
log() { echo "[$(date +%H:%M:%S)] $*"; }
CO="Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz"

log "waiting for main batch to finish (regen_all.log -> Done.)"
while [ "$(grep -c '^Done\.' regen_all.log 2>/dev/null)" -lt 1 ]; do sleep 30; done
log "main batch finished"; sleep 5

log "committing id_features.json prompt corrections"
git -C .. add scripts/id_features.json
git -C .. commit -m "Fold reviewer corrections into id_features prompts

$CO" 2>&1 | tail -2
git -C .. push origin main 2>&1 | tail -2

log "applying choices_5.json (keep champions; enqueue re-gens)"
python apply_choices.py choices_5.json 2>&1

log "backfilling review prompt ids"
python backfill_review_ids.py 2>&1
git -C .. add docs/review/manifest.json
git -C .. commit -m "Backfill editable prompt field into review entries

$CO" 2>&1 | tail -2
git -C .. push origin main 2>&1 | tail -2

log "starting continuous generation worker (background)"
nohup python gen_worker.py > gen_worker.log 2>&1 &
log "worker pid $!; post-batch orchestration done"
