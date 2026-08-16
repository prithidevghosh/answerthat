"""Making the exported `.tex` compilable under pdfLaTeX, without touching the text.

A paper parsed out of a PDF carries the characters the PDF used: `θ`, `∼`, `∈`, `∇`.
Pandoc writes those through to the `.tex` verbatim, which is correct — the exported text
must match the source. But under pdfTeX the LaTeX kernel's UTF-8 support only knows the
Latin ranges and a little punctuation. Every other codepoint stops the compile dead with

    ! LaTeX Error: Unicode character θ (U+03B8) not set up for use with LaTeX.

which is what every online LaTeX service reported. XeTeX and LuaTeX are fine — the
template loads `unicode-math` for them — but a `.tex` that only builds on two of the
three engines is a `.tex` most people cannot build.

So the text stays exactly as parsed and we teach pdfTeX to set it, by emitting
`\\DeclareUnicodeCharacter` for the characters the document actually uses. The
declarations go in `header-includes`, which pandoc's template places *after* the encoding
setup, and are wrapped in `\\ifPDFTeX` because the command does not exist under the
Unicode engines.

A character we have no macro for gets a visible `[U+XXXX NOT REPRODUCED]` marker rather
than either a silent drop or a hard export failure — the same bargain ADR-008 already
strikes for figures and tables. It is rare by construction: the table below covers Greek,
the math operators and relations, arrows, and the sub/superscript digits, which is what
scientific text actually contains.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["UNICODE_MACROS", "undeclarable", "pdftex_unicode_preamble"]


def _math(macro: str) -> str:
    return f"\\ensuremath{{{macro}}}"


# Greek. The uppercase letters LaTeX has no macro for are the ones that are simply Latin
# letters in disguise (Alpha is A), so they map to the Latin letter rather than a marker.
_GREEK: dict[str, str] = {
    "α": _math(r"\alpha"), "β": _math(r"\beta"), "γ": _math(r"\gamma"),
    "δ": _math(r"\delta"), "ε": _math(r"\varepsilon"), "ϵ": _math(r"\epsilon"),
    "ζ": _math(r"\zeta"), "η": _math(r"\eta"), "θ": _math(r"\theta"),
    "ϑ": _math(r"\vartheta"), "ι": _math(r"\iota"), "κ": _math(r"\kappa"),
    "λ": _math(r"\lambda"), "μ": _math(r"\mu"), "µ": _math(r"\mu"),
    "ν": _math(r"\nu"), "ξ": _math(r"\xi"), "π": _math(r"\pi"),
    "ϖ": _math(r"\varpi"), "ρ": _math(r"\rho"), "ϱ": _math(r"\varrho"),
    "σ": _math(r"\sigma"), "ς": _math(r"\varsigma"), "τ": _math(r"\tau"),
    "υ": _math(r"\upsilon"), "φ": _math(r"\varphi"), "ϕ": _math(r"\phi"),
    "χ": _math(r"\chi"), "ψ": _math(r"\psi"), "ω": _math(r"\omega"),
    "Γ": _math(r"\Gamma"), "Δ": _math(r"\Delta"), "Θ": _math(r"\Theta"),
    "Λ": _math(r"\Lambda"), "Ξ": _math(r"\Xi"), "Π": _math(r"\Pi"),
    "Σ": _math(r"\Sigma"), "Υ": _math(r"\Upsilon"), "Φ": _math(r"\Phi"),
    "Ψ": _math(r"\Psi"), "Ω": _math(r"\Omega"),
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
}

_RELATIONS: dict[str, str] = {
    "∼": _math(r"\sim"), "≈": _math(r"\approx"), "≃": _math(r"\simeq"),
    "≅": _math(r"\cong"), "≠": _math(r"\neq"), "≡": _math(r"\equiv"),
    "≤": _math(r"\leq"), "≥": _math(r"\geq"), "⩽": _math(r"\leqslant"),
    "⩾": _math(r"\geqslant"), "≪": _math(r"\ll"), "≫": _math(r"\gg"),
    "∝": _math(r"\propto"), "≺": _math(r"\prec"), "≻": _math(r"\succ"),
    "∈": _math(r"\in"), "∉": _math(r"\notin"), "∋": _math(r"\ni"),
    "⊂": _math(r"\subset"), "⊆": _math(r"\subseteq"), "⊃": _math(r"\supset"),
    "⊇": _math(r"\supseteq"), "⊥": _math(r"\perp"), "∥": _math(r"\parallel"),
    "≜": _math(r"\triangleq"), "≐": _math(r"\doteq"),
}

_OPERATORS: dict[str, str] = {
    "∪": _math(r"\cup"), "∩": _math(r"\cap"), "∅": _math(r"\emptyset"),
    "∀": _math(r"\forall"), "∃": _math(r"\exists"), "∄": _math(r"\nexists"),
    "¬": _math(r"\neg"), "∧": _math(r"\wedge"), "∨": _math(r"\vee"),
    "⊕": _math(r"\oplus"), "⊗": _math(r"\otimes"), "⊙": _math(r"\odot"),
    "∇": _math(r"\nabla"), "∂": _math(r"\partial"), "∞": _math(r"\infty"),
    "∫": _math(r"\int"), "∬": _math(r"\iint"), "∮": _math(r"\oint"),
    "∑": _math(r"\sum"), "∏": _math(r"\prod"), "√": _math(r"\surd"),
    "∓": _math(r"\mp"), "⋅": _math(r"\cdot"), "∘": _math(r"\circ"),
    "∗": _math(r"\ast"), "⊤": _math(r"\top"), "⊢": _math(r"\vdash"),
    "∠": _math(r"\angle"), "∴": _math(r"\therefore"), "∵": _math(r"\because"),
    "⌈": _math(r"\lceil"), "⌉": _math(r"\rceil"), "⌊": _math(r"\lfloor"),
    "⌋": _math(r"\rfloor"), "⟨": _math(r"\langle"), "⟩": _math(r"\rangle"),
    "′": _math(r"\prime"), "″": _math(r"\prime\prime"), "‖": _math(r"\|"),
    "…": r"\ldots", "⋯": _math(r"\cdots"), "⋮": _math(r"\vdots"), "⋱": _math(r"\ddots"),
}

_ARROWS: dict[str, str] = {
    "→": _math(r"\rightarrow"), "←": _math(r"\leftarrow"),
    "↔": _math(r"\leftrightarrow"), "⇒": _math(r"\Rightarrow"),
    "⇐": _math(r"\Leftarrow"), "⇔": _math(r"\Leftrightarrow"),
    "↦": _math(r"\mapsto"), "↑": _math(r"\uparrow"), "↓": _math(r"\downarrow"),
    "⟶": _math(r"\longrightarrow"), "⟵": _math(r"\longleftarrow"),
    "⟹": _math(r"\Longrightarrow"), "↗": _math(r"\nearrow"), "↘": _math(r"\searrow"),
}

_BLACKBOARD: dict[str, str] = {
    "ℝ": _math(r"\mathbb{R}"), "ℕ": _math(r"\mathbb{N}"), "ℤ": _math(r"\mathbb{Z}"),
    "ℚ": _math(r"\mathbb{Q}"), "ℂ": _math(r"\mathbb{C}"), "𝔼": _math(r"\mathbb{E}"),
    "𝟙": _math(r"\mathbb{1}"), "ℓ": _math(r"\ell"), "ℏ": _math(r"\hbar"),
}

# Sub/superscript digits and signs. `¹²³` live in Latin-1 and inputenc already knows them.
_SCRIPTS: dict[str, str] = {
    **{c: _math(f"^{{{d}}}") for c, d in zip("⁰⁴⁵⁶⁷⁸⁹", "0456789", strict=True)},
    **{c: _math(f"_{{{d}}}") for c, d in zip("₀₁₂₃₄₅₆₇₈₉", "0123456789", strict=True)},
    "⁺": _math("^{+}"), "⁻": _math("^{-}"), "⁽": _math("^{(}"), "⁾": _math("^{)}"),
    "₊": _math("_{+}"), "₋": _math("_{-}"), "₍": _math("_{(}"), "₎": _math("_{)}"),
}

UNICODE_MACROS: dict[str, str] = {
    **_GREEK, **_RELATIONS, **_OPERATORS, **_ARROWS, **_BLACKBOARD, **_SCRIPTS,
}

# What `inputenc`'s utf8 encoding plus `textcomp` already handle, so we neither need nor
# want to redeclare it: the Latin ranges, and the General Punctuation that utf8.def maps
# (dashes, curly quotes, daggers, bullet, ellipsis, per-mille, guillemets).
_SAFE_PUNCTUATION = frozenset(
    "‐‑‒–—―"
    "‘’‚‛“”„"
    "†‡•…‰‹›⁄"
    "€™"
)


def _is_safe(char: str) -> bool:
    code = ord(char)
    if code < 0x80:  # noqa: PLR2004 — ASCII needs no declaration
        return True
    if 0xA0 <= code <= 0x24F:  # noqa: PLR2004 — Latin-1 Supplement + Latin Extended-A/B
        return True
    return char in _SAFE_PUNCTUATION


def undeclarable(text: str) -> list[str]:
    """The characters in `text` that pdfTeX cannot set without help, in first-use order.

    Deduplicated but order-preserving, so the preamble reads in the order a reader would
    meet the characters in the paper rather than in codepoint order.
    """
    seen: dict[str, None] = {}
    for char in text:
        if not _is_safe(char):
            seen.setdefault(char, None)
    return list(seen)


def _replacement(char: str) -> str:
    macro = UNICODE_MACROS.get(char)
    if macro is not None:
        return macro
    # No macro: say so in the output rather than dropping the character or refusing the
    # whole export. Same bargain as ADR-008's figure and table placeholders.
    return rf"\textbf{{[U+{ord(char):04X} NOT REPRODUCED]}}"


def pdftex_unicode_preamble(text: str) -> str | None:
    """A `\\ifPDFTeX`-guarded block declaring every character `text` needs, or None.

    Returns None when the text is already within what `inputenc` handles, so a plain
    ASCII paper gets no extra preamble at all.
    """
    chars = undeclarable(text)
    if not chars:
        return None

    lines = [
        r"% Characters this paper uses that inputenc does not know. Declared so the file",
        r"% builds under pdflatex as well as xelatex/lualatex, without altering the text.",
        r"\ifPDFTeX",
    ]
    for char in chars:
        name = unicodedata.name(char, "UNNAMED")
        lines.append(rf"  \DeclareUnicodeCharacter{{{ord(char):04X}}}{{{_replacement(char)}}} % {name}")
    lines.append(r"\fi")
    return "\n".join(lines)


def scan_json_strings(payload: str) -> str:
    """Every character in a JSON blob, for pre-render scanning of an AST or bibliography.

    Scanning the serialised form rather than walking it is deliberate: it cannot miss a
    field, and the JSON punctuation it also sees is ASCII, which `undeclarable` ignores.
    Escaped sequences are the one thing it would miss, so they are decoded first.
    """
    return re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        payload,
    )
