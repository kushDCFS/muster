"""
The deliverable: a printable Coverage Reliability Report.

This is what an agency actually receives and forwards to a county board or
attaches to a SIREN / state EMS grant application. It is written to survive
being read by someone who has never seen the software and is skeptical of it,
so it states its own limits in the body rather than in fine print.
"""

import html
from datetime import date

WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _e(s):
    return html.escape(str(s))


def _hour_label(h):
    return f"{h:02d}:00"


def _pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def _heatmap(by_hour):
    max_rate = max([h["failure_rate"] for h in by_hour] + [0.0001])
    cells = []
    for h in by_hour:
        rate = h["failure_rate"]
        intensity = rate / max_rate if max_rate else 0
        # white -> deep red
        r = 255 - int(60 * intensity)
        g = 255 - int(150 * intensity)
        b = 255 - int(155 * intensity)
        title = (f"{_hour_label(h['hour'])} — {h['failures']} of {h['incidents']} incidents "
                 f"({_pct(rate)})")
        label = f"{rate*100:.0f}" if h["incidents"] else "–"
        cells.append(
            f'<div class="hm-cell" style="background:rgb({r},{g},{b})" title="{_e(title)}">'
            f'<span class="hm-hr">{h["hour"]}</span><span class="hm-v">{label}</span></div>'
        )
    return f'<div class="heatmap">{"".join(cells)}</div>'


def render_report(audit_id, payload):
    f = payload["findings"]
    s, pat, cost = f["summary"], f["pattern"], f["cost"]
    pr = payload["parse_report"]
    agency = payload["agency_name"]
    is_demo = payload["is_demo"]

    ref = payload.get("reference_meta")
    if ref:
        demo_banner = (
            '<div class="ref-banner"><strong>REFERENCE DATASET — REAL DATA.</strong> '
            f'{int(ref["rows"]):,} actual ambulance dispatches from the {_e(ref["agency_name"])}, '
            f'{_e(ref["period"])}, via {_e(ref["source"])}. '
            f'<br><br>{_e(ref["why_this_dataset"])}'
            f'<br><br><em>{_e(ref["limitation"])}</em></div>')
    elif is_demo:
        demo_banner = (
            '<div class="demo-banner">SYNTHETIC DEMONSTRATION DATA — not a real agency.</div>')
    else:
        demo_banner = ""

    # A clean result must read as clean. Alarming language on an agency that
    # has no crew-assembly problem would be both wrong and instantly
    # discrediting to anyone who knows their own operation.
    clean = s["failure_rate"] < 0.01

    ratio = pat["workday_penalty_ratio"]
    ratio_line = (
        f"Weekday daytime calls fail to raise a crew <strong>{ratio}&times; as often</strong> as "
        f"calls at all other times ({_pct(pat['workday_failure_rate'])} vs "
        f"{_pct(pat['offhours_failure_rate'])})."
        if ratio and ratio > 1.05 else
        f"Weekday daytime failure rate is {_pct(pat['workday_failure_rate'])} versus "
        f"{_pct(pat['offhours_failure_rate'])} at other times — <strong>no strong daytime penalty "
        f"is present in this data</strong>, which is itself a useful finding.")

    worst_rows = "".join(
        f"<tr><td>{_e(w['weekday'])} {_hour_label(w['hour'])}</td>"
        f"<td class='num'>{w['incidents']}</td>"
        f"<td class='num'>{w['failures']}</td>"
        f"<td class='num strong'>{_pct(w['failure_rate'], 0)}</td></tr>"
        for w in pat["worst_windows"][:8]) or "<tr><td colspan='4'>Not enough volume in any single hour-of-week cell to rank.</td></tr>"

    wd_rows = "".join(
        f"<tr><td>{_e(w['weekday'])}</td><td class='num'>{w['incidents']}</td>"
        f"<td class='num'>{w['failures']}</td><td class='num'>{_pct(w['failure_rate'], 0)}</td></tr>"
        for w in pat["by_weekday"])

    assembly_line = (
        f"Median time from dispatch to en route was <strong>{s['median_assembly_min']} minutes</strong>, "
        f"and the slowest 10% of calls took over {s['p90_assembly_min']} minutes."
        if s["median_assembly_min"] is not None else
        "No en-route timestamps were present, so turnout time could not be measured — only "
        "explicit mutual-aid and no-unit dispositions were counted.")

    recognized = ", ".join(f"{_e(k)} &larr; <code>{_e(v)}</code>"
                           for k, v in pr["columns_recognized"].items())
    dropped = pr["columns_dropped_as_phi"]
    dropped_line = (f"<strong>{len(dropped)} column(s) dropped before analysis</strong> because their "
                    f"names indicated patient data: {', '.join(_e(c) for c in dropped)}."
                    if dropped else
                    "No columns were identified as patient data.")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Coverage Reliability Report — {_e(agency)}</title>
