from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from common import collision_safe_path, load_docx, open_file, replace_labeled_pmids, scan_groups, sha256_file, unique_pmids
from endnote_library import discover_library, find_by_pmids


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def normalized_surname(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def verify_record(pmid: str, expected: dict[str, Any], actual: dict[str, Any]) -> None:
    expected_doi = normalize_doi(expected.get("doi"))
    actual_doi = normalize_doi(actual.get("doi"))
    if expected_doi and actual_doi and expected_doi != actual_doi:
        raise RuntimeError(f"PMID {pmid} DOI mismatch between PubMed metadata and EndNote")
    expected_year = str(expected.get("year") or "").strip()
    if expected_year and actual["year"] and expected_year != actual["year"]:
        raise RuntimeError(f"PMID {pmid} year mismatch between PubMed metadata and EndNote")
    expected_author = normalized_surname(expected.get("first_author"))
    actual_author = normalized_surname(actual.get("first_author"))
    if expected_author and actual_author and expected_author != actual_author:
        raise RuntimeError(f"PMID {pmid} first-author mismatch between PubMed metadata and EndNote")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create EndNote Record Number citations in a DOCX.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--library", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    output = collision_safe_path(args.output or args.input.with_name(f"{args.input.stem}.endnote-ready.docx"))
    if output.resolve() == args.input.resolve():
        raise ValueError("Output must be separate from the input DOCX")

    state = json.loads(args.state.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported state schema")
    source_hash = sha256_file(args.input)
    if source_hash != state.get("input_sha256"):
        raise RuntimeError("The input DOCX does not match the Phase 1 source hash")

    document = load_docx(args.input)
    groups = scan_groups(document)
    pmids = unique_pmids(groups)
    if pmids != state.get("unique_pmids"):
        raise RuntimeError("The PMID sequence no longer matches the Phase 1 state")

    if args.library:
        library = args.library
        library_source = "explicit"
    else:
        library, library_source = discover_library(args.input.parent)
    resolved = find_by_pmids(library, pmids)
    metadata = state.get("records", {})
    for pmid in pmids:
        verify_record(pmid, metadata.get(pmid, {}), resolved[pmid])

    def citation_for(group: list[str]) -> str:
        parts = []
        for pmid in group:
            record = resolved[pmid]
            author = record["first_author"] or metadata.get(pmid, {}).get("first_author") or "Unknown"
            year = record["year"] or metadata.get(pmid, {}).get("year") or "0000"
            parts.append(f"{author}, {year} #{record['rec_number']}")
        return "{" + "; ".join(parts) + "}"

    replacement_count = replace_labeled_pmids(document, citation_for)
    if replacement_count != len(groups):
        raise RuntimeError("Not every labeled PMID group was replaced")
    if scan_groups(document):
        raise RuntimeError("Labeled PMID markers remain after replacement")

    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    if sha256_file(args.input) != source_hash:
        raise RuntimeError("The source DOCX changed during finalization")
    if args.open:
        open_file(output)

    print(json.dumps({
        "ok": True,
        "action": "finalize",
        "output": str(output),
        "library_source": library_source,
        "citation_groups": replacement_count,
        "unique_pmids": len(pmids),
        "record_numbers": {pmid: resolved[pmid]["rec_number"] for pmid in pmids},
        "unresolved_pmids": [],
        "source_unchanged": True,
        "next": "Open the output in Word and click EndNote 20 > Update Citations and Bibliography.",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
