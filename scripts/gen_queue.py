"""Prioritized image-generation job queue (local state, gitignored).

gen_queue.json is a list of job dicts. Lower `priority` runs first; ties break
FIFO by insertion order (`seq`). A job:

  {seq, code, pose, kind, n_new, seed_off, refetch, priority, reason}

kinds:
  - "challengers": keep the live champion, generate `n_new` fresh variant slots
    next to it (the reviewer compares Current vs the new suggestions).
  - "regen": regenerate all 3 variant slots ("none good enough"); live untouched.
  - "coverage": first-time best-of-3 for a never-generated species; sets live.

Priorities: feedback-driven jobs use FEEDBACK (0); the "cover every species"
backlog uses COVERAGE (10), so feedback always jumps the queue.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "gen_queue.json")

FEEDBACK = 0
COVERAGE = 10


def load():
    if os.path.exists(QUEUE):
        try:
            return json.load(open(QUEUE, encoding="utf-8"))
        except Exception:
            return []
    return []


def save(jobs):
    json.dump(jobs, open(QUEUE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _next_seq(jobs):
    return max((j.get("seq", 0) for j in jobs), default=0) + 1


def enqueue(jobs, code, kind, pose="sitting", n_new=2, seed_off=0,
            refetch=False, priority=FEEDBACK, reason=""):
    """Add/replace a job. At most one pending job per (code, pose); a newer
    enqueue replaces an older one (so feedback supersedes a coverage backlog
    entry, and the latest feedback wins). Returns the updated list."""
    jobs = [j for j in jobs if not (j["code"] == code and j.get("pose") == pose)]
    jobs.append({"seq": _next_seq(jobs), "code": code, "pose": pose, "kind": kind,
                 "n_new": n_new, "seed_off": seed_off, "refetch": bool(refetch),
                 "priority": priority, "reason": reason})
    return jobs


def pop(jobs):
    """Return (highest-priority job, remaining jobs). (None, jobs) if empty."""
    if not jobs:
        return None, jobs
    order = sorted(jobs, key=lambda j: (j.get("priority", 0), j.get("seq", 0)))
    head = order[0]
    rest = [j for j in jobs if j is not head]
    return head, rest
