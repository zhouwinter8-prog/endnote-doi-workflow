# User workflow

## Compatibility

This release is tested with EndNote 20 for Windows and Microsoft Word desktop with Cite While You Write. Other EndNote versions, EndNote for macOS, and EndNote Web have not been tested and are not currently supported. EndNote library formats may change between versions.

## One-time EndNote convention

The supported EndNote 20 configuration uses:

- temporary citation delimiters: ASCII `{` and `}`;
- Record Number marker: `#`;
- `Use field instead of record number`: off.

Use English half-width braces, not full-width braces or square brackets.

The Release package's `bin\EndNote_Workflow.exe` contains its own runtime. Release users do not install Python or run `pip`. A source checkout can use `py -3 scripts\workflow_cli.py` after installing the repository requirements.

## Normal run

1. Put one `.docx` containing explicit markers such as `(PMID: 12345678)` in `Tasks\My_Manuscript`.
2. Ask Codex: `Use $endnote-workflow to process the Word document in Tasks\My_Manuscript.`
3. Codex creates the `.enw` file beside the Word document and opens the project folder.
4. Import the generated `.enw` into the intended EndNote library:
   - `File > Import > File`
   - `Import Option: EndNote Import`
   - `Text Translation: Unicode (UTF-8)`
5. Tell Codex: `Imported. Continue.` Codex reads the most recently used valid EndNote library without modifying it. If the library cannot be determined uniquely, Codex asks for the `.enl` path.
6. Codex creates and opens the final temporary-citation DOCX in the same project folder.
7. Keep the same EndNote library open and click `EndNote 20 > Update Citations and Bibliography` once.

Existing outputs are not overwritten; timestamped names are used when needed.

## If Word does not react

The supported EndNote configuration and this workflow both use `{Author, Year #Number}`. The user does not need to change EndNote Preferences. If Word does not update a temporary citation, verify that the displayed Record Number exists in the open library.

If `Insert Citation > Find Citation` also does nothing in a blank Word document, repair the Cite While You Write add-in. Regenerating the DOCX will not repair a disconnected add-in.
