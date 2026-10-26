---
name: endnote-workflow
description: Convert explicitly labeled PubMed PMIDs in a Word DOCX into EndNote Tagged Import records and, after import, a separate EndNote 20 temporary-citation DOCX using the library's actual Record Numbers. Use for the supplied two-stage EndNote 20 workflow; do not use for automatic literature discovery or direct EndNote database modification.
---

# EndNote Workflow

Prefer the bundled Windows executable when `bin/EndNote_Workflow.exe` exists. Resolve it relative to this `SKILL.md` and invoke it by its absolute quoted path. In a source checkout without the executable, use Python 3 with `scripts/workflow_cli.py`. Preserve the source DOCX and treat the EndNote library as read-only.

This workflow is tested with EndNote 20 for Windows and Microsoft Word desktop with Cite While You Write. Do not claim support for other EndNote versions, EndNote for macOS, or EndNote Web. EndNote library formats may change between versions.

## Choose the phase

- If the user supplies a **list of DOIs** (a text/CSV file, or identifiers on the command line) and wants them imported into EndNote, run **Phase 0: batch DOI import**. This is the only entry point that does not require a DOCX.
- If the user has supplied a DOCX containing `(PMID: 12345678)` or `(PMIDs: 12345678, 23456789)` and has not imported references yet, run Phase 1.
- If the user says the ENW was imported, run Phase 2 with the original DOCX, Phase 1 state JSON, and the selected `.enl` library.
- In the normal packaged workflow, look in `Tasks/My_Manuscript` or the user-named folder under `Tasks`. Ignore `.txt`, `.enw`, `.json`, `~$*.docx`, and `*.endnote-ready*.docx`; process the only remaining source DOCX.
- If a required path is unknown, locate it in the current project first. Ask only when several plausible source documents or libraries remain.

## Phase 0: batch import from a DOI list

Use this when the user hands over DOIs and wants them in EndNote. It produces one `.enw` containing every resolved record, imported in a single pass. No DOCX is needed.

Source fallback on Windows:

```text
py -3 scripts\workflow_cli.py prepare-doi --doi-file DOIS.txt --outdir DIR
```

Input options:

- `--doi-file DOIS.txt` — one DOI per line (`#` comments and blank lines ignored; `.csv` reads the first column).
- `--doi "10.1038/s41586-021-03819-2,10.1002/ccr3.4439"` — repeatable, comma-separated.
- `--from-docx MANUSCRIPT.docx` — scan the manuscript itself for labeled `(DOI: …)`, `(DOIs: …, …)`, and `(PMID: …)` markers. Handles full-width colons and `https://doi.org/` prefixes. The DOCX is only read, never modified.
- `--scan-bare-dois` (only with `--from-docx`) — opt-in: also pick up **unlabeled** DOIs in the body text. Off by default so only identifiers the author actually marked are used.
- A bare all-digit token is treated as a **PMID** instead of a DOI, so mixed lists work.

Resolution strategy, in order:

1. Each DOI is resolved to a PMID through PubMed `esearch` with the `[doi]` field tag. PubMed records keep full fidelity and their `PMID-<id>` label, so they remain usable with Phase 1/2 later.
2. DOIs **not** indexed in PubMed fall back to **Crossref** so every supplied DOI still yields a record. These are labeled with the DOI itself (`%M <doi>`) and are import-only — they cannot receive Word temporary citations, because a Record Number only exists after import.

Useful flags: `--outdir` (output folder), `--name` (output stem), `--email` (NCBI/Crossref etiquette), `--api-key`/`NCBI_API_KEY` (raises the NCBI rate limit), `--no-crossref` (PubMed-only).

Smoothness: transient network failures (rate limits, 5xx, timeouts) are retried with backoff; a real 404 is not. Without an API key the script paces itself to NCBI's 3 requests/second. For long lists, supply `--api-key` (or set `NCBI_API_KEY`) to remove the pacing delay.

Behavior and reporting:

- Never guess. A DOI that PubMed and Crossref both fail to resolve is reported under `unresolved` and is simply absent from the ENW.
- **Every record is verified against the identifier that requested it.** A PubMed hit is accepted for a DOI only when that record itself carries the same DOI; a Crossref hit is accepted only when the registry returns the same DOI. A mismatch is discarded and the item is reported, never imported. This is the guard against importing the wrong paper — do not bypass it.
- Input that is neither a PMID nor a DOI-shaped string is flagged `invalid` and never queried.
- A `*.audit.csv` is written beside the ENW listing, per input: status, resolved ID, `doi_match`, and title. Show or open it so the user can confirm each record corresponds before importing.
- Deduplicate by identity before writing. A DOI marker and a PMID marker can point at the same paper; the second occurrence is skipped and listed under `duplicates_skipped`, so the library never gets duplicate records.
- Report `resolved_via_pubmed` and `resolved_via_crossref` counts so the user knows which records came from which source.
- Stop and tell the user when nothing resolves, rather than emitting an empty file.

Then tell the user to import the generated `.enw` the same way as Phase 1 (`File > Import > File`, `Import Option: EndNote Import`, `Text Translation: Unicode (UTF-8)`). This phase never touches the EndNote library.

## Phase 1: prepare the import

Run the bundled executable:

```text
bin\EndNote_Workflow.exe prepare INPUT.docx --open-folder
```

Source fallback on Windows:

```text
py -3 scripts\workflow_cli.py prepare INPUT.docx --open-folder
```

The default output directory is the source DOCX directory. Return the generated `.enw` path first. Tell the user to open the intended EndNote library and import that file with `File > Import > File`, `Import Option: EndNote Import`, `Text Translation: Unicode (UTF-8)`. Stop after this handoff; Record Numbers do not exist until import completes.

Do not include an email address unless the user explicitly supplies one for PubMed API etiquette. The script never stores that value in the state file.

## Phase 2: create the Word handoff

After the user confirms import, run:

```text
bin\EndNote_Workflow.exe finalize INPUT.docx --state TASK.endnote-state.json --open
```

Source fallback on Windows:

```text
py -3 scripts\workflow_cli.py finalize INPUT.docx --state TASK.endnote-state.json --open
```

The output uses the supplied EndNote build's standard temporary-citation convention:

```text
{Author, Year #RecordNumber}
```

If `--library` is omitted, the executable first uses EndNote's most recent valid library and then a single local `.enl`; it stops if neither choice is unambiguous. The default output is a collision-safe `.endnote-ready.docx` beside the source. Before delivery, require the finalizer to report zero unresolved PMIDs and confirm the source DOCX hash is unchanged. Tell the user to keep that library open and click `EndNote 20 > Update Citations and Bibliography` once in the automatically opened output.

## Invariants

- Process only explicitly labeled PMIDs. Do not infer citations from arbitrary numbers.
- Never edit the input DOCX in place.
- Never write to `.enl`, `.Data`, `sdb.eni`, or other EndNote-owned files.
- Match imported records by exact PMID/Accession Number or Label, then verify DOI, first-author surname, and year when available.
- Stop on missing or conflicting library matches. If duplicate PMID records have identical author, year, title, and DOI metadata, use the newest Record Number; otherwise never guess.
- Do not claim that the result contains dynamic citations before the user runs Word's EndNote update command.
- Do not expose local absolute paths in reusable state files or reports.
- Never overwrite prior outputs. Use the scripts' collision-safe default paths.

For user-facing instructions and troubleshooting, read [references/user-workflow.md](references/user-workflow.md).

This skill is distributed under the repository's MIT License.
