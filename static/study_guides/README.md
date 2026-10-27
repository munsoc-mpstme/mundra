# Backend-Hosted Study Guides

Drop late-arriving or updated committee study guide PDFs into this folder named after the committee (for example `UNSC.pdf` or `IPC.pdf`).

- The `/study_guides` endpoint automatically detects any `<COMMITTEE>.pdf` file in `static/study_guides/` and exposes `/static/study_guides/<COMMITTEE>.pdf` to the Delego app.
- Alternatively, you can set an external or custom PDF URL in `data/study_guides.json`.
