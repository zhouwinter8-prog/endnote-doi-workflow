from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import PMID_BLOCK, collision_safe_path, iter_paragraphs, load_docx, sha256_file
import prepare

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
CROSSREF_URL = "https://api.crossref.org/works/"

# Crossref type -> EndNote reference type tag
CROSSREF_TYPE_MAP = {
    "journal-article": "Journal Article",
    "book": "Book",
    "book-chapter": "Book Section",
    "book-part": "Book Section",
    "monograph": "Book",
    "edited-book": "Edited Book",
    "reference-entry": "Book Section",
    "proceedings-article": "Conference Proceedings",
    "paper-conference": "Conference Paper",
    "report": "Report",
    "report-component": "Report",
    "dissertation": "Thesis",
    "posted-content": "Journal Article",
    "peer-review": "Journal Article",
    "standard": "Standard",
    "dataset": "Data File",
}

# Matches labeled DOI markers in a manuscript, e.g. "(DOI: 10.1038/x)" or
# "(DOIs: 10.1038/x, 10.1002/y)". An optional https://doi.org/ prefix is allowed.
_DOI_CORE = r"(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/[^\s,;，；)]+"
DOI_BLOCK = re.compile(
    r"\(\s*DOIs?\s*[:：#]?\s*(?P<ids>"
    + _DOI_CORE
    + r"(?:\s*[,;，；]\s*" + _DOI_CORE + r")*)\s*\)",
    re.IGNORECASE,
)
DOI_SPLIT = re.compile(r"\s*[,;，；]\s*")

# Opt-in: catch unlabeled DOIs sitting in the text. Off by default so we only
# ever act on identifiers the author actually marked.
BARE_DOI = re.compile(_DOI_CORE, re.IGNORECASE)


def clean_text(value: Any) -> str:
    """Collapse whitespace and decode HTML entities (Crossref titles contain &amp; etc.)."""
    text = html.unescape(str(value or "")).strip()
    return " ".join(text.split())


def normalize_doi(value: str) -> str:
    """Strip doi.org URL / 'doi:' prefixes and surrounding whitespace."""
    text = str(value or "").strip().strip("<>,;")
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi.org/", "doi:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            break
    return text


def doi_key(value: Any) -> str:
    """Canonical form used ONLY for deciding whether two DOIs are the same."""
    return normalize_doi(value).casefold().rstrip(".")


def looks_like_doi(value: str) -> bool:
    """Reject junk early so we never query the network for something that isn't a DOI."""
    return bool(re.fullmatch(r"10\.\d{4,9}/\S+", normalize_doi(value)))


def is_pmid(token: str) -> bool:
    """A bare all-digit token is treated as a PMID, not a DOI."""
    return bool(re.fullmatch(r"\d{6,8}", token.strip()))


def split_doi_arguments(values: list[str] | None) -> list[str]:
    """Split repeated/comma-separated --doi values into individual tokens."""
    tokens: list[str] = []
    for value in values or []:
        for piece in re.split(r"[,;\s]+", str(value).strip()):
            piece = piece.strip()
            if piece:
                tokens.append(piece)
    return tokens


def read_doi_file(path: Path) -> list[str]:
    """Read DOIs from a plain text file (one per line) or CSV (first column)."""
    text = path.read_text(encoding="utf-8-sig")
    tokens: list[str] = []
    if path.suffix.lower() == ".csv":
        for row in csv.reader(text.splitlines()):
            if row and row[0].strip() and not row[0].strip().startswith("#"):
                tokens.append(row[0].strip())
        return tokens
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            tokens.append(stripped)
    return tokens


