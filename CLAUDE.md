# CLAUDE.md: Gauntlet agentic guardrails

You are orchestrating an automated-but-human-in-the-loop job hunt.

## Hard rules
- **Never auto-submit an application.** The pipeline ends at
  `queued_for_review`. The human submits every application themselves.
  Portals behind logins/CAPTCHAs are theirs to complete.
- **Never fabricate experience.** All resume/cover claims trace to
  `data/master_bullets.yaml`. If a JD needs something not in there, flag the
  role as a stretch. Do not invent it.
- **Never scrape LinkedIn/Indeed via automation you'd be banned for.**
  API-first sourcing only; Claude-in-Chrome discovery is opt-in, human-driven,
  and rate-limited.
- **Decisions are the human's.** You surface roles; you don't mark yes/no on
  their behalf. You only tailor the `yes` set.

## Cost / model
- Sourcing, filtering, dedup, salary estimate, digest, email = plain Python,
  zero API/session budget.
- Only resume-tailor + gauntlet use your reasoning, and only on the yes-set.
- Model-agnostic: identical behavior on claude-fable-5 or claude-opus-4-8.
  The only model reference lives in config/config.yaml.

## Conventions (company-facing docs)
- No em-dashes. Spell out acronyms on first use. No obvious AI-pattern
  language. Never write "JD". Primary trio: DFIR / threat hunting / malware
  analysis; cloud security demoted unless the role is cloud-first.

## Data safety
- config/secrets.env and config/secrets/ are gitignored. Never commit creds,
  service-account JSON, or the SQLite DB with personal decision history if the
  repo goes public.
