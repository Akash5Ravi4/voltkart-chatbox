import io

from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

PALETTE = ["#2f5bea", "#e8833a", "#2a9d8f", "#c44569",
           "#8d6cdb", "#7f8c8d", "#f4c430", "#3d9be9"]
FIG_W, FIG_H = 7.5, 4.2  # inches; the PDF export uses the same ratio


def fmt_num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if float(v).is_integer():
        return f"{v:.0f}"
    return f"{v:,.2f}"


def render_chart(spec: dict, dpi: int = 150) -> bytes:
    """Draw a bar / line / pie chart from a stored chart spec and return PNG bytes."""
    labels = [str(x) for x in spec["labels"]]
    series = spec["series"]
    kind = spec["chart_type"]

    fig = Figure(figsize=(FIG_W, FIG_H))  # no pyplot: safe inside web requests
    ax = fig.subplots()

    if kind == "pie":
        ax.pie(
            series[0]["values"], labels=labels, autopct="%1.1f%%",
            startangle=90, counterclock=False,
            colors=[PALETTE[i % len(PALETTE)] for i in range(len(labels))],
        )
        ax.axis("equal")
    else:
        xs = list(range(len(labels)))
        if kind == "line":
            for i, s in enumerate(series):
                color = PALETTE[i % len(PALETTE)]
                ax.plot(xs, s["values"], marker="o", color=color, label=s["name"])
                if len(series) == 1 and len(xs) <= 15:
                    for x, v in zip(xs, s["values"]):
                        ax.annotate(fmt_num(v), (x, v), textcoords="offset points",
                                    xytext=(0, 7), ha="center", fontsize=7)
        else:  # bar
            n = len(series)
            width = 0.8 / n
            for i, s in enumerate(series):
                offset = -0.4 + width * (i + 0.5)
                bars = ax.bar([x + offset for x in xs], s["values"], width,
                              color=PALETTE[i % len(PALETTE)], label=s["name"])
                if len(xs) <= 12 and n <= 2:
                    ax.bar_label(bars, labels=[fmt_num(v) for v in s["values"]],
                                 fontsize=7, padding=2)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels)
        if len(labels) > 6 or max(len(l) for l in labels) > 10:
            for t in ax.get_xticklabels():
                t.set_rotation(30)
                t.set_ha("right")
        ax.set_xlabel(spec["x_label"])
        ax.set_ylabel(spec["y_label"])
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_num(v)))
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        if len(series) > 1:
            ax.legend()

    ax.set_title(spec["title"], fontsize=12, fontweight="bold")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    return buf.getvalue()