def extract_from_docx(path: Path, scan_bare: bool = False) -> tuple[list[str], list[str]]:
    """Scan a manuscript for labeled (DOI: ...) and (PMID: ...) markers.

    Returns (dois, pmids) in document order, deduplicated. The DOCX is only read.
    """
    document = load_docx(path)
    dois: list[str] = []
    pmids: list[str] = []
    seen_doi: set[str] = set()

    for paragraph in iter_paragraphs(document):
        text = paragraph.text
        for match in DOI_BLOCK.finditer(text):
            for token in DOI_SPLIT.split(match.group("ids")):
                doi = normalize_doi(token)
                if doi and doi.lower() not in seen_doi:
                    seen_doi.add(doi.lower())
                    dois.append(doi)
        for match in PMID_BLOCK.finditer(text):
            for token in re.split(r"\s*[,;，；]\s*", match.group("ids")):
                token = token.strip()
                if token and token not in pmids:
                    pmids.append(token)
        if scan_bare:
            # Strip labeled spans first so we never double-count an explicit marker.
            remainder = DOI_BLOCK.sub(" ", text)
            for match in BARE_DOI.finditer(remainder):
                doi = normalize_doi(match.group(0))
                if doi and doi.lower() not in seen_doi:
                    seen_doi.add(doi.lower())
                    dois.append(doi)

    return dois, pmids


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30,
              retries: int = 3) -> dict[str, Any]:
    """GET JSON with backoff on transient failures (429/5xx/timeouts)."""
    last_error: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(
            url, headers=headers or {"User-Agent": "EndNote_Workflow/1.2"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 404 means "not found" — retrying will not help.
            if exc.code == 404:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"network request failed after {retries} attempts: {last_error}")


def resolve_pmid_for_doi(doi: str, email: str | None, api_key: str | None) -> str | None:
    """Resolve a DOI to a PubMed PMID using esearch's [doi] field tag."""
    parameters = {
        "db": "pubmed",
        "term": f"{doi}[doi]",
        "retmode": "json",
        "retmax": "1",
        "tool": "EndNote_Workflow",
    }
    if email:
        parameters["email"] = email
    if api_key:
        parameters["api_key"] = api_key
    try:
        payload = http_json(ESEARCH_URL + "?" + urllib.parse.urlencode(parameters))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    idlist = (payload.get("esearchresult") or {}).get("idlist") or []
    return str(idlist[0]) if idlist else None


def crossref_record(doi: str, email: str | None) -> dict[str, Any]:
    """Fetch metadata from Crossref for DOIs that are not PubMed-indexed."""
    headers = {"User-Agent": "EndNote_Workflow/1.2"}
    if email:
        headers["User-Agent"] += f" (mailto:{email})"
    url = CROSSREF_URL + urllib.parse.quote(doi, safe="")
    message = http_json(url, headers=headers).get("message", {})

    authors: list[str] = []
    for author in message.get("author", []) or []:
        family = clean_text(author.get("family"))
        given = clean_text(author.get("given"))
        if family:
            authors.append(f"{family}, {given}".rstrip(" ,"))
        elif author.get("name"):
            authors.append(clean_text(author["name"]))

    year = ""
    date_parts = ((message.get("issued") or {}).get("date-parts") or [[]])[0]
    if date_parts and date_parts[0]:
        year = str(date_parts[0])

    titles = message.get("title") or []
    containers = message.get("container-title") or []

    return {
        "pmid": "",  # not PubMed-indexed
        "authors": authors,
        "first_author": authors[0].split(",", 1)[0] if authors else "Unknown",
        "year": year,
        "title": clean_text(titles[0]) if titles else "",
        "journal": clean_text(containers[0]) if containers else clean_text(message.get("publisher")),
        "volume": clean_text(message.get("volume")),
        "issue": clean_text(message.get("issue")),
        "pages": clean_text(message.get("page")),
        "doi": clean_text(message.get("DOI")) or doi,
        "requested_doi": doi,
        "reference_type": CROSSREF_TYPE_MAP.get(str(message.get("type") or ""), "Journal Article"),
        "source": "crossref",
    }


def enw_block(record: dict[str, Any]) -> str:
    """Render one EndNote Tagged Import record. Mirrors prepare.enw_text tags."""
    lines = [f"%0 {record.get('reference_type') or 'Journal Article'}"]
    lines.extend(f"%A {author}" for author in record.get("authors", []))
    mapping = (
        ("%D", "year"), ("%T", "title"), ("%J", "journal"),
        ("%V", "volume"), ("%N", "issue"), ("%P", "pages"), ("%R", "doi"),
    )
    for tag, key in mapping:
        value = str(record.get(key, "")).strip()
        if value:
            lines.append(f"{tag} {value}")
    doi = str(record.get("doi", "")).strip()
    if doi:
        lines.append(f"%U https://doi.org/{doi}")

    pmid = str(record.get("pmid", "")).strip()
    if pmid:
        label = f"PMID-{pmid}"
        tail = f"%Z PMID:{pmid}"
    else:
        label = doi or record.get("label", "")
        tail = f"%Z DOI:{doi}"
    lines.extend((f"%M {label}", f"%F {label}", tail))
    return "\n".join(lines)


def enw_text(records: list[dict[str, Any]]) -> str:
    return "\n\n".join(enw_block(record) for record in records) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a batch EndNote import (.enw) from a list of DOIs (PMIDs accepted too)."
    )
    parser.add_argument("--doi", action="append", default=[],
                        help="DOI (repeatable; comma-separated allowed).")
    parser.add_argument("--doi-file", type=Path, help="Text/CSV file of DOIs, one per line.")
    parser.add_argument("--from-docx", type=Path,
                        help="Extract labeled (DOI: ...) / (PMID: ...) markers from a Word DOCX.")
    parser.add_argument("--scan-bare-dois", action="store_true",
                        help="With --from-docx, also pick up unlabeled DOIs in the text (opt-in).")
    parser.add_argument("--outdir", type=Path, default=Path("."), help="Output directory.")
    parser.add_argument("--name", default="endnote-doi-import", help="Output file stem.")
    parser.add_argument("--email", help="Contact email for NCBI/Crossref etiquette.")
    parser.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    parser.add_argument("--no-crossref", action="store_true",
                        help="Skip the Crossref fallback for DOIs not found in PubMed.")
    parser.add_argument("--open-folder", action="store_true")
    args = parser.parse_args()

    raw_tokens = split_doi_arguments(args.doi)
    if args.doi_file:
        if not args.doi_file.is_file():
            raise RuntimeError(f"DOI file not found: {args.doi_file}")
        raw_tokens.extend(read_doi_file(args.doi_file))

    direct_pmids: list[str] = []
    dois: list[str] = []
    seen_doi: set[str] = set()
    invalid: list[str] = []
    for token in raw_tokens:
        if is_pmid(token):
            if token not in direct_pmids:
                direct_pmids.append(token)
            continue
        doi = normalize_doi(token)
        if not looks_like_doi(doi):
            # Not a PMID and not a DOI-shaped string: refuse rather than guess.
            invalid.append(token)
            continue
        if doi.lower() not in seen_doi:
            seen_doi.add(doi.lower())
            dois.append(doi)

    if args.from_docx:
        if not args.from_docx.is_file():
            raise RuntimeError(f"DOCX not found: {args.from_docx}")
        docx_dois, docx_pmids = extract_from_docx(args.from_docx, args.scan_bare_dois)
        print(f"Extracted {len(docx_dois)} DOI marker(s) and {len(docx_pmids)} PMID marker(s) "
              f"from {args.from_docx.name}", flush=True)
        for doi in docx_dois:
            doi = normalize_doi(doi)
            if doi and doi.lower() not in seen_doi:
                seen_doi.add(doi.lower())
                dois.append(doi)
        for pmid in docx_pmids:
            if pmid not in direct_pmids:
                direct_pmids.append(pmid)

    if not dois and not direct_pmids:
        raise RuntimeError(
            "No DOIs or PMIDs supplied. Use --doi, --doi-file, or --from-docx."
        )

    print(f"Resolving {len(dois)} DOI(s) and {len(direct_pmids)} direct PMID(s)...",
          flush=True)

    doi_to_pmid: dict[str, str] = {}
    unresolved: list[str] = []
    for index, doi in enumerate(dois):
        pmid = resolve_pmid_for_doi(doi, args.email, args.api_key)
        if pmid:
            doi_to_pmid[doi] = pmid
            print(f"  [pubmed] {doi} -> PMID {pmid}", flush=True)
        else:
            # Not fatal yet: Crossref may still supply the record below.
            print(f"  [no pmid] {doi} (will try Crossref)", flush=True)
        # NCBI asks for <=3 requests/second without an API key.
        if not args.api_key and index < len(dois) - 1:
            time.sleep(0.34)

    # Records obtained through PubMed keep full fidelity and stay usable with finalize.
    pmids = direct_pmids + [doi_to_pmid[d] for d in dois if d in doi_to_pmid]
    records: dict[str, dict[str, Any]] = {}
    if pmids:
        records = prepare.fetch_pubmed(pmids, args.email, args.api_key)
        for record in records.values():
            record.setdefault("reference_type", "Journal Article")
            record.setdefault("source", "pubmed")

    # Correspondence gate: a PubMed hit is only accepted for a DOI when that
    # record itself carries the same DOI. Otherwise we would import the wrong paper.
    for doi in list(doi_to_pmid):
        pmid = doi_to_pmid[doi]
        record = records.get(pmid)
        if record is None:
            continue
        record_doi = str(record.get("doi") or "").strip()
        if not record_doi:
            record["doi_verified"] = False
            print(f"  [unverified] {doi} -> PMID {pmid} has no DOI field; keeping record",
                  flush=True)
            continue
        if doi_key(record_doi) == doi_key(doi):
            record["doi_verified"] = True
        else:
            print(f"  [MISMATCH] {doi} -> PMID {pmid} carries DOI {record_doi}; "
                  f"discarding this match", flush=True)
            del doi_to_pmid[doi]

    # Order: follow the user's original DOI list, then direct PMIDs.
    # A DOI and a PMID marker can point at the same paper, so dedupe by identity
    # (PMID when known, otherwise DOI) to avoid duplicate EndNote records.
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    duplicates: list[str] = []

    def emit(record: dict[str, Any]) -> bool:
        key = str(record.get("pmid") or "").strip()
        if not key:
            key = str(record.get("doi") or "").strip().lower()
        if not key or key in emitted:
            return False
        emitted.add(key)
        ordered.append(record)
        return True

    audit: list[dict[str, Any]] = []

    def note(input_id: str, kind: str, status: str, resolved_id: str = "",
             title: str = "", match: str = "") -> None:
        audit.append({"input": input_id, "kind": kind, "status": status,
                      "resolved_id": resolved_id, "doi_match": match,
                      "title": " ".join(str(title).split())[:180]})

    for doi in dois:
        pmid = doi_to_pmid.get(doi)
        if pmid and pmid in records:
            record = records[pmid]
            match = "yes" if record.get("doi_verified", False) else "unverified"
            if emit(record):
                note(doi, "doi", "pubmed", pmid, record.get("title", ""), match)
            else:
                duplicates.append(doi)
                note(doi, "doi", "duplicate", pmid, record.get("title", ""), match)
                print(f"  [duplicate] {doi} -> PMID {pmid} (already included)", flush=True)
            continue
        if args.no_crossref:
            unresolved.append(doi)
            note(doi, "doi", "unresolved")
            print(f"  [unresolved] {doi} (not in PubMed, Crossref disabled)", flush=True)
            continue
        try:
            record = crossref_record(doi, args.email)
        except Exception as exc:
            print(f"  [FAILED] {doi}: {exc}", flush=True)
            unresolved.append(doi)
            note(doi, "doi", "unresolved")
            continue
        # Same gate on the Crossref side: refuse a record that is not the requested work.
        if doi_key(record.get("doi")) != doi_key(doi):
            print(f"  [MISMATCH] {doi}: registry returned {record.get('doi')!r}; refusing to import",
                  flush=True)
            unresolved.append(doi)
            note(doi, "doi", "rejected-mismatch", "", record.get("title", ""), "no")
            continue
        if not emit(record):
            duplicates.append(doi)
            note(doi, "doi", "duplicate", "", record.get("title", ""), "yes")
            print(f"  [duplicate] {doi} (already included)", flush=True)
            continue
        print(f"  [crossref] {doi} -> {record['title'][:60]}", flush=True)
        note(doi, "doi", "crossref", "", record.get("title", ""), "yes")

    for pmid in direct_pmids:
        if pmid not in records:
            unresolved.append(pmid)
            note(pmid, "pmid", "unresolved")
            continue
        record = records[pmid]
        if emit(record):
            note(pmid, "pmid", "pubmed", pmid, record.get("title", ""), "yes")
        else:
            duplicates.append(pmid)
            note(pmid, "pmid", "duplicate", pmid, record.get("title", ""), "yes")
            print(f"  [duplicate] PMID {pmid} (already included)", flush=True)

    for token in invalid:
        note(token, "unknown", "invalid")

    if not ordered:
        raise RuntimeError("No records could be resolved from the supplied identifiers.")

    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    enw_path = collision_safe_path(outdir / f"{args.name}.enw")
    state_path = collision_safe_path(outdir / f"{args.name}.state.json")
    enw_path.write_text(enw_text(ordered), encoding="utf-8")

    # Machine- and human-checkable proof that every record corresponds to its input.
    audit_path = collision_safe_path(outdir / f"{args.name}.audit.csv")
    with audit_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["input", "kind", "status", "resolved_id", "doi_match", "title"]
        )
        writer.writeheader()
        writer.writerows(audit)

    state = {
        "schema_version": 1,
        "mode": "doi-batch",
        "requested_dois": dois,
        "direct_pmids": direct_pmids,
        "doi_to_pmid": doi_to_pmid,
        "unresolved": sorted(set(unresolved)),
        "records": {
            (record.get("pmid") or record.get("doi")): {
                "pmid": record.get("pmid", ""),
                "doi": record.get("doi", ""),
                "first_author": record.get("first_author", ""),
                "year": str(record.get("year", "")),
                "title": record.get("title", ""),
                "source": record.get("source", ""),
            }
            for record in ordered
        },
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.open_folder:
        from common import reveal_file
        reveal_file(enw_path)

    print("\n--- correspondence audit (input -> resolved record) ---", flush=True)
    for entry in audit:
        print(f"  {entry['status']:<18} {entry['input'][:46]:<46} "
              f"{entry['title'][:52]}", flush=True)
    print(flush=True)

    print(json.dumps({
        "ok": True,
        "action": "prepare-doi",
        "enw": str(enw_path),
        "state": str(state_path),
        "audit": str(audit_path),
        "requested": len(dois) + len(direct_pmids),
        "records_written": len(ordered),
        "resolved_via_pubmed": sum(1 for r in ordered if r.get("source") == "pubmed"),
        "resolved_via_crossref": sum(1 for r in ordered if r.get("source") == "crossref"),
        "unresolved": sorted(set(unresolved)),
        "duplicates_skipped": sorted(set(duplicates)),
        "next": "Import the ENW into EndNote: File > Import > File, Import Option: EndNote Import, "
                "Text Translation: Unicode (UTF-8).",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=__import__("sys").stderr)
        raise SystemExit(2)
