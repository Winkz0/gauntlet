# Gauntlet

An automated, human-in-the-loop job search pipeline for security operations
roles. Plain Python sources and filters postings from public applicant
tracking system (ATS) APIs for the companies you name, plus a general
keyword search (Adzuna and USAJobs) for everyone else. A Claude Code skill
tailors a resume and cover letter for each role the human says yes to, then
attacks its own draft with the screening prompts recruiters run. The human
reviews and submits every application. Nothing is ever auto-submitted.

```
registry.yaml ──► adapters/boards.py ──► enrich ──► filter gate ──► SQLite
  (companies)      (Workday, Greenhouse,   (salary,   (deterministic   (source of
                    Lever, Ashby, Eightfold, remote,    rules, no LLM)   truth)
                    Amazon, SmartRecruiters) hire type)                    │
                                                                           ▼
   phone ◄──► Google Sheet mirror ◄──── sheet_sync ◄────────────┬──── digest (markdown / email)
              (pipeline + archive tabs)                         │
                                                                ▼
                               human says yes ──► /morning-hunt ──► resume-tailor skill
                                                                    ├── resume.docx / .pdf
                                                                    ├── cover_letter.docx / .pdf
                                                                    ├── interview_defense.md
                                                                    └── gauntlet.json
                                                                          │
                                                          status: queued_for_review
                                                          (the human submits)
```

## Why it exists

Job boards are noisy, and resume tailoring done badly produces keyword soup
that recruiter-side AI screens flag. This project separates the two problems:

- **Sourcing and filtering are boring and deterministic.** No model calls.
  Regex, a config file, and a SQLite database. Runs on a schedule for free.
- **Tailoring is where reasoning matters, and it is adversarial.** The skill
  drafts, then runs six recruiter screening prompts plus a rejection-email
  simulation against its own output, and revises once toward authenticity.
  Every claim must trace to a bullet in the candidate's master bullet
  library. Missing experience is flagged as a stretch, never invented.

## Layout

```
adapters/
  boards.py            one function per ATS, all returning the same dict shape
  search.py            general search across all employers (Adzuna, USAJobs), same dict shape
  registry.yaml        which companies to pull and how
pipeline/
  config.py            loads config.yaml and overlays config/secrets.env
  source.py            daily driver: pull, enrich, filter, store, mark gone
  enrich.py            salary / remote / hire type / seniority heuristics
  filters.py           the gate; every reason is a dict with an optional tag
  salary_osint.py      geo-average fallback when a posting lists no salary
  store.py             SQLite layer, dedup, migrations, state model
  digest.py            daily / biweekly markdown digest, optional email
  decide.py            terminal triage and stage updates
  sheet_sync.py        two-tab Google Sheet mirror with retention
data/
  schema.sql
  master_bullets.yaml  the candidate's bullet library (gitignored; see below)
.claude/
  commands/morning-hunt.md    the daily loop, run as /morning-hunt
  skills/resume-tailor/       the tailoring + gauntlet skill
adversarial_checks_seed_prompts.md   the screening prompts the gauntlet uses
tests/                 pytest suite for enrich, filters, store, sheet retention
```

## State model

| Table | Field | Values | Set by |
|---|---|---|---|
| jobs | board_status | live, gone | sourcing run, when a posting disappears from its board |
| decisions | decision | yes, no, skip | the human (terminal or Sheet) |
| applications | status | drafted, queued_for_review, applied, interviewing, offer, rejected, closed | the skill (to queued_for_review) and the human after that |

A job appears in the digest while it is live, passed the gate, and has no
decision. A yes creates an application row when the skill runs. The Sheet
shows both decision and stage as dropdowns.

## The Google Sheet

Two tabs, rewritten from the database on every push:

- **pipeline**: live postings you have not archived. Two editable columns,
  `decision` and `stage`. Clearing a decision cell undoes it.
- **archive**: rows that retired. A row moves here when it is untouched for
  `sheet.archive_after_days` (default 8), gone from its board for
  `sheet.gone_grace_days`, said no to, or in a terminal stage. Nothing is
  deleted, and edits on the archive tab are still read back.

