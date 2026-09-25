from src.i18n import kind_title, t_chrome
from src.report.schema import Report, Status


def _score_str(score, lang: str) -> str:
    return f"{score}/10" if score is not None else t_chrome("report.na_value", lang)


def render_markdown(report: Report) -> str:
    lang = report.meta.lang
    status_label = {Status.OK: t_chrome("status.ok", lang), Status.WARN: t_chrome("status.warn", lang), Status.BAD: t_chrome("status.bad", lang), Status.NA: t_chrome("status.na", lang)}

    lines: list[str] = [f"# {kind_title(report.meta.kind, lang)}", ""]
    lines += [f"**{t_chrome('report.subject', lang)}:** {report.meta.subject}  ", f"**{t_chrome('report.generated', lang)}:** {report.meta.generated_at.isoformat()}", ""]

    counts = report.status_counts()
    lines.append(f"## {t_chrome('report.executive_summary', lang)}")
    lines.append("")
    grade = report.grade if report.grade != "N/A" else t_chrome("report.na_value", lang)
    lines.append(f"**{t_chrome('report.overall', lang, score=_score_str(report.overall_score, lang), grade=grade)}** — {report.verdict}")
    lines.append("")
    lines.append(t_chrome("report.counts", lang, bad=counts["bad"], warn=counts["warn"], ok=counts["ok"], na=counts["na"]))
    top = sorted(report.recommendations, key=lambda r: r.priority)[:5]
    if top:
        lines.append("")
        lines.append(f"{t_chrome('report.top_priorities', lang)}:")
        for rec in top:
            lines.append(f"{rec.priority}. {rec.action}")
    lines.append("")

    if report.pillars:
        lines += [f"## {t_chrome('report.scorecard', lang)}", "", f"| {t_chrome('report.col_pillar', lang)} | {t_chrome('report.col_score', lang)} | {t_chrome('report.col_summary', lang)} |", "|---|---|---|"]
        for pillar in report.pillars:
            lines.append(f"| {pillar.name} | {_score_str(pillar.score, lang)} | {pillar.summary} |")
        lines.append("")

        for pillar in report.pillars:
            lines += [f"## {pillar.name}", ""]
            for f in pillar.findings:
                lines.append(f"- **[{status_label[f.status]}] {f.check}** — {f.detail}")
                if f.evidence:
                    lines.append(f"  - {t_chrome('report.evidence', lang)}: {f.evidence}")
                if f.fix and f.status in (Status.WARN, Status.BAD):
                    lines.append(f"  - {t_chrome('report.fix', lang)}: {f.fix}")
            lines.append("")

    if report.recommendations:
        lines += [f"## {t_chrome('report.recommendations', lang)}", ""]
        for rec in sorted(report.recommendations, key=lambda r: r.priority):
            lines.append(f"{rec.priority}. **{rec.action}** — {rec.rationale}")
        lines.append("")

    if report.methodology:
        lines += [f"## {t_chrome('report.methodology', lang)}", "", report.methodology, ""]
    if report.limitations:
        lines += [f"## {t_chrome('report.limitations', lang)}", "", report.limitations, ""]

    return "\n".join(lines).rstrip() + "\n"
