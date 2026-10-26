# EndNote DOI Workflow

**Turn a list of DOIs — or the DOI links already sitting in your Word manuscript — into a single file you can import straight into EndNote.**

Most reference workflows force you to hand-feed one identifier at a time. This skill takes a DOI list (or a `.docx`) and emits one EndNote Tagged Import file (`.enw`) that EndNote swallows in a single `File > Import`. It resolves each DOI through PubMed first and falls back to Crossref, and it **refuses to import a record it cannot prove is the right paper**.

```bash
python scripts/workflow_cli.py prepare-doi --from-docx manuscript.docx --scan-bare-dois --outdir out
# -> out/manuscript.enw  ->  EndNote: File > Import > File
```

---

## Why this exists

The upstream project this grew out of only accepted **PMIDs** typed into a Word document as `(PMID: 12345678)`. In real manuscripts you almost never have PMIDs — you have DOIs, usually dangling at the end of each reference as `https://doi.org/10.xxxx/...`.

So the DOI path was added from scratch:

| Capability | Upstream | This repo |
|---|---|---|
| `(PMID: …)` markers in DOCX → ENW | Yes | Yes (unchanged) |
| EndNote 20 temp-citation DOCX (Phase 2) | Yes | Yes (unchanged) |
| **DOI list → ENW** | No | **Yes** |
| **Scan DOI links out of a DOCX** | No | **Yes** |
| **DOI correspondence verification** | n/a | **Yes** |
| Crossref fallback for non-PubMed DOIs | No | **Yes** |
| Audit trail you can check before importing | No | **Yes** |

---

## Three ways in

```bash
# 1. From a text/CSV file of DOIs (one per line, `#` comments ok)
python scripts/workflow_cli.py prepare-doi --doi-file dois.txt --outdir out

# 2. Straight on the command line (repeatable, comma-separated)
python scripts/workflow_cli.py prepare-doi --doi "10.1038/s41586-021-03819-2,10.1002/ccr3.4439" --outdir out

# 3. From the DOI links in a Word manuscript (read-only, never modifies your file)
python scripts/workflow_cli.py prepare-doi --from-docx manuscript.docx --outdir out
```

See [`examples/dois.txt`](examples/dois.txt) for a sample input file.

### What gets recognised

| Marker style | Supported |
|---|---|
| `(DOI: 10.1038/s41586-021-03819-2)` | Yes |
| `(DOIs: 10.1002/ccr3.4439, 10.1103/PhysRevLett.116.061102)` | Yes |
| `(DOI：10.1145/...)` (full-width colon) | Yes |
| `https://doi.org/10.1038/...` bare link | Yes — with `--scan-bare-dois` |
| `(PMID: 34265844)` | Yes |
| Unlabeled DOI in running text | Opt-in: `--scan-bare-dois` |
| Plain all-digit token | Treated as a PMID, so mixed DOI/PMID lists work |

### Useful flags

| Flag | Purpose |
|---|---|
| `--outdir DIR` | Where to write outputs |
| `--name STEM` | Output filename stem (defaults to the input name) |
| `--scan-bare-dois` | Also catch unlabeled DOIs (off by default — never guess silently) |
| `--no-crossref` | PubMed only; skip the Crossref fallback |
| `--email YOU@example.com` | NCBI/Crossref polite-pool identification |
| `--api-key KEY` / `NCBI_API_KEY` | Raises the NCBI rate limit (removes the 3 req/s throttle) |

---

## Getting it into EndNote

1. Run any command above → you get a `.enw` file.
2. In EndNote: **File → Import → File**
3. Pick the `.enw`, set **Import Option: `EndNote Import`**, **Text Translation: `Unicode (UTF-8)`**
4. Import. Done — every reference lands in one pass.

> The scripts never write to your EndNote library. They only produce the import file. Reading your library for Record Numbers (Phase 2) is deliberately read-only.

---

## Correctness: it will not import the wrong paper

This was the design priority. A DOI→PMID lookup that silently returns a *different* paper would quietly pollute your library, so every resolution is gated:

