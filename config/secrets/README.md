# config/secrets/ (gitignored)

Place here:
- `gsheets_sa.json`: Google service-account key for the Sheet mirror.

Setup:
1. Google Cloud console -> new project -> enable Google Sheets API and Google
   Drive API -> IAM -> service account -> create JSON key -> save it here as
   `gsheets_sa.json`.
2. Open the JSON and copy `client_email`.
3. Create a Google Sheet. Share it with that client_email as Editor.
4. Put the Sheet ID (from its URL) in `config/secrets.env` as `SHEET_ID=...`.
   See `config/secrets.env.example`.
5. Run `python -m pipeline.sheet_sync --init`.
