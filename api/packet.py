"""
The Funding Packet: the audit rendered as the evidence sections a grant
reviewer or county board actually asks for.

Scope is deliberately limited. This generates the sections that require data
the agency does not have time to assemble -- documented need, the specific
ask, projected outcome, cost of inaction. It does NOT generate narrative for
organizational capacity, project management, or letters of support. Auto-
written filler in those sections is obvious to reviewers and would hurt an
application more than a blank page would.
"""

import html
from datetime import date


def _e(s):
    return html.escape(str(s))


def _pct(x, d=1):
    return f"{x * 100:.{d}f}%"


def render_packet(audit_id, payload, plan):
    f = payload["findings"]
    s, pat, cost = f["summary"], f["pattern"], f["cost"]
    agency = payload["agency_name"]
    ref = payload.get("reference_meta")

    ref_banner = (
        '<div class="banner">This packet was generated from a public reference dataset, not from '
        f'{_e(agency)}\'s own submission. It demonstrates format only.</div>' if ref else "")

    # An agency with no meaningful problem must not be handed a manufactured
    # funding case. Generating a grant request for a service whose turnout is
    # already fast would be obvious to any reviewer and would discredit every
    # other packet this tool produces.
    clean = s["failure_rate"] < 0.01
    clean_banner = (
        '<div class="banner clean">NOT A FUNDING CASE. This agency\'s dispatch data shows no '
        f'meaningful crew-assembly problem ({_pct(s["failure_rate"])} of calls). The sections below '
        'are rendered to show the packet format only &mdash; they should not be submitted. A funding '
        'request built on numbers this small would not survive review, and should not.</div>'
        if clean else "")

    blocks_rows = "".join(
        f"<tr><td><strong>{_e(b['label'])}</strong></td><td class='num'>{b['length']} hrs</td>"
        f"<td class='num'>{b['incidents']}</td><td class='num strong'>{b['failures_covered']}</td></tr>"
        for b in plan["blocks"]) or "<tr><td colspan='4'>No qualifying coverage block found.</td></tr>"

    ratio = pat["workday_penalty_ratio"]
    # The volunteer-roster explanation is only offered when the underlying
    # rates are actually material -- a 1.5x ratio between 0.2% and 0.1% is
    # noise, and asserting a staffing cause from it would be wrong.
    pattern_sentence = (
        f"Failures are concentrated, not evenly distributed: weekday daytime calls fail to raise a "
        f"crew {ratio}&times; as often as calls at all other times "
        f"({_pct(pat['workday_failure_rate'])} versus {_pct(pat['offhours_failure_rate'])}). "
        "This is consistent with a volunteer roster whose members hold outside daytime employment."
        if ratio and ratio > 1.2 and pat["workday_failure_rate"] >= 0.03 else
        f"Weekday daytime failure rate is {_pct(pat['workday_failure_rate'])} versus "
        f"{_pct(pat['offhours_failure_rate'])} at other times.")

    cpf = plan["cost_per_failure_prevented"]
    cpf_line = (f"That is approximately <strong>${cpf:,} per uncovered call prevented</strong>, "
                "before accounting for avoided mutual-aid fees." if cpf else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Funding Request Evidence — {_e(agency)}</title>
<style>
  @page {{ margin: 20mm 18mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1c1c19; line-height: 1.6; max-width: 800px; margin: 0 auto;
         padding: 40px 28px 72px; background: #fff; font-size: 14.5px; }}
  h1 {{ font-size: 1.55rem; margin: 0 0 6px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 1rem; margin: 34px 0 12px; text-transform: uppercase; letter-spacing: .07em;
       color: #2f5d50; padding-bottom: 7px; border-bottom: 2px solid #2f5d50; }}
  .sub {{ color: #6b6b63; margin: 0 0 6px; font-size: 0.92rem; }}
  .banner {{ background: #fdf0d5; border: 2px solid #d9a441; color: #7a4f0c; padding: 10px 14px;
            border-radius: 6px; font-size: 0.82rem; font-weight: 600; margin-bottom: 22px; }}
  .banner.clean {{ background: #f4f2ee; border-color: #8a8a82; color: #4a4a44; }}
  .callout {{ background: #eef3f1; border-left: 5px solid #2f5d50; padding: 18px 20px;
             border-radius: 4px; margin: 16px 0; }}
  .callout .big {{ font-size: 1.9rem; font-weight: 700; color: #2f5d50; line-height: 1.15; }}
  .ask {{ border: 2px solid #2f5d50; border-radius: 8px; padding: 20px 22px; margin: 18px 0;
         background: #fff; }}
  .ask .amount {{ font-size: 2.1rem; font-weight: 700; color: #2f5d50; line-height: 1.1; }}
  .ask .what {{ font-size: 0.95rem; margin-top: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 6px; font-size: 0.88rem; }}
  th, td {{ text-align: left; padding: 7px 9px; border-bottom: 1px solid #eceae4; }}
  th {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: .05em; color: #6b6b63; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.strong {{ font-weight: 700; color: #2f5d50; }}
  .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }}
  .stat {{ border: 1px solid #e3e0d8; border-radius: 6px; padding: 12px 14px; }}
  .stat .v {{ font-size: 1.35rem; font-weight: 700; color: #2f5d50; }}
  .stat .l {{ font-size: 0.72rem; color: #6b6b63; margin-top: 3px; }}
  .note {{ background: #faf9f6; border: 1px solid #e3e0d8; border-radius: 6px;
          padding: 13px 16px; font-size: 0.83rem; margin: 14px 0; }}
  .todo {{ background: #fff; border: 1px dashed #b9b6ad; border-radius: 6px; padding: 14px 16px;
          font-size: 0.85rem; margin: 14px 0; color: #55544e; }}
  .todo b {{ color: #1c1c19; }}
  ul {{ margin: 8px 0; padding-left: 20px; }}
  li {{ margin-bottom: 6px; }}
  footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid #e3e0d8;
           font-size: 0.72rem; color: #6b6b63; }}
  .noprint {{ margin: 20px 0; }}
  button {{ font: inherit; padding: 8px 16px; border: 1px solid #2f5d50; background: #2f5d50;
           color: #fff; border-radius: 6px; cursor: pointer; }}
  @media print {{ .noprint {{ display: none; }} body {{ padding: 0; }} }}
</style></head><body>

{ref_banner}
{clean_banner}

<h1>Funding Request &mdash; Supporting Evidence</h1>
<p class="sub"><strong>{_e(agency)}</strong></p>
<p class="sub">Based on {s['incidents']:,} dispatch records, {_e(s['date_range'][0])} to
{_e(s['date_range'][1])} &middot; prepared {date.today().isoformat()}</p>

<div class="noprint"><button onclick="window.print()">Print / Save as PDF</button></div>

<h2>1. Statement of Need</h2>

<div class="callout">
  <div class="big">{s['failures_per_year']:,} calls per year</div>
  <div>arrive when this agency cannot put a crew on the road promptly &mdash;
  {s['no_crew']:,} where no crew formed at all and {s['severe_delay']:,} where assembly exceeded
  10 minutes, representing <strong>{_pct(s['failure_rate'])}</strong> of all dispatches reviewed.</div>
</div>

<p>{pattern_sentence}</p>

<div class="stats">
  <div class="stat"><div class="v">{s['calls_per_year']:,}</div><div class="l">annual call volume</div></div>
  <div class="stat"><div class="v">{s['median_assembly_min'] if s['median_assembly_min'] is not None else '&ndash;'} min</div>
    <div class="l">median dispatch to en route</div></div>
  <div class="stat"><div class="v">${cost['mutual_aid_cost_per_year']:,}</div>
    <div class="l">annual mutual-aid fees</div></div>
</div>

<h2>2. The Specific Request</h2>

<div class="ask">
  <div class="amount">${plan['annual_cost']:,} <span style="font-size:1rem;font-weight:600;">per year</span></div>
  <div class="what">to fund <strong>{plan['hours_used']} hours per week</strong> of scheduled duty-crew
  coverage, at ${plan['cost_per_hour']:.0f}/hour for a staffed two-person crew, placed in the
  specific windows identified below.</div>
</div>

<table>
  <tr><th>Coverage window</th><th class="num">Hours</th><th class="num">Calls in window</th>
      <th class="num">Failures addressed</th></tr>
  {blocks_rows}
</table>

<h2>3. Projected Outcome</h2>

<p>The requested coverage addresses <strong>{plan['failures_covered']:,} of
{plan['failures_total']:,}</strong> documented crew-assembly failures &mdash;
<strong>{plan['coverage_pct']}%</strong> of the total &mdash; by placing staffed crews in the hours
where those failures actually occur. {cpf_line}</p>

<div class="note">
<strong>What this does and does not fix.</strong> A scheduled duty crew eliminates failures caused by
no crew being available. It does not resolve situations where the unit is already committed to
another call, which require a second unit rather than additional staffing. The figure above counts
only the former.
</div>

<h2>4. Cost of No Action</h2>

<p>At the current rate, the agency continues to incur approximately
<strong>${cost['mutual_aid_cost_per_year']:,} per year</strong> in mutual-aid fees alone, and
{s['failures_per_year']:,} calls annually continue to be answered late or by another agency. The
mutual-aid figure is a floor: it excludes the responding agency's own unit-hour cost and the
operational risk of leaving their district uncovered while assisting.</p>

<h2>5. Methodology</h2>

<p>Every incident in the agency's dispatch export was classified by the interval between dispatch
and the unit going en route &mdash; the standard measure of crew turnout. Incidents were counted as
<em>no crew formed</em> where mutual aid was requested, the disposition indicated no unit was
available, or no en-route time was recorded; and as <em>severe delay</em> where assembly exceeded
10 minutes. Thresholds follow NFPA 1720 expectations for predominantly volunteer services, which
allow substantially longer turnout than career departments.</p>

<p>No patient-level information was accessed at any point. Only incident timestamps, unit
identifiers, and disposition codes were analyzed, and every figure in this document is an aggregate
count or a time-of-day pattern.</p>

<p><strong>This document makes no claim about patient outcomes.</strong> A systematic review of 115
studies covering 691,056 patients found insufficient evidence to link EMS response time to survival
at these margins. The need documented here is operational: calls that cannot be answered promptly by
the responding agency.</p>

<h2>6. Sections To Complete</h2>

<div class="todo">
  This packet intentionally covers only the sections that require dispatch-data analysis. The
  following must be written by the agency, and reviewers can tell when they are not:
  <ul>
    <li><b>Organizational capacity</b> &mdash; governance, staffing structure, service area</li>
    <li><b>Project management plan</b> &mdash; who administers the funds and on what timeline</li>
    <li><b>Sustainability</b> &mdash; what happens when the grant period ends</li>
    <li><b>Letters of support</b> &mdash; county, hospital, mutual-aid partners</li>
    <li><b>Match funding</b> &mdash; if the program requires it</li>
  </ul>
</div>

<footer>
Funding Request Evidence &middot; audit {_e(audit_id)} &middot; generated {date.today().isoformat()}
&middot; source: {_e(payload['source'])}<br>
Figures derive from the agency's own dispatch records. Method and data handling: see Section 5.
</footer>

</body></html>"""
