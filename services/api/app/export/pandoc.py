"""Thin, honest wrapper around the `pandoc` binary.

Everything citation-shaped in this system goes through here (HR-4). There is no other
sanctioned way to turn a CSL-JSON record into a rendered string — not an f-string, not a
template, not a regex.

The wrapper's one real job beyond running a subprocess is refusing to hide a failure.
Pandoc's stderr is the only thing that explains why a render broke, so it is carried
into `ExportFailure` verbatim rather than summarised.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import ExportFailure

__all__ = ["PandocResult", "pandoc_available", "run_pandoc", "ast_to_latex", "render_bibliography_entries"]

# Pandoc refuses AST JSON whose api version it does not recognise. Confirmed against
# pandoc 3.10.1; see memory.md §4 if a future pandoc rejects it.
PANDOC_API_VERSION = [1, 23, 1, 2]

DEFAULT_TIMEOUT_S = 120


@dataclass(frozen=True)
class PandocResult:
    stdout: str
    stderr: str
    argv: list[str]


def pandoc_available() -> bool:
    """Whether the binary exists. For skipping tests — never for degrading behaviour."""
    return shutil.which(get_settings().pandoc_bin if _settings_loadable() else "pandoc") is not None


def _settings_loadable() -> bool:
    try:
        get_settings()
    except Exception:
        return False
    return True


def _pandoc_bin() -> str:
    return get_settings().pandoc_bin if _settings_loadable() else "pandoc"


def run_pandoc(args: list[str], *, stdin: str | None = None, timeout: int = DEFAULT_TIMEOUT_S) -> PandocResult:
    """Run pandoc, raising `ExportFailure` on a non-zero exit or a timeout."""
    argv = [_pandoc_bin(), *args]
    try:
        proc = subprocess.run(  # noqa: S603 - argv is constructed here, never shell-interpolated
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ExportFailure(
            f"pandoc not found on PATH as {_pandoc_bin()!r}. Every citation in this system is "
            "rendered by pandoc (HR-4), so there is no fallback path — install pandoc or set "
            "PANDOC_BIN."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ExportFailure(f"pandoc timed out after {timeout}s: {' '.join(argv)}") from exc

    if proc.returncode != 0:
        raise ExportFailure(
            f"pandoc exited {proc.returncode}.\n"
            f"  command: {' '.join(argv)}\n"
            f"  stderr:  {proc.stderr.strip() or '(empty)'}"
        )
    return PandocResult(stdout=proc.stdout, stderr=proc.stderr, argv=argv)


def ast_to_latex(
    ast: dict,
    *,
    csl_path: Path,
    bibliography: list[dict],
    standalone: bool = True,
    extra_args: list[str] | None = None,
) -> PandocResult:
    """Render a Pandoc AST to LaTeX with citeproc.

    `csl_path` is a real `.csl` file — pandoc wants a path, not a style name, and the
    same file is read by the frontend's citation.js so preview and export cannot drift
    (HR-4). `bibliography` is CSL-JSON; every citation key used in the AST must appear
    in it, or pandoc will emit an unresolved-reference warning that we escalate.
    """
    if not csl_path.is_file():
        raise ExportFailure(
            f"CSL style file not found: {csl_path}. Citations cannot be rendered without one "
            "(HR-4); check that packages/csl-styles/ is mounted into the container."
        )

    with tempfile.TemporaryDirectory(prefix="answerthat-export-") as tmp:
        bib_path = Path(tmp) / "bibliography.json"
        bib_path.write_text(json.dumps(bibliography, ensure_ascii=False), encoding="utf-8")

        args = [
            "-f", "json",
            "-t", "latex",
            "--citeproc",
            f"--csl={csl_path}",
            f"--bibliography={bib_path}",
        ]
        if standalone:
            args.append("--standalone")
        args.extend(extra_args or [])

        result = run_pandoc(args, stdin=json.dumps(ast, ensure_ascii=False))

    # citeproc reports an unresolved key on stderr and then renders "???" into the
    # document. Silently shipping a .tex containing "???" where a citation belongs is
    # exactly the kind of quiet degradation HR-3 forbids.
    if "Citeproc" in result.stderr and "not found" in result.stderr:
        raise ExportFailure(
            "citeproc could not resolve every citation key against the supplied "
            f"bibliography, so the output would contain unrendered citations.\n  stderr: {result.stderr.strip()}"
        )
    return result


def render_bibliography_entries(
    entries: list[dict],
    *,
    csl_path: Path,
    output_format: str = "plain",
) -> list[str]:
    """Render each CSL-JSON entry on its own, returning one formatted string per entry.

    Used by style detection (ADR-011), which compares a rendered reference against the
    raw string we extracted from the PDF. Entries are rendered one at a time because we
    need them individually addressable, and because a style that numbers its entries
    would otherwise number them by position in a batch.

    Returns strings in the same order as `entries`. An entry that renders empty is
    returned as an empty string rather than skipped — dropping it would silently
    misalign the caller's zip().
    """
    rendered: list[str] = []
    for entry in entries:
        key = str(entry.get("id") or "")
        if not key:
            raise ExportFailure("CSL-JSON entry has no 'id'; it cannot be cited or rendered")
        ast = {
            "pandoc-api-version": PANDOC_API_VERSION,
            "meta": {"nocite": {"t": "MetaBlocks", "c": [_nocite_block(key)]}},
            "blocks": [],
        }
        with tempfile.TemporaryDirectory(prefix="answerthat-cite-") as tmp:
            bib_path = Path(tmp) / "bibliography.json"
            bib_path.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
            result = run_pandoc(
                [
                    "-f", "json",
                    "-t", output_format,
                    "--citeproc",
                    f"--csl={csl_path}",
                    f"--bibliography={bib_path}",
                ],
                stdin=json.dumps(ast, ensure_ascii=False),
            )
        rendered.append(result.stdout.strip())
    return rendered


def _nocite_block(key: str) -> dict:
    """A `nocite` entry forces an uncited work into the bibliography."""
    return {
        "t": "Para",
        "c": [
            {
                "t": "Cite",
                "c": [
                    [
                        {
                            "citationId": key,
                            "citationPrefix": [],
                            "citationSuffix": [],
                            "citationMode": {"t": "NormalCitation"},
                            "citationNoteNum": 0,
                            "citationHash": 0,
                        }
                    ],
                    [{"t": "Str", "c": f"[@{key}]"}],
                ],
            }
        ],
    }
