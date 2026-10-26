from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from common import collision_safe_path, load_docx, reveal_file, scan_groups, sha256_file, unique_pmids


EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
YEAR_PATTERN = re.compile(r"\b(19|20)\d{2}\b")


def text_of(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def first_year(article: ET.Element) -> str:
    for path in (
        ".//JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//JournalIssue/PubDate/MedlineDate",
    ):
        value = text_of(article.find(path))
        match = YEAR_PATTERN.search(value)
        if match:
            return match.group(0)
    return ""


def parse_article(article: ET.Element) -> dict[str, Any]:
    pmid = text_of(article.find(".//MedlineCitation/PMID"))
    article_node = article.find(".//MedlineCitation/Article")
    if article_node is None or not pmid:
        raise ValueError("PubMed response contains an article without a PMID")

    authors: list[str] = []
    for author in article_node.findall(".//AuthorList/Author"):
        collective = text_of(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        last = text_of(author.find("LastName"))
        fore = text_of(author.find("ForeName")) or text_of(author.find("Initials"))
        if last:
            authors.append(f"{last}, {fore}".rstrip(" ,"))

    doi = ""
    for identifier in article.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if identifier.attrib.get("IdType", "").casefold() == "doi":
            doi = text_of(identifier)
            break

    return {
        "pmid": pmid,
        "authors": authors,
        "first_author": authors[0].split(",", 1)[0] if authors else "Unknown",
        "year": first_year(article),
        "title": text_of(article_node.find("ArticleTitle")),
        "journal": text_of(article_node.find("Journal/Title")),
        "volume": text_of(article_node.find("Journal/JournalIssue/Volume")),
        "issue": text_of(article_node.find("Journal/JournalIssue/Issue")),
        "pages": text_of(article_node.find("Pagination/MedlinePgn")),
        "doi": doi,
    }


def fetch_pubmed(pmids: list[str], email: str | None, api_key: str | None) -> dict[str, dict[str, Any]]:
    parameters = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "EndNote_Workflow",
    }
    if email:
        parameters["email"] = email
    if api_key:
        parameters["api_key"] = api_key
    request = Request(
        EFETCH_URL + "?" + urlencode(parameters),
        headers={"User-Agent": "EndNote_Workflow/1.2"},
    )
    with urlopen(request, timeout=30) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    records = {record["pmid"]: record for record in map(parse_article, root.findall("PubmedArticle"))}
    missing = [pmid for pmid in pmids if pmid not in records]
    if missing:
        raise RuntimeError("PubMed did not return: " + ", ".join(missing))
    return records


def load_metadata_fixture(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records", payload)
    if not isinstance(records, dict):
        raise ValueError("Metadata fixture must be an object keyed by PMID")
    return {str(key): value for key, value in records.items()}


def enw_text(records: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for record in records:
        lines = ["%0 Journal Article"]
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
        key = f"PMID-{record['pmid']}"
        lines.extend((f"%M {key}", f"%F {key}", f"%Z PMID:{record['pmid']}"))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an EndNote import from labeled PMIDs in DOCX.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--outdir", type=Path)
    parser.add_argument("--email")
    parser.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"))
    parser.add_argument("--metadata-json", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--open-folder", action="store_true")
    args = parser.parse_args()

    source_hash = sha256_file(args.input)
    document = load_docx(args.input)
    groups = scan_groups(document)
    pmids = unique_pmids(groups)
    if not pmids:
        raise RuntimeError("No explicit labeled PMID markers were found")

    records = load_metadata_fixture(args.metadata_json) if args.metadata_json else fetch_pubmed(pmids, args.email, args.api_key)
    missing = [pmid for pmid in pmids if pmid not in records]
    if missing:
        raise RuntimeError("Metadata is missing for: " + ", ".join(missing))

    outdir = args.outdir or args.input.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    enw_path = collision_safe_path(outdir / f"{stem}.endnote-import.enw")
    state_path = collision_safe_path(outdir / f"{stem}.endnote-state.json")
    enw_path.write_text(enw_text([records[pmid] for pmid in pmids]), encoding="utf-8")

    portable_records = {
        pmid: {
            "pmid": pmid,
            "first_author": records[pmid].get("first_author", ""),
            "year": str(records[pmid].get("year", "")),
            "doi": str(records[pmid].get("doi", "")),
            "title": str(records[pmid].get("title", "")),
        }
        for pmid in pmids
    }
    state = {
        "schema_version": 1,
        "input_sha256": source_hash,
        "citation_groups": groups,
        "unique_pmids": pmids,
        "records": portable_records,
        "temporary_citation": {"open": "{", "close": "}", "record_marker": "#"},
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sha256_file(args.input) != source_hash:
        raise RuntimeError("The source DOCX changed during preparation")
    if args.open_folder:
        reveal_file(enw_path)

    print(json.dumps({
        "ok": True,
        "action": "prepare",
        "enw": str(enw_path),
        "state": str(state_path),
        "unique_pmids": len(pmids),
        "citation_groups": len(groups),
        "source_unchanged": True,
        "next": "Import the ENW into the intended EndNote library, then run finalize.py.",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
