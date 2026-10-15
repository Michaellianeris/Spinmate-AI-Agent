"""LaTeX in report text -> MathML, so indices and exponents render in the HTML report (also printed and offline,
no JavaScript). $...$ and \\(...\\) inline, $$...$$ and \\[...\\] as a block. Bare symbols such as n_z, m_1z,
H_E, 10^-12 or 0.248e-12 outside math are wrapped too; code identifiers (SOT_DC_Amp, settle_time_s) are left alone."""
import html
import re

from latex2mathml.converter import convert

MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|(?<![\\\w$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\w)|\\\((.+?)\\\)", re.S)
# one-letter symbol with a short index: n_z, m_1z, m_{1z}, H_E, A_0, M_s, u_ani
BARE_SUB = re.compile(r"(?<![\w\\{])([A-Za-z])_(\{[^{}\s]{1,8}\}|[A-Za-z0-9]{1,3})(?![\w])")
# exponent: 10^-12, 10^{-12}, Ms^2, cm^2
BARE_SUP = re.compile(r"(?<![\w\\{])(\d+|[A-Za-z]{1,2})\^(\{-?[\w.]{1,6}\}|-?\d{1,3})(?![\w])")


# scientific notation in prose: 0.248e-12 -> 0.248 x 10^-12 (not in code such as --A0=0.248e-12)
SCI = re.compile(r"(?<![\w=.\-])(-?\d+(?:\.\d+)?)[eE]([+-]?\d{1,3})(?![\w.])")


def _bare_to_latex(m):
    base, idx = m.group(1), m.group(2).strip("{}")
    if len(idx) > 1 and idx.isalpha() and not re.fullmatch(r"[xyz]{1,2}", idx):
        idx = r"\mathrm{" + idx + "}"          # descriptive index: H_E, u_ani
    return f"{base}_{{{idx}}}"


def mathml(latex, block=False):
    try:
        return convert(latex.strip(), display="block" if block else "inline")
    except Exception:                          # malformed LaTeX: show it as code rather than break the page
        return f"<code>{html.escape(latex)}</code>"


def split(text):
    """[(is_math, block, content)] with the math segments of text."""
    out, pos = [], 0
    for m in MATH.finditer(text or ""):
        if m.start() > pos:
            out.append((False, False, text[pos:m.start()]))
        block = m.group(1) is not None or m.group(2) is not None
        out.append((True, block, next(g for g in m.groups() if g is not None)))
        pos = m.end()
    if pos < len(text or ""):
        out.append((False, False, text[pos:]))
    return out


def bare(text):
    """Plain text with bare indexed symbols turned into $...$."""
    text = BARE_SUB.sub(lambda m: "$" + _bare_to_latex(m) + "$", text)
    text = SCI.sub(lambda m: f"${m.group(1)}\\times10^{{{int(m.group(2))}}}$", text)
    # a minus sign written right before a symbol: -$m_{2z}$ -> $-m_{2z}$
    text = re.sub(r"(?<![\w-])-\$(?!\$)", "$-", text)
    return BARE_SUP.sub(lambda m: f"${m.group(1)}^{{{m.group(2).strip('{}')}}}$", text)


def to_html(text, inline_fmt=html.escape):
    """Text with LaTeX -> HTML. Math is swapped for placeholders first, so inline_fmt (escaping, Markdown
    bold/italics) can span it: **$n_z$ flips** stays bold."""
    maths = []

    def hold(latex, block=False):
        maths.append(mathml(latex, block))
        return f"\x00{len(maths) - 1}\x00"

    held = []
    for is_math, block, s in split(text or ""):
        if is_math:
            held.append(hold(s, block))
            continue
        for k, chunk in enumerate(re.split(r"(`[^`]*`)", s)):       # never inside `code`
            if k % 2:
                held.append(chunk)
            else:
                held.append("".join(hold(t) if m else t for m, _, t in split(bare(chunk))))
    out = inline_fmt("".join(held))
    return re.sub("\x00(\\d+)\x00", lambda m: maths[int(m.group(1))], out)


def to_markdown(text):
    """Markdown keeps LaTeX; bare symbols get $...$ so GitHub, Obsidian or Typora render them."""
    out = []
    for is_math, block, s in split(text or ""):
        if is_math:
            out.append(f"$${s}$$" if block else f"${s}$")
        else:
            out.append("".join(c if k % 2 else bare(c) for k, c in enumerate(re.split(r"(`[^`]*`)", s))))
    return "".join(out)