Rows are matched on job id, so sorting and filtering on a phone is safe.
Push always pulls first so a phone edit is never overwritten.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp config/secrets.env.example config/secrets.env      # fill in SHEET_ID, EMAIL_TO, SMTP_*
cp data/master_bullets.example.yaml data/master_bullets.yaml   # then replace with your history
# Optional Sheet mirror: follow config/secrets/README.md, then
python -m pipeline.sheet_sync --init
```

Edit `config/config.yaml` for salary floor, remote policy, metro, keyword
lists, and blocklists. Preferences you would rather not commit go in
`config/config.local.yaml` (gitignored), which is layered over it; see
`config/config.local.example.yaml`. Edit `adapters/registry.yaml` to add
companies.
PDF rendering of packets uses LibreOffice (`soffice --headless`).

## Daily use

```bash
python -m pipeline.source                  # pull everything (cron this)
python -m pipeline.source --only Keeper    # one company, no general search
python -m pipeline.source --no-search      # registry companies only
python -m pipeline.source --search-only    # general search only
python -m pipeline.source --refilter       # re-run the gate after editing config, no network
python -m pipeline.digest                  # what is new; --kind biweekly adds idle yes-roles
python -m pipeline.decide 42 yes           # or no / skip / clear
python -m pipeline.decide 42 stage applied
python -m pipeline.decide list
python -m pipeline.sheet_sync              # pull phone edits, rewrite both tabs
python -m pipeline.sheet_sync --dry-run    # show which tab each row lands on
```

Inside Claude Code, `/morning-hunt` runs the whole loop and invokes the
`resume-tailor` skill for the yes-set. Packets land in
`output/<Company>_<jobid>/`.

## General search

The registry only covers companies someone added by hand. After the registry
pull, `pipeline.source` sends every query under `role_families` in
`config/config.yaml` to two official job search APIs with free keys:

- **Adzuna** (developer.adzuna.com): aggregates US postings across boards.
  Two calls per query, one for remote postings and one near
  `location_context`, capped by `search.adzuna.max_calls` to stay inside the
  free tier (250 calls a day, 2,500 a month). It returns a snippet, not the
  full posting, and sometimes a predicted salary, which is tagged as an
  estimate.
- **USAJobs** (developer.usajobs.gov): federal postings, always with a
  salary. Announcements open only to current federal employees are skipped.

Put the keys in `config/secrets.env` (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`USAJOBS_API_KEY`, `USAJOBS_EMAIL`); a source without keys is skipped.

Role families also decide what a title means. A title belongs to a family
when it contains every word of one variant in any order, so `soc analyst`
matches "Analyst, SOC" and "SOC Tier 2 Analyst", and `threat intel*` matches
"Cyber Threat Intelligence Analyst". A family can set its own `min_level`
and tag titles that state no level; see `config/config.local.example.yaml`.

Companies are split in two:

- **Named**: in the registry or `targets_preferred`. A search result at a
  named company with a working adapter is dropped, since the adapter pulls
  the full posting; one at a named company without an adapter (Google,
  Rapid7) is renamed to match and ranks with the named group.
- **Unnamed**: everyone else. The digest lists them after the named group,
  tagged `general_search`, and `filters.unnamed_require_salary` holds them to
  the salary floor: a listed or estimated salary whose top clears
  `salary_floor`, never "not listed".

A search result no run has returned for `search.expire_after_days` is marked
gone, since a query only returns recent postings.

## Adding a board

Every adapter takes the registry entry and returns a list of the same dict
(`source, source_job_id, company, title, location, url, description, raw`).
Workday, Eightfold, and Amazon are search-driven and fetch one detail page
per posting, so they take an optional `search_terms` list. See the header of
`adapters/boards.py` for the fields each board type needs. To keep a company
out of the committed registry, list it under `companies+:` in
`adapters/registry.local.yaml` (gitignored) instead.

## Guardrails

- Never auto-submit. The pipeline ends at `queued_for_review`.
- Never fabricate. Every resume and cover letter claim traces to
  `data/master_bullets.yaml`; the gauntlet output records what could not be
  claimed and why.
- API-first sourcing only. No LinkedIn or Indeed scraping.
- Decisions are the human's. The pipeline surfaces and tailors; it does not
  choose.

## Personal data

`data/master_bullets.yaml`, `data/gauntlet.db`, `output/`, and everything under
`config/secrets*` are gitignored. `data/master_bullets.example.yaml` is a
fictional bullet library (all names, employers, and metrics invented) that
shows the structure the skill reads: candidate, experience, bullets with
roles / tools / skills / metric / evidence, certifications, tool_inventory,
skills. Copy it to `data/master_bullets.yaml` and replace every entry with
your own history before tailoring anything.

## Tests

```bash
pytest -q
```

## License

MIT. See `LICENSE`.
