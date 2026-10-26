from __future__ import annotations

import json
import multiprocessing
import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print(
            "Usage:\n"
            "  EndNote_Workflow.exe prepare INPUT.docx [options]\n"
            "  EndNote_Workflow.exe prepare-doi --doi-file DOIS.txt [options]\n"
            "  EndNote_Workflow.exe finalize INPUT.docx --state STATE.json [options]"
        )
        return 0 if len(sys.argv) >= 2 else 2

    command = sys.argv.pop(1).casefold()
    if command == "prepare":
        import prepare

        return prepare.main()
    if command in {"prepare-doi", "preparedoi", "prepare_doi"}:
        import prepare_doi

        return prepare_doi.main()
    if command == "finalize":
        import finalize

        return finalize.main()

    print(json.dumps({
        "ok": False,
        "error": f"Unknown command: {command}. Use prepare, prepare-doi, or finalize.",
    }, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
