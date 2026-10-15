"""Reports of selected runs: one self-contained HTML page (print it to PDF from the browser) and a
Markdown version with the figures, both kept under WORKSPACE/reports."""
import base64
import json
import html
import io
import re
import time
import zipfile

from . import figures
from . import mathtext
from . import simulations as S

REPORTS = S.WORKSPACE / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

METHOD = (r"Two-sublattice macrospin model of the original SpinMate code: the coupled Landau-Lifshitz-Gilbert "
          r"equations of $\mathbf{m}_1$ and $\mathbf{m}_2$ are integrated with Heun's method. Each sublattice feels "
          r"the inter-sublattice exchange field $4A_0/(\mu_0 M_\mathrm{s}^2 l^2)$ along the other sublattice and the "
          r"uniaxial anisotropy field $2K_\mathrm{u}/(\mu_0 M_\mathrm{s}^2)$ along $\mathbf{u}_\mathrm{ani}$ (both in "
          r"units of $M_\mathrm{s}$), and the applied fields, spin-orbit torques and thermal field set in its parameter "
          r"file. Order is read from $\mathbf{m}_1\cdot\mathbf{m}_2$ (AFM below $-0.9$, FM above $+0.9$); a switch is "
          r"a reversal of the Néel vector $\mathbf{n} = (\mathbf{m}_1 - \mathbf{m}_2)/2$ with $n_z$ crossing from "
          r"below $-0.5$ to above $+0.5$ or back.")


LOGO = S.REPO / "web" / "spinmate-lockup.png"


def _logo_html():
    """SpinMate logo and wordmark at the top of the first page, embedded so the page stays one file."""
    try:
        b64 = base64.b64encode(LOGO.read_bytes()).decode()
    except OSError:
        return ""
    return f"<header class='brandbar'><img src='data:image/png;base64,{b64}' alt='SpinMate'><span>Simulation report</span></header>"


def _num(v, d=3):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{d}g}"
    return str(v)


def _ps(v):
    return "–" if v is None else f"{v * 1e12:.1f}"


def _md_inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)


def tex(s):
    """One line of text with LaTeX (and bare n_z, 10^-12) as HTML."""
    return mathtext.to_html(str(s))


def _join_display_math(text):
    """A $$ ... $$ block written over several lines becomes one line, so the line parser keeps it whole."""
    return re.sub(r"\$\$(.+?)\$\$", lambda m: "$$" + " ".join(m.group(1).split()) + "$$", text or "", flags=re.S)


def md_to_html(text):
    """Small Markdown subset for the notes: headings, lists, bold, italics, code, paragraphs, LaTeX math."""
    out, para, items = [], [], []
    text = _join_display_math(text)

    def inline(s):
        return mathtext.to_html(s, _md_inline)

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            flush()
        elif m := re.match(r"(#{1,4})\s+(.*)", s):
            flush()
            out.append(f"<h3>{inline(m.group(2))}</h3>")
        elif m := re.match(r"[-*]\s+(.*)", s):
            if para:
                flush()
            items.append(m.group(1))
        else:
            if items:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


def _figures(ids, labels):
    """(caption, png bytes) pairs: overlays for several runs, components for each run."""
    out = []
    if len(ids) > 1:
        out.append((r"Néel vector $n_z$ of all runs", figures.render(ids, ["nz"], style="science", fmt="png",
                                                                  width_in=5.5, height_in=3.0, labels=labels)[0]))
        out.append((r"$\mathbf{m}_1\cdot\mathbf{m}_2$ of all runs", figures.render(ids, ["dot"], style="science", fmt="png",
                                                        width_in=5.5, height_in=3.0, labels=labels)[0]))
    for rid in ids[:6]:
        out.append((rf"{rid}: $m_{{1z}}$, $m_{{2z}}$ and $n_z$", figures.render([rid], ["m1z", "m2z", "nz"], style="science",
                                                               fmt="png", width_in=5.5, height_in=2.8)[0]))
    return out


