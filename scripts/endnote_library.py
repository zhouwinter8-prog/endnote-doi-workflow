from __future__ import annotations

import shutil
import sqlite3
import tempfile
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    import winreg
except ImportError:
    winreg = None


COLLATIONS = ("ENCI_Base", "ENCIN_zh_TW")
FIELDS = (
    "id", "author", "year", "title", "label", "accession_number",
    "electronic_resource_number", "trash_state",
)
RECENT_LIBRARY_KEYS = (
    r"Software\ISI ResearchSoft\EndNote\Recent Libraries",
    r"Software\ResearchSoft\EndNote\Recent Libraries",
)


def database_for_library(library: Path) -> Path:
    if library.suffix.lower() != ".enl" or not library.is_file():
        raise ValueError(f"EndNote library must be an existing .enl file: {library}")
    database = library.with_suffix(".Data") / "sdb" / "sdb.eni"
    if not database.is_file():
        raise FileNotFoundError(f"EndNote data file not found beside {library.name}")
    return database


def _registry_text(value: Any) -> str:
    if isinstance(value, bytes):
        for encoding in ("utf-16-le", "utf-8"):
            try:
                return value.decode(encoding).strip("\x00 \t\r\n\"")
            except UnicodeDecodeError:
                continue
        return ""
    return str(value or "").strip("\x00 \t\r\n\"")


def recent_library() -> Path | None:
    if os.environ.get("ENDNOTE_WORKFLOW_DISABLE_REGISTRY") == "1":
        return None
    if winreg is None:
        return None
    for key_name in RECENT_LIBRARY_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                value, _ = winreg.QueryValueEx(key, "File1")
        except OSError:
            continue
        text = _registry_text(value)
        if text:
            candidate = Path(text)
            if candidate.suffix.casefold() == ".enl" and candidate.is_file():
                return candidate
    return None


def discover_library(project_directory: Path) -> tuple[Path, str]:
    recent = recent_library()
    if recent is not None:
        return recent, "endnote_recent_library"

    directories = [project_directory]
    if project_directory.parent != project_directory:
        directories.append(project_directory.parent)
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.glob("*.enl")):
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0], "single_local_library"
    if not candidates:
        raise RuntimeError("No recent or local EndNote .enl library was found; pass --library")
    names = ", ".join(candidate.name for candidate in candidates)
    raise RuntimeError(f"Multiple local EndNote libraries were found ({names}); pass --library")


def neutral_collation(left: str, right: str) -> int:
    return (left > right) - (left < right)


@contextmanager
def readonly_copy(database: Path) -> Iterator[sqlite3.Connection]:
    temp_root = Path(tempfile.mkdtemp(prefix="endnote-workflow-"))
    connection: sqlite3.Connection | None = None
    try:
        copied = temp_root / database.name
        shutil.copy2(database, copied)
        for sibling in database.parent.glob(database.name + "-*"):
            if sibling.is_file():
                shutil.copy2(sibling, temp_root / sibling.name)
        connection = sqlite3.connect(f"{copied.as_uri()}?mode=ro", uri=True)
        for name in COLLATIONS:
            connection.create_collation(name, neutral_collation)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        shutil.rmtree(temp_root)


def normalize_pmid(value: Any) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith("pmid-"):
        text = text[5:]
    return text


def first_author_surname(author_block: Any) -> str:
    first = str(author_block or "").splitlines()[0].strip()
    return first.split(",", 1)[0].strip()


def identity(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(record.get("first_author") or "").strip().casefold(),
        str(record.get("year") or "").strip(),
        " ".join(str(record.get("title") or "").split()).casefold(),
        str(record.get("doi") or "").strip().casefold(),
    )


def find_by_pmids(library: Path, pmids: list[str]) -> dict[str, dict[str, Any]]:
    database = database_for_library(library)
    wanted = set(pmids)
    matches: dict[str, list[dict[str, Any]]] = {pmid: [] for pmid in pmids}
    columns = ", ".join(FIELDS)
    with readonly_copy(database) as connection:
        rows = connection.execute(
            f"SELECT {columns} FROM refs WHERE trash_state = 0 ORDER BY id"
        ).fetchall()
    for row in rows:
        record = dict(zip(FIELDS, row))
        candidates = {
            normalize_pmid(record.get("accession_number")),
            normalize_pmid(record.get("label")),
        }
        for pmid in wanted.intersection(candidates):
            matches[pmid].append({
                "rec_number": int(record["id"]),
                "first_author": first_author_surname(record.get("author")),
                "year": str(record.get("year") or "").strip(),
                "title": str(record.get("title") or "").strip(),
                "doi": str(record.get("electronic_resource_number") or "").strip(),
            })

    resolved: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for pmid in pmids:
        candidates = matches[pmid]
        if not candidates:
            problems.append(f"PMID {pmid} is not present in the selected EndNote library")
        elif len(candidates) > 1:
            identities = {identity(item) for item in candidates}
            if len(identities) == 1:
                resolved[pmid] = max(candidates, key=lambda item: item["rec_number"])
            else:
                numbers = ", ".join(str(item["rec_number"]) for item in candidates)
                problems.append(f"PMID {pmid} has conflicting duplicate records: {numbers}")
        else:
            resolved[pmid] = candidates[0]
    if problems:
        raise RuntimeError("; ".join(problems))
    return resolved