<style>
  @page {{ margin: 18mm 16mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #23231f; line-height: 1.5; max-width: 860px; margin: 0 auto; padding: 32px 24px 64px;
         background: #fff; font-size: 14px; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
  h2 {{ font-size: 1.1rem; margin: 32px 0 10px; padding-bottom: 6px; border-bottom: 2px solid #2f5d50; }}
  h3 {{ font-size: 0.95rem; margin: 20px 0 8px; }}
  .sub {{ color: #6b6b63; margin: 0 0 18px; }}
  .demo-banner {{ background: #fdf0d5; border: 2px solid #d9a441; color: #7a4f0c; padding: 10px 14px;
                 border-radius: 6px; font-weight: 700; font-size: 0.85rem; margin-bottom: 20px; }}
  .ref-banner {{ background: #eef3f1; border: 2px solid #2f5d50; color: #23231f; padding: 12px 16px;
                border-radius: 6px; font-size: 0.82rem; margin-bottom: 20px; line-height: 1.55; }}
  .clean-note {{ background: #eef3f1; border-left: 5px solid #2f5d50; padding: 12px 16px;
                border-radius: 4px; margin: 14px 0; font-size: 0.9rem; }}
  .headline.clean {{ background: #faf9f6; border-left-color: #8a8a82; }}
  .headline.clean .big {{ color: #6b6b63; }}
  .headline {{ background: #eef3f1; border-left: 5px solid #2f5d50; padding: 16px 18px;
              border-radius: 4px; margin: 18px 0; }}
  .headline .big {{ font-size: 2.1rem; font-weight: 700; color: #2f5d50; line-height: 1.1; }}
  .headline .cap {{ font-size: 0.9rem; margin-top: 6px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 14px 0; }}
  .tile {{ border: 1px solid #e3e0d8; border-radius: 6px; padding: 10px 12px; }}
  .tile .v {{ font-size: 1.3rem; font-weight: 700; color: #2f5d50; }}
  .tile .l {{ font-size: 0.72rem; color: #6b6b63; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0 4px; font-size: 0.85rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eceae4; }}
  th {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: .04em; color: #6b6b63; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.strong {{ font-weight: 700; color: #2f5d50; }}
  .heatmap {{ display: grid; grid-template-columns: repeat(24, 1fr); gap: 2px; margin: 8px 0; }}
  .hm-cell {{ border: 1px solid #e3e0d8; border-radius: 2px; padding: 4px 0; text-align: center;
             font-size: 0.6rem; display: flex; flex-direction: column; gap: 1px; }}
  .hm-hr {{ color: #8a8a82; font-size: 0.55rem; }}
  .hm-v {{ font-weight: 700; }}
  .note {{ background: #faf9f6; border: 1px solid #e3e0d8; border-radius: 6px; padding: 12px 14px;
          font-size: 0.82rem; margin: 12px 0; }}
  .limits li {{ margin-bottom: 7px; font-size: 0.85rem; }}
  .rec {{ border: 1px solid #e3e0d8; border-left: 4px solid #2f5d50; border-radius: 4px;
         padding: 12px 14px; margin-bottom: 10px; }}
  .rec b {{ display: block; margin-bottom: 4px; }}
  footer {{ margin-top: 36px; padding-top: 12px; border-top: 1px solid #e3e0d8;
           font-size: 0.72rem; color: #6b6b63; }}
  code {{ background: #f2f0ea; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }}
  .noprint {{ margin: 18px 0; }}
  @media print {{ .noprint {{ display: none; }} body {{ padding: 0; }} }}
</style></head><body>

{demo_banner}

<h1>Coverage Reliability Report</h1>
<p class="sub"><strong>{_e(agency)}</strong> &middot; dispatch history
{_e(s['date_range'][0])} to {_e(s['date_range'][1])} ({s['span_years']} years,
{s['incidents']:,} incidents) &middot; prepared {date.today().isoformat()}</p>

<div class="noprint">
  <button onclick="window.print()">Print / Save as PDF</button>
</div>

<h2>1. The finding</h2>

{'<div class="clean-note"><strong>No significant crew-assembly problem was found in this data.</strong> '
 'Turnout is fast and consistent across every hour of the week, which is what a continuously staffed '
 'service looks like. The figures below are reported for completeness and as a baseline for comparison.'
 '</div>' if clean else ''}

<div class="headline{' clean' if clean else ''}">
  <div class="big">{s['failures_per_year']:,} calls per year</div>
  <div class="cap">arrived when this agency could not put a crew on the road promptly —
  {s['no_crew']:,} where no crew formed at all, and {s['severe_delay']:,} where assembly took more
  than {int(10)} minutes. That is <strong>{_pct(s['failure_rate'])}</strong> of all
  {s['incidents']:,} incidents in the period reviewed.</div>
</div>

<div class="grid">
  <div class="tile"><div class="v">{s['calls_per_year']:,}</div><div class="l">calls per year</div></div>
  <div class="tile"><div class="v">{s['no_crew']:,}</div><div class="l">no crew formed</div></div>
  <div class="tile"><div class="v">{s['severe_delay']:,}</div><div class="l">severe assembly delay</div></div>
  <div class="tile"><div class="v">{_pct(s['failure_rate'], 1)}</div><div class="l">of all incidents</div></div>
</div>

<p>{assembly_line}</p>

<h2>2. When it happens</h2>

<p>{ratio_line}</p>

<h3>Failure rate by hour of day (% of incidents in that hour)</h3>
{_heatmap(pat['by_hour'])}
<p class="sub" style="font-size:.78rem;">Darker = higher share of calls in that hour where no crew
formed or assembly exceeded 10 minutes. Hour labels above each cell.</p>

<h3>Worst hour-of-week windows</h3>
<table>
  <tr><th>Window</th><th class="num">Incidents</th><th class="num">Failures</th><th class="num">Rate</th></tr>
  {worst_rows}
</table>
<p class="sub" style="font-size:.78rem;">Cells with fewer than 3 incidents are excluded — too little
volume to be meaningful.</p>

<h3>By day of week</h3>
<table>
  <tr><th>Day</th><th class="num">Incidents</th><th class="num">Failures</th><th class="num">Rate</th></tr>
  {wd_rows}
</table>

<h2>3. What it costs</h2>

<p>At a published mutual-aid rate of <strong>${cost['mutual_aid_fee_assumed']}</strong> per request,
the {cost['mutual_aid_activations_per_year']:,} activations per year implied by this history represent
approximately <strong>${cost['mutual_aid_cost_per_year']:,} per year</strong> in aid fees alone.</p>

<div class="note"><strong>This is a floor, not a full cost.</strong> {_e(cost['basis'])}
It excludes the neighboring agency's unit-hour cost, the operational risk of leaving their own
district uncovered, and any downstream clinical consequence — which this report deliberately does
not attempt to price (see Section 5).</div>

<h2>4. What to do about it</h2>

{'<p><strong>For this dataset: nothing.</strong> The recommendations below are the standard set '
 'this report produces when a coverage problem is present. They are shown here so the format is '
 'visible, but a service with this turnout profile does not need them.</p>' if clean else ''}

<div class="rec"><b>1. Target the specific windows above, not the schedule as a whole.</b>
The failures in this data are concentrated, not uniform. Covering the top {min(3, len(pat['worst_windows']))}
windows listed in Section 2 addresses a disproportionate share of them without asking more of the
roster overall.</div>

<div class="rec"><b>2. Convert reactive mutual aid into a standing arrangement for those windows.</b>
Aid is already being requested {cost['mutual_aid_activations_per_year']:,} times a year. A pre-agreed
window with a neighboring agency turns an emergency phone call into a planned handoff, at the same or
lower cost.</div>

<div class="rec"><b>3. Use this document as grant evidence.</b>
The federal SIREN program (Supporting and Improving Rural EMS Needs) funds recruitment, retention, and
technology for rural EMS at up to $200,000 per award, and applications require exactly this kind of
documented operational need. State EMS offices and county boards ask for the same.</div>

<h2>5. How this was calculated, and what it does not claim</h2>

<p>Every incident was classified by the interval between dispatch (tone-out) and the unit going en
route — the standard measure of crew turnout. Incidents were counted as <em>no crew formed</em> when
mutual aid was requested, the disposition indicated no unit was available, or no en-route time was
recorded; and as <em>severe delay</em> when assembly exceeded 10 minutes. Thresholds follow NFPA 1720
expectations for volunteer services, which allow substantially longer turnout than career departments;
they are set to be defensible for a volunteer agency rather than to maximize the failure count.</p>

<ul class="limits">
  <li><strong>No claim is made about patient outcomes.</strong> A systematic review of 115 studies and
  691,056 patients found insufficient evidence linking EMS response time to survival at these margins.
  This report counts operational failures only and does not convert them into lives, injuries, or a
  value-of-statistical-life figure.</li>
  <li><strong>This is a description of the past, not a forecast.</strong> Nothing here predicts future
  call volume or future failures.</li>
  <li><strong>The analysis is only as complete as the export.</strong> {s['unknown']:,} incidents lacked
  the timestamps needed to classify assembly time and are excluded from the failure counts, meaning the
  true figure may be higher than stated.</li>
  <li><strong>The cost figure uses an external benchmark</strong>, not this agency's actual aid
  agreement. Substituting the real per-request rate will change it.</li>
</ul>

<h2>6. Data handling</h2>

<div class="note">
<p style="margin:0 0 8px;"><strong>Columns read:</strong> {recognized or "none"}.</p>
<p style="margin:0 0 8px;">{dropped_line}</p>
<p style="margin:0;">Only timestamps, unit identifiers, and disposition codes were analyzed. No
patient-level field was read, stored, or reported, and every figure in this document is an aggregate
count or a time-of-day pattern. The uploaded file was held in memory for the duration of the analysis
and was not written to disk.</p>
</div>

<footer>
Coverage Reliability Report &middot; audit {_e(audit_id)} &middot; generated
{date.today().isoformat()} &middot; source: {_e(payload['source'])}<br>
Prepared with the Rural EMS Coverage Forecast prototype. Method and sources:
see <code>data/DATA_SOURCES.md</code>.
</footer>

</body></html>"""