def build(run_ids, title="", notes="", author=""):
    """Writes repNNNN.html and repNNNN.zip (Markdown + figures). Returns their names and the run ids used."""
    ids = [r for r in dict.fromkeys(run_ids or []) if S._valid(r, "r")][:6]
    infos = [i for i in (S.run_info(r, full=True) for r in ids) if i and i.get("status") == "done"]
    if not infos:
        return {"ok": False, "error": "no finished runs among the ids given"}
    ids = [i["id"] for i in infos]
    labels = {i["id"]: i.get("label") or i["id"] for i in infos}
    title = (title or "SpinMate report: " + ", ".join(ids)).strip()[:150]
    cmp = S.compare(ids) if len(ids) > 1 else None
    figs = _figures(ids, labels)
    date = time.strftime("%Y-%m-%d %H:%M")

    head = ["Run", "Label", "Order", r"$\mathbf{m}_1\cdot\mathbf{m}_2$ (end)", r"$n_z$ switches", "First switch (ps)",
            "Settled (ps)", r"$f(m_{1x})$ (GHz)", "Duration (ns)", "Runtime (s)"]
    rows = []
    for i in infos:
        s = i.get("summary") or {}
        dur = (s.get("t_end_s") or 0) - (s.get("t_start_s") or 0)
        rows.append([i["id"], labels[i["id"]], f"{s.get('order_initial')} → {s.get('order_final')}",
                     _num(s.get("m1_dot_m2_final")), _num(s.get("neel_z_switches", s.get("neel_z_sign_changes"))),
                     _ps(s.get("first_neel_z_switch_s")), _ps(s.get("settle_time_s")),
                     _num(s.get("dominant_freq_m1x_GHz")), f"{dur * 1e9:.3g}", _num(s.get("runtime_s"))])
    changed = [(i["id"], i.get("changed_from_defaults") or {}) for i in infos]
    differ = (cmp or {}).get("differing_parameters") or {}
    common = {k: v for k, v in changed[0][1].items()
              if k not in differ and all(json_eq(c.get(k), v) for _, c in changed)}

    # ---- HTML
    def table(h, rs, code_first=False):
        cell = lambda x, k: f"<code>{html.escape(str(x))}</code>" if code_first and k == 0 else html.escape(str(x))
        return ("<table><tr>" + "".join(f"<th>{tex(x)}</th>" for x in h) + "</tr>" +
                "".join("<tr>" + "".join(f"<td>{cell(x, k)}</td>" for k, x in enumerate(r)) + "</tr>" for r in rs) +
                "</table>")

    parts = [_logo_html(), f"<h1>{tex(title)}</h1>",
             f"<p class='meta'>{date}{' · ' + html.escape(author) if author else ''} · runs {', '.join(ids)}</p>",
             "<h2>Method</h2>", f"<p>{tex(METHOD)}</p>",
             "<h2>Results</h2>", table(head, rows)]
    if common:
        parts += ["<h2>Parameters shared by all runs (differing from the defaults)</h2>",
                  table(["Parameter", "Value"], [[k, fmt_val(v)] for k, v in sorted(common.items())], True)]
    if differ:
        parts += ["<h2>Parameters that differ between runs</h2>",
                  table(["Parameter"] + ids, [[k] + [fmt_val(v.get(r)) for r in ids] for k, v in sorted(differ.items())], True)]
    if notes:
        parts += ["<h2>Discussion</h2>", md_to_html(notes)]
    parts.append("<h2>Figures</h2>")
    for n, (cap, png) in enumerate(figs, 1):
        b64 = base64.b64encode(png).decode()
        parts.append(f"<figure><img src='data:image/png;base64,{b64}' alt='{html.escape(re.sub(r"[$\\{}]", "", cap))}'>"
                     f"<figcaption>Figure {n}. {tex(cap)}</figcaption></figure>")
    parts.append("<h2>Appendix: parameter files</h2>")
    for i in infos:
        parts.append(f"<h3>{i['id']} · {html.escape(labels[i['id']])}</h3><pre>{html.escape(i.get('parameters_text') or '')}</pre>")
    page = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(re.sub(r"[$\\{}]", "", title))}</title>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><style>"
            "body{font:15px/1.55 Georgia,'Times New Roman',serif;color:#1A1530;max-width:820px;margin:32px auto;padding:0 18px}"
            ".brandbar{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #E6DFF0;padding-bottom:10px;margin-bottom:22px}"
            ".brandbar img{height:34px;width:auto}.brandbar span{font:12px/1 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;color:#6B5A86}"
            "h1{font-size:1.7rem;margin:0 0 4px}h2{font-size:1.15rem;margin:26px 0 8px;border-bottom:1px solid #E6DFF0;padding-bottom:4px}"
            "h3{font-size:1rem;margin:16px 0 6px}.meta{color:#6B5A86;margin:0 0 18px}"
            "table{border-collapse:collapse;width:100%;font:13px/1.4 system-ui,sans-serif;margin:6px 0}"
            "th,td{border:1px solid #E6DFF0;padding:5px 7px;text-align:left}th{background:#F8F4FB}"
            "figure{margin:18px 0;text-align:center}img{max-width:100%}figcaption{font-size:.9rem;color:#4A3D60;margin-top:4px}"
            "pre{background:#F8F4FB;padding:10px;border-radius:6px;font:12px/1.45 ui-monospace,monospace;white-space:pre-wrap}"
            "code{font-family:ui-monospace,monospace}math{font-size:1.05em}math[display=block]{margin:10px 0}@media print{body{margin:0 auto}.brandbar img{height:30px}h2{break-after:avoid}figure{break-inside:avoid}}"
            "</style></head><body>" + "\n".join(parts) + "</body></html>")

    # ---- Markdown + figures
    md = ["![SpinMate](spinmate-logo.png)", "", f"# {title}", "", f"{date}{' · ' + author if author else ''} · runs {', '.join(ids)}", "",
          "## Method", "", METHOD, "", "## Results", "", md_table(head, rows), ""]
    if common:
        md += ["## Parameters shared by all runs (differing from the defaults)", "",
               md_table(["Parameter", "Value"], [[f"`{k}`", fmt_val(v)] for k, v in sorted(common.items())]), ""]
    if differ:
        md += ["## Parameters that differ between runs", "",
               md_table(["Parameter"] + ids, [[f"`{k}`"] + [fmt_val(v.get(r)) for r in ids] for k, v in sorted(differ.items())]), ""]
    if notes:
        md += ["## Discussion", "", mathtext.to_markdown(notes.strip()), ""]
    md += ["## Figures", ""] + [f"![Figure {n}. {cap}](figure{n}.png)\n" for n, (cap, _) in enumerate(figs, 1)]
    md += ["## Appendix: parameter files", ""]
    for i in infos:
        md += [f"### {i['id']} · {labels[i['id']]}", "", "```", (i.get("parameters_text") or "").strip(), "```", ""]

    name = S._next_id(REPORTS, "rep")          # one folder per report
    (REPORTS / name / "report.html").write_text(page, encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.md", "\n".join(md))
        if LOGO.exists():
            z.write(LOGO, "spinmate-logo.png")
        z.writestr("report.html", page)
        for n, (_, png) in enumerate(figs, 1):
            z.writestr(f"figure{n}.png", png)
    (REPORTS / name / "report.zip").write_bytes(buf.getvalue())
    (REPORTS / name / "report.json").write_text(json.dumps({"report": name, "title": title, "run_ids": ids,
                                                             "created": time.time()}))
    return {"ok": True, "report": name, "run_ids": ids, "title": title,
            "html_url": f"/api/reports/{name}.html", "zip_url": f"/api/reports/{name}.zip"}


def json_eq(a, b):
    return repr(a) == repr(b)


def fmt_val(v):
    if isinstance(v, list):
        return ", ".join(_num(x, 4) for x in v)
    return _num(v, 4) if isinstance(v, float) else ("–" if v is None else str(v))


def md_table(head, rows):
    esc = lambda x: str(x).replace("|", "\\|")
    return "\n".join(["| " + " | ".join(map(esc, head)) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(map(esc, r)) + " |" for r in rows])


def report_file(name):
    m = re.fullmatch(r"(rep\d{4,})\.(html|zip|json)", name or "")
    if not m:
        return None
    path = REPORTS / m.group(1) / f"report.{m.group(2)}"
    return path if path.exists() else None
