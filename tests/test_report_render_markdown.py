

def test_scorecard_cell_escapes_pipes_and_newlines():
    # An LLM-written summary containing "|" or a newline used to split the row
    # across lines with an extra column.
    from src.report.render.markdown import render_markdown
    from src.report.schema import Pillar, Report, ReportMeta

    report = Report(
        meta=ReportMeta(kind="website_audit", subject="https://example.com"),
        pillars=[Pillar(name="SEO", score=6, summary="Good | but\nsecond line")],
    ).finalize()
    md = render_markdown(report)
    row = next(line for line in md.splitlines() if line.startswith("| SEO"))
    assert row.count("|") - row.count("\\|") == 4  # 3 cells -> 4 unescaped delimiters
    assert "Good \\| but second line" in row
