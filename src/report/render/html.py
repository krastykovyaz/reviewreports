from jinja2 import Environment, select_autoescape

from src.i18n import kind_title, t_chrome
from src.report.schema import Report

_TEMPLATE_SRC = """\
<!DOCTYPE html>
<html lang="{{ report.meta.lang }}">
<head>
<meta charset="utf-8">
<title>{{ title }} — {{ report.meta.subject }}</title>
<style>
  :root { color-scheme: light dark; --ok: #1a7f37; --warn: #9a6700; --bad: #cf222e; --na: #6e7781; --line: rgba(127,127,127,0.25); }
  body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
  h1 { margin-bottom: 0.2rem; }
  .meta { color: var(--na); margin-bottom: 1.5rem; }
  .summary { display: grid; grid-template-columns: auto 1fr; gap: 1.25rem; align-items: center; padding: 1rem 1.25rem; border-radius: 10px; background: rgba(127,127,127,0.08); margin-bottom: 1.5rem; }
  .grade { font-size: 2.6rem; font-weight: 700; line-height: 1; }
  .grade small { display: block; font-size: 0.85rem; font-weight: 500; color: var(--na); }
  .counts span { display: inline-block; margin-right: 1rem; font-weight: 600; }
  .c-bad { color: var(--bad); } .c-warn { color: var(--warn); } .c-ok { color: var(--ok); } .c-na { color: var(--na); }
  ol.top { margin: 0.5rem 0 0 1.2rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
  th, td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { font-weight: 600; }
  .status { font-weight: 600; white-space: nowrap; }
  .status-ok { color: var(--ok); } .status-warn { color: var(--warn); } .status-bad { color: var(--bad); } .status-na { color: var(--na); }
  .sub { display: block; font-size: 0.85rem; color: var(--na); margin-top: 0.2rem; word-break: break-word; }
  .fix { display: block; font-size: 0.85rem; margin-top: 0.2rem; }
  .section-title { margin-top: 2rem; }
  ol.recommendations li { margin-bottom: 0.5rem; }
  .small { font-size: 0.9rem; color: var(--na); }
</style>
</head>
<body>
  <h1>{{ title }}</h1>
  <div class="meta">
    <div><strong>{{ t('report.subject') }}:</strong> {{ report.meta.subject }}</div>
    <div><strong>{{ t('report.generated') }}:</strong> {{ report.meta.generated_at.isoformat() }}</div>
  </div>

  <section class="summary">
    <div class="grade">{{ grade }}<small>{{ score(report.overall_score) }}</small></div>
    <div>
      <div>{{ report.verdict }}</div>
      <div class="counts">
        <span class="c-bad">{{ counts.bad }} {{ t('status.bad') }}</span>
        <span class="c-warn">{{ counts.warn }} {{ t('status.warn') }}</span>
        <span class="c-ok">{{ counts.ok }} {{ t('status.ok') }}</span>
        <span class="c-na">{{ counts.na }} {{ t('status.na') }}</span>
      </div>
      {% if top %}
      <div><strong>{{ t('report.top_priorities') }}</strong>
        <ol class="top">{% for rec in top %}<li>{{ rec.action }}</li>{% endfor %}</ol>
      </div>
      {% endif %}
    </div>
  </section>

  {% if report.pillars %}
  <h2>{{ t('report.scorecard') }}</h2>
  <table>
    <tr><th>{{ t('report.col_pillar') }}</th><th>{{ t('report.col_score') }}</th><th>{{ t('report.col_summary') }}</th></tr>
    {% for pillar in report.pillars %}
    <tr><td>{{ pillar.name }}</td><td>{{ score(pillar.score) }}</td><td>{{ pillar.summary }}</td></tr>
    {% endfor %}
  </table>

  {% for pillar in report.pillars %}
  <h2 class="section-title">{{ pillar.name }} <span class="small">{{ score(pillar.score) }}</span></h2>
  <table>
    <tr><th style="width:9rem">{{ t('report.col_status') }}</th><th style="width:14rem">{{ t('report.col_check') }}</th><th>{{ t('report.col_finding') }}</th></tr>
    {% for f in pillar.findings %}
    <tr>
      <td class="status status-{{ f.status.value }}">{{ status_label(f.status) }}</td>
      <td>{{ f.check }}</td>
      <td>{{ f.detail }}
        {% if f.evidence %}<span class="sub">{{ t('report.evidence') }}: {{ f.evidence }}</span>{% endif %}
        {% if f.fix and f.status.value in ("warn", "bad") %}<span class="fix"><strong>{{ t('report.fix') }}:</strong> {{ f.fix }}</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% endfor %}
  {% endif %}

  {% if report.recommendations %}
  <h2 class="section-title">{{ t('report.recommendations') }}</h2>
  <ol class="recommendations">
    {% for rec in report.recommendations|sort(attribute='priority') %}
    <li><strong>{{ rec.action }}</strong> — {{ rec.rationale }}</li>
    {% endfor %}
  </ol>
  {% endif %}

  {% if report.methodology %}
  <h2 class="section-title">{{ t('report.methodology') }}</h2>
  <p class="small">{{ report.methodology }}</p>
  {% endif %}

  {% if report.limitations %}
  <h2 class="section-title">{{ t('report.limitations') }}</h2>
  <p class="small">{{ report.limitations }}</p>
  {% endif %}
</body>
</html>
"""

_env = Environment(autoescape=select_autoescape(["html"]))
_template = _env.from_string(_TEMPLATE_SRC)


def render_html(report: Report) -> str:
    lang = report.meta.lang
    grade = report.grade if report.grade != "N/A" else t_chrome("report.na_value", lang)
    return _template.render(
        report=report,
        title=kind_title(report.meta.kind, lang),
        grade=grade,
        counts=report.status_counts(),
        top=sorted(report.recommendations, key=lambda r: r.priority)[:5],
        t=lambda key, **kw: t_chrome(key, lang, **kw),
        status_label=lambda s: t_chrome(f"status.{s.value}", lang),
        score=lambda s: f"{s}/10" if s is not None else t_chrome("report.na_value", lang),
    )
