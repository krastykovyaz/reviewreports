"""Escaping for user-derived text embedded in a LaTeX document."""

_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    if not text:
        return ""
    # Backslash must be replaced first so later replacements don't double-escape it.
    out = text.replace("\\", "\x00BACKSLASH\x00")
    for char, replacement in _SPECIAL_CHARS.items():
        if char == "\\":
            continue
        out = out.replace(char, replacement)
    out = out.replace("\x00BACKSLASH\x00", r"\textbackslash{}")
    return out
