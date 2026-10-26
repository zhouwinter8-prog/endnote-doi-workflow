from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path


TEXT_SUFFIXES = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".cmd", ".bat"}
BINARY_SUFFIXES = {".exe", ".dll", ".pyd"}
BLOCKED_SUFFIXES = {".docx", ".enl", ".enw", ".ris", ".nbib", ".pem", ".key", ".pyc"}
BLOCKED_PARTS = {".data", "__pycache__", ".venv", "venv"}
SKIP_PARTS = {".git"}
ALLOWED_EMAILS = {"noreply@users.noreply.github.com"}
EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9][A-Z0-9._%+-]*@[A-Z0-9.-]+\.[A-Z]{2,24}\b")
WINDOWS_HOME_PATTERN = re.compile(r"[A-Za-z]:" + re.escape("\\") + r"Users" + re.escape("\\") + r"[^\\\s]+", re.I)
POSIX_HOME_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+")


def text_problems(name: str, text: str, forbidden: list[str], *, allow_public_license_emails: bool = False) -> list[str]:
    problems: list[str] = []
    emails = sorted({match.group(0) for match in EMAIL_PATTERN.finditer(text) if match.group(0).casefold() not in ALLOWED_EMAILS})
    if allow_public_license_emails:
        emails = []
    if emails:
        problems.append(f"{name}: email-like text ({', '.join(emails)})")
    if WINDOWS_HOME_PATTERN.search(text) or POSIX_HOME_PATTERN.search(text):
        problems.append(f"{name}: user-home absolute path")
    lowered = text.casefold()
    for value in forbidden:
        if value and value.casefold() in lowered:
            problems.append(f"{name}: forbidden value '{value}'")
    return problems


def path_problems(relative: str) -> list[str]:
    path = Path(relative)
    lowered_parts = {part.casefold() for part in path.parts}
    problems: list[str] = []
    if path.suffix.casefold() in BLOCKED_SUFFIXES:
        problems.append(f"{relative}: private or generated artifact type")
    if lowered_parts.intersection(BLOCKED_PARTS):
        problems.append(f"{relative}: private data or cache directory")
    lowered_name = path.name.casefold()
    if "endnote-state" in lowered_name or "endnote-ready" in lowered_name or lowered_name.startswith("~$"):
        problems.append(f"{relative}: generated workflow artifact")
    return problems


def binary_problems(name: str, payload: bytes, forbidden: list[str]) -> list[str]:
    # PyInstaller archives and PE resources can retain build paths. Scan both
    # single-byte and UTF-16LE views without attempting to execute the binary.
    views = (
        payload.decode("latin-1", errors="ignore"),
        payload.decode("utf-16-le", errors="ignore"),
    )
    problems: list[str] = []
    for index, view in enumerate(views, start=1):
        problems.extend(text_problems(f"{name} [binary view {index}]", view, forbidden))
    return problems


def git_ignored_paths(root: Path) -> set[str]:
    if not (root / ".git").is_dir():
        return set()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "-i", "--exclude-standard", "-z"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not enumerate Git-ignored files")
    return {value.decode("utf-8", errors="replace") for value in result.stdout.split(b"\0") if value}


def audit_directory(root: Path, forbidden: list[str]) -> tuple[list[str], list[str]]:
    files: list[str] = []
    problems: list[str] = []
    ignored = git_ignored_paths(root)
    for path in sorted(item for item in root.rglob("*") if item.is_file() and not {part.casefold() for part in item.relative_to(root).parts}.intersection(SKIP_PARTS)):
        relative = path.relative_to(root).as_posix()
        if relative in ignored:
            continue
        files.append(relative)
        problems.extend(text_problems(relative, relative, forbidden))
        problems.extend(path_problems(relative))
        if path.suffix.casefold() in TEXT_SUFFIXES:
            is_public_license = "/third_party_licenses/" in "/" + relative.casefold()
            problems.extend(text_problems(relative, path.read_text(encoding="utf-8"), forbidden, allow_public_license_emails=is_public_license))
        elif path.suffix.casefold() in BINARY_SUFFIXES:
            problems.extend(binary_problems(relative, path.read_bytes(), forbidden))
    return files, problems


def audit_zip(path: Path, forbidden: list[str]) -> tuple[list[str], list[str]]:
    files: list[str] = []
    problems: list[str] = []
    with zipfile.ZipFile(path) as archive:
        members = (item for item in archive.infolist() if not item.is_dir())
        for member in sorted(members, key=lambda item: item.filename):
            files.append(member.filename)
            problems.extend(text_problems(member.filename, member.filename, forbidden))
            problems.extend(path_problems(member.filename))
            if Path(member.filename).suffix.casefold() in TEXT_SUFFIXES:
                text = archive.read(member).decode("utf-8")
                is_public_license = "/third_party_licenses/" in "/" + member.filename.casefold()
                problems.extend(text_problems(member.filename, text, forbidden, allow_public_license_emails=is_public_license))
            elif Path(member.filename).suffix.casefold() in BINARY_SUFFIXES:
                problems.extend(binary_problems(member.filename, archive.read(member), forbidden))
    return files, problems


def audit_git_history(root: Path, forbidden: list[str]) -> list[str]:
    if not (root / ".git").is_dir():
        return ["git history: target is not a Git repository"]
    command = ["git", "-C", str(root), "log", "--all", "--full-history", "--no-color", "--format=commit %H%n%an%n%ae", "-p"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return [f"git history: {result.stderr.strip() or 'git log failed'}"]
    return text_problems("git history", result.stdout, forbidden)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit an EndNote_Workflow folder or ZIP for personal data.")
    parser.add_argument("target", type=Path)
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--git-history", action="store_true", help="Also scan every commit and patch in the repository")
    args = parser.parse_args()
    if args.target.is_dir():
        files, problems = audit_directory(args.target, args.forbid)
    elif args.target.suffix.casefold() == ".zip" and args.target.is_file():
        files, problems = audit_zip(args.target, args.forbid)
    else:
        raise ValueError("Target must be a directory or ZIP archive")
    if args.git_history:
        if not args.target.is_dir():
            raise ValueError("--git-history requires a repository directory")
        problems.extend(audit_git_history(args.target, args.forbid))
    result = {"ok": not problems, "files_checked": len(files), "files": files, "problems": problems}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not problems else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
