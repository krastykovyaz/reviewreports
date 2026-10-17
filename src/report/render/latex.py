from src.i18n import kind_title, t_chrome
from src.report.render._latex_escape import escape_latex
from src.report.schema import Report, Status

_STATUS_CMD = {Status.OK: r"\okmark", Status.WARN: r"\warnmark", Status.BAD: r"\badmark", Status.NA: r"\napmark"}

_PREAMBLE = r"""\documentclass[11pt]{article}

\usepackage[margin=1in]{geometry}
\usepackage[utf8]{inputenc}
\usepackage[T1,T2A]{fontenc}
\usepackage[english,russian,french]{babel}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{enumitem}
\usepackage{hyperref}
\usepackage{fancyhdr}

\definecolor{ok}{HTML}{1a7f37}
\definecolor{warn}{HTML}{9a6700}
\definecolor{bad}{HTML}{cf222e}
\definecolor{headergray}{HTML}{57606a}

\hypersetup{colorlinks=true, urlcolor=blue, linkcolor=black}

\pagestyle{fancy}
\fancyhf{}
\lhead{\small %(kind_title)s}
\rhead{\small %(subject)s}
\cfoot{\small\thepage}

\newcommand{\okmark}{\textcolor{ok}{\textbf{%(status_ok)s}}}
\newcommand{\warnmark}{\textcolor{warn}{\textbf{%(status_warn)s}}}
\newcommand{\badmark}{\textcolor{bad}{\textbf{%(status_bad)s}}}
\newcommand{\napmark}{\textcolor{headergray}{\textbf{%(status_na)s}}}

\begin{document}
%(babel_selector)s

\begin{center}
{\LARGE\textbf{%(kind_title)s}}\\[4pt]
{\large %(subject)s}\\[4pt]
{\small %(generated_at)s}
\end{center}
\thispagestyle{fancy}
"""

_BABEL_LANG = {"en": "english", "ru": "russian", "fr": "french"}


def _score_str(score, lang: str) -> str:
    return f"{score}/10" if score is not None else t_chrome("report.na_value", lang)


def render_latex(report: Report, include_footer: bool = True) -> str:
    # include_footer accepted for interface parity with the other renderers;
    # this format never renders a footer of its own — see render_html.
    lang = report.meta.lang
    preamble = _PREAMBLE % {
        "kind_title": escape_latex(kind_title(report.meta.kind, lang)),
        "subject": escape_latex(report.meta.subject),
        "generated_at": escape_latex(report.meta.generated_at.isoformat()),
        "status_ok": escape_latex(t_chrome("status.ok", lang)),
        "status_warn": escape_latex(t_chrome("status.warn", lang)),
        "status_bad": escape_latex(t_chrome("status.bad", lang)),
        "status_na": escape_latex(t_chrome("status.na", lang)),
        "babel_selector": rf"\selectlanguage{{{_BABEL_LANG.get(lang, 'english')}}}",
    }
    parts = [preamble]

    counts = report.status_counts()
    grade = report.grade if report.grade != "N/A" else t_chrome("report.na_value", lang)
    parts.append(rf"\section*{{{escape_latex(t_chrome('report.executive_summary', lang))}}}")
    parts.append(
        r"\noindent\textbf{%s}\\[4pt]%s"
        % (
            escape_latex(t_chrome("report.overall", lang, score=_score_str(report.overall_score, lang), grade=grade)) + r" --- " + escape_latex(report.verdict),
            escape_latex(t_chrome("report.counts", lang, bad=counts["bad"], warn=counts["warn"], ok=counts["ok"], na=counts["na"])),
        )
    )
    top = sorted(report.recommendations, key=lambda r: r.priority)[:5]
    if top:
        parts.append(rf"\textbf{{{escape_latex(t_chrome('report.top_priorities', lang))}:}}")
        parts.append(r"\begin{enumerate}[leftmargin=1.4cm, itemsep=2pt]")
        parts += [rf"    \item {escape_latex(rec.action)}" for rec in top]
        parts.append(r"\end{enumerate}")

    if report.pillars:
        parts += [
            rf"\section*{{{escape_latex(t_chrome('report.scorecard', lang))}}}",
            r"\begin{longtable}{@{}p{4.5cm} p{2cm} p{8cm}@{}}",
            r"\toprule",
            rf"\textbf{{{escape_latex(t_chrome('report.col_pillar', lang))}}} & \textbf{{{escape_latex(t_chrome('report.col_score', lang))}}} & \textbf{{{escape_latex(t_chrome('report.col_summary', lang))}}} \\",
            r"\midrule",
            r"\endhead",
        ]
        for pillar in report.pillars:
            parts.append(f"{escape_latex(pillar.name)} & {_score_str(pillar.score, lang)} & {escape_latex(pillar.summary)} \\\\")
        parts += [r"\bottomrule", r"\end{longtable}"]

        for pillar in report.pillars:
            parts.append(rf"\section*{{{escape_latex(pillar.name)} \small\textcolor{{headergray}}{{{_score_str(pillar.score, lang)}}}}}")
            if pillar.findings:
                parts.append(r"\begin{itemize}[leftmargin=1.2cm, itemsep=3pt]")
                for f in pillar.findings:
                    item = rf"    \item {_STATUS_CMD[f.status]} \textbf{{{escape_latex(f.check)}}} --- {escape_latex(f.detail)}"
                    if f.evidence:
                        item += rf"\\ {{\small\textcolor{{headergray}}{{{escape_latex(t_chrome('report.evidence', lang))}: {escape_latex(f.evidence)}}}}}"
                    if f.fix and f.status in (Status.WARN, Status.BAD):
                        item += rf"\\ {{\small\textbf{{{escape_latex(t_chrome('report.fix', lang))}:}} {escape_latex(f.fix)}}}"
                    parts.append(item)
                parts.append(r"\end{itemize}")

    if report.recommendations:
        parts += [rf"\section*{{{escape_latex(t_chrome('report.recommendations', lang))}}}", r"\begin{enumerate}[leftmargin=1.4cm, itemsep=4pt]"]
        parts += [rf"    \item \textbf{{{escape_latex(rec.action)}}} --- {escape_latex(rec.rationale)}" for rec in sorted(report.recommendations, key=lambda r: r.priority)]
        parts.append(r"\end{enumerate}")

    if report.methodology:
        parts += [rf"\section*{{{escape_latex(t_chrome('report.methodology', lang))}}}", rf"\small\textcolor{{headergray}}{{{escape_latex(report.methodology)}}}"]
    if report.limitations:
        parts += [rf"\section*{{{escape_latex(t_chrome('report.limitations', lang))}}}", rf"\small\textcolor{{headergray}}{{{escape_latex(report.limitations)}}}"]

    parts.append(r"\end{document}")
    return "\n\n".join(parts) + "\n"