1. **PubMed gate** — a PubMed hit is accepted for a requested DOI **only if that record's own DOI equals the DOI you asked for**. Otherwise the match is discarded and the item reported as unresolved.
2. **Crossref gate** — same rule on the Crossref fallback.
3. **Input validation** — anything that is neither a plausible DOI nor a PMID is flagged `invalid` and never sent to the network.
4. **Identity dedupe** — if a DOI and a PMID marker resolve to the same paper, it is written once and logged under `duplicates_skipped`.

**Nothing is guessed.** A DOI that both PubMed and Crossref fail to resolve is reported — never fabricated.

### Verify before you import

Every run also writes an `*.audit.csv` (UTF-8 with BOM, opens cleanly in Excel) listing, per input:

```
input,kind,status,resolved_id,doi_match,title
10.1038/s41586-021-03819-2,doi,pubmed,34265844,yes,Highly accurate protein structure prediction with AlphaFold.
10.1145/3292500.3330675,doi,crossref,,yes,Whole Page Optimization with Global Constraints
```

Skim that table, confirm the titles are the papers you meant, then import. A printed version appears in the terminal too.

---

## Resolution strategy

DOIs are resolved in two tiers so that both biomedical and non-biomedical literature work:

1. **PubMed first** — DOI → PMID via NCBI E-utilities. Richest metadata, and the record keeps a `PMID-<id>` label so it also works with the Phase 2 Word temp-citation flow.
2. **Crossref fallback** — for DOIs PubMed does not index (books, conference proceedings, many engineering and social-science journals). EndNote reference types are mapped properly (`Book Section`, `Conference Proceedings`, …) and the DOI itself is used as the label.

Network hiccups (429 / 5xx / timeouts) are retried with backoff; hard 404s are not. Without an API key the tool paces itself to NCBI's requested 3 req/s.

---

## The original two-phase flow (still here, unchanged)

If you work from PMIDs in Word instead:

```bash
# Phase 1 — DOI/PMID markers -> importable .enw
python scripts/workflow_cli.py prepare manuscript.docx --outdir out

# Phase 2 — after you import into EndNote: replace markers with real
#           EndNote temporary citations using your library's Record Numbers
python scripts/workflow_cli.py finalize manuscript.docx --state out/manuscript.state.json
```

Phase 2 reads `<library>.enl`'s sidecar SQLite database read-only to look up Record Numbers. It requires **EndNote 20 for Windows** and an already-imported library.

---

## Requirements

- Python 3.10+
- `python-docx` (only needed for the DOCX entry points)

```bash
pip install python-docx
```

`requests` is not required — the scripts use only the standard library.

---

## Install as a skill

This repo is a standard `SKILL.md` skill. Drop the folder into your agent's skills directory:

- **Claude Code**: `~/.claude/skills/endnote-doi-workflow/`
- **WorkBuddy**: `~/.workbuddy/skills/endnote-doi-workflow/`

Restart the session and invoke it by name. You can also just use it as a plain CLI — nothing about the scripts depends on any agent runtime.

---

## Repository layout

```
endnote-doi-workflow/
├── SKILL.md                    # skill definition & agent instructions
├── README.md
├── scripts/
│   ├── workflow_cli.py         # entry point: prepare | prepare-doi | finalize
│   ├── prepare_doi.py          # Phase 0 — DOI batch import (added here)
│   ├── prepare.py              # Phase 1 — PMID markers -> ENW
│   ├── finalize.py             # Phase 2 -> temp-citation DOCX
│   ├── common.py               # shared parsing / ENW emission
│   ├── endnote_library.py      # read-only EndNote library access
│   └── privacy_audit.py
├── references/user-workflow.md
└── examples/dois.txt
```

---

## Upstream and credits

This project is derived from **[xfeng3097-beep/codex-endnote-workflow](https://github.com/xfeng3097-beep/codex-endnote-workflow)** (MIT), which authored the original two-stage PMID workflow preserved here in `prepare.py`, `finalize.py`, `common.py`, `endnote_library.py` and `privacy_audit.py`.

Work added in this repo: the entire DOI entry path (`prepare_doi.py`), the Crossref fallback, correspondence verification, the audit trail, and deduplication.

Metadata comes from **NCBI E-utilities** and **Crossref** — please respect their usage policies and consider passing `--email` or an NCBI API key for bulk runs.

---

## License

MIT. See [LICENSE](LICENSE). The upstream MIT copyright notice is preserved alongside this repository's.
