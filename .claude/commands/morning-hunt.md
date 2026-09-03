---
description: Run the daily Gauntlet loop. Source, sync the Sheet, review the digest, tailor the yes-set, queue for submission.
---

# /morning-hunt

Run the full daily loop. You (Claude) orchestrate; the human decides yes/no
and does final submission. Everything up to tailoring is plain Python and
costs no API budget. Only the tailoring step uses your reasoning.

## Steps

1. **Source** (deterministic, no LLM):
   ```
   python -m pipeline.source
   ```
   Pulls every registry company, enriches, filters, dedupes, and marks
   postings that vanished from their board. Report the per-company line
   for any `[warn]` or `[skip]` so dead endpoints get noticed.

2. **Sync the Sheet first** if `sheet.enabled` is true, so decisions made
   from the phone are respected before anything else happens:
   ```
   python -m pipeline.sheet_sync
   ```

3. **Build the digest** and show it to the human:
   ```
   python -m pipeline.digest
   ```
   Present the roles conversationally. Target companies and strong-salary
   roles first. Call out every `verify` tag so the human knows what is
   unconfirmed. On the biweekly run use `--kind biweekly` and mention the
   idle yes-roles section.

4. **Get decisions.** Ask which job ids are a `yes`. Record each one:
   ```
   python -m pipeline.decide <id> yes|no|skip
   ```
   Only `yes` roles proceed. Never tailor anything not greenlit.

5. **Tailor the yes-set.** For each `yes` job id without a packet, invoke the
   `resume-tailor` skill. It writes to `output/<company>_<jobid>/` and
   records the application at `queued_for_review` through `pipeline.decide`.

6. **Push the board** so the phone view shows the new state:
   ```
   python -m pipeline.sheet_sync --push
   ```

7. **Report.** Per tailored role, two lines: gauntlet result (pass, or what
   got revised, plus any stretch flag) and the output folder. Remind the
   human these are drafts to review and submit themselves.

## Cadence
- Daily: steps 1 to 4, tailor only if there is a yes-set.
- Biweekly: same, with `--kind biweekly` on the digest.
