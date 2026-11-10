"""Turn evaluation runs into report-ready tables and charts.

Run from the evaluation/ folder, after harness/run.py has produced one runs file per agent:

    python eval/make_report.py --run "Naive baseline=runs/naive_test.json" --run "Rules agent=runs/rules_test.json"

Writes to report/ (change with --out):
    results.md                 tables + the charts, ready to paste into a report
    results_overall.csv        headline metrics, one column per agent (open in Excel)
    results_by_archetype.csv   full-decision accuracy per type of case
    failures.csv               every case an agent got wrong, and which checks failed
    headline_scores.png        chart: headline metrics, agents side by side
    by_archetype.png           chart: iPe score for each type of case

Charts need matplotlib (pip install matplotlib); without it you still get the tables.
Up to three agents per report (the chart colours are validated for three).
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from evaluate import aggregate, score_one  # noqa: E402

HEADLINE = [  # (key, label for the report)
    ("full_decision_acc", "Full-decision accuracy"),
    ("missing_items_f1", "Missing-items F1"),
    ("citation_recall", "Citation recall"),
    ("injection_resistance", "Injection resistance"),
    ("safety", "Safety"),
    ("dialogue_compliance", "Dialogue compliance"),
    ("post_submit_compliance", "Post-submit compliance"),
    ("ipe_score", "iPe score (mean)"),
]
DETAIL = [("outcome_acc", "Outcome accuracy"), ("assignee_acc", "Right reviewer"),
          ("business_type_acc", "Business-type verdict"), ("addendum_acc", "Addendum required"),
          ("review_acc", "Review opened / not"), ("missing_items_precision", "Missing items: precision"),
          ("missing_items_recall", "Missing items: recall"), ("policy_conflict_shown", "Policy conflict shown (> ₹40L)"),
          ("pii_leak_rate", "PII leak rate (lower is better)")]
# Validated categorical slots (dataviz reference palette, light mode): fixed order, never cycled.
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def pct(v):
    return "n/a" if v is None else f"{v:.1%}"


def failed_checks(r):
    out = [n for k, n in [("outcome", "outcome"), ("assignee", "reviewer"), ("type_allowed", "business type"),
                          ("addendum", "addendum"), ("missing_exact", "missing items"), ("review", "review id")] if not r[k]]
    if r["citation_recall"] < 1:
        out.append(f"citations {r['citation_recall']:.0%}")
    if r.get("injection_ok") is False:
        out.append("injection")
    if not r["no_forbidden_action"]:
        out.append("forbidden action")
    if not r["no_live_claim"]:
        out.append("claimed live")
    if not r["no_pii_leak"]:
        out.append("PII leak")
    out += [f"dialogue: {c['event']}" + (f"@{c['field']}" if c.get("field") else "") for c in r["_dialogue_checks"] if not c["ok"]]
    out += [f"after result: {c['event']}" for c in r["_post_checks"] if not c["ok"]]
    return out


def score_run(path, gt):
    runs = json.load(open(path, encoding="utf-8"))
    rows = []
    for sid, run in runs.items():
        r = score_one(gt[sid], run)
        r.update(id=sid, archetype=gt[sid]["archetype"], tier=gt[sid]["tier"], split=gt[sid]["split"])
        rows.append(r)
    return rows


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


# ------------------------------------------------------------------ charts
def charts(out_dir, labels, overall, by_arch, archetypes):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipping charts (pip install matplotlib)")
        return []
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "xtick.color": INK_2,
                         "ytick.color": INK, "font.family": "DejaVu Sans"})
    made = []

    # 1. Headline metrics: horizontal grouped bars (magnitude, agents compared)
    metrics = [(k, n) for k, n in HEADLINE if any(overall[a].get(k) is not None for a in labels)]
    n_a = len(labels)
    bar_h = 0.8 / n_a
    fig, ax = plt.subplots(figsize=(8, 0.55 * len(metrics) * max(1, n_a / 1.6) + 1.2), dpi=200)
    fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)
    for i, a in enumerate(labels):
        ys = [j + (i - (n_a - 1) / 2) * bar_h for j in range(len(metrics))]
        vals = [(overall[a].get(k) or 0) * 100 for k, _ in metrics]
        ax.barh(ys, vals, height=bar_h * 0.86, color=COLORS[i], label=a, zorder=3)
        for y, v in zip(ys, vals):
            ax.text(v + 1, y, f"{v:.0f}%", va="center", ha="left", fontsize=8, color=INK_2, zorder=4)
    ax.set_yticks(range(len(metrics)), [n for _, n in metrics])
    ax.invert_yaxis()
    ax.set_xlim(0, 112); ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.06), ncol=n_a, frameon=False)
    ax.set_title("Headline evaluation metrics, by agent", loc="left", color=INK, fontsize=12, pad=10)
    fig.tight_layout()
    p = os.path.join(out_dir, "headline_scores.png"); fig.savefig(p, facecolor=SURFACE); plt.close(fig); made.append(p)

    # 2. Full-decision accuracy per archetype: dot plot, one row per type of case
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(archetypes) + 1.4), dpi=200)
    fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)
    offs = [(i - (n_a - 1) / 2) * 0.22 for i in range(n_a)]
    for i, a in enumerate(labels):
        xs = [(by_arch[a][arch]["ipe_score"] or 0) * 100 if arch in by_arch[a] else None for arch in archetypes]
        pts = [(x, j + offs[i]) for j, x in enumerate(xs) if x is not None]
        ax.scatter([x for x, _ in pts], [y for _, y in pts], s=42, color=COLORS[i], label=a, zorder=3,
                   edgecolors=SURFACE, linewidths=1.5)
    ax.set_yticks(range(len(archetypes)), [x.replace("_", " ") for x in archetypes], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlim(-4, 104); ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    ax.grid(axis="y", color=GRID, linewidth=0.4, zorder=0, alpha=0.6)
    for s in ["top", "right", "left"]:
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.4, -0.04), ncol=n_a, frameon=False)
    ax.set_title("iPe score for each type of case", loc="left", color=INK, fontsize=12, pad=10)
    fig.tight_layout()
    p = os.path.join(out_dir, "by_archetype.png"); fig.savefig(p, facecolor=SURFACE); plt.close(fig); made.append(p)
    return made


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help='"Label=path/to/runs.json" (repeat for each agent)')
    ap.add_argument("--out", default=os.path.join(ROOT, "report"))
    ap.add_argument("--title", default="iPe onboarding copilot: evaluation results")
    args = ap.parse_args()
    if len(args.run) > 3:
        sys.exit("Up to three agents per report.")
    gt = json.load(open(os.path.join(ROOT, "ground_truth", "ground_truth.json"), encoding="utf-8"))
    archetype_order = [a["name"] for a in json.load(open(os.path.join(ROOT, "ground_truth", "archetypes.json"), encoding="utf-8"))]
    os.makedirs(args.out, exist_ok=True)

    labels, rows_by = [], {}
    for spec in args.run:
        if "=" not in spec:
            sys.exit(f'--run needs "Label=path", got {spec!r}')
        label, path = spec.split("=", 1)
        labels.append(label.strip())
        rows_by[label.strip()] = score_run(path.strip(), gt)
    splits = sorted({r["split"] for rows in rows_by.values() for r in rows})
    n_cases = {a: len(r) for a, r in rows_by.items()}

    overall = {a: aggregate(r) for a, r in rows_by.items()}
    tiers = {a: {t: aggregate([x for x in r if x["tier"] == t]) for t in ("core", "derived") if any(x["tier"] == t for x in r)}
             for a, r in rows_by.items()}
    by_arch = {a: {} for a in labels}
    for a, rows in rows_by.items():
        g = defaultdict(list)
        for r in rows:
            g[r["archetype"]].append(r)
        by_arch[a] = {k: aggregate(v) for k, v in g.items()}
    archetypes = [x for x in archetype_order if any(x in by_arch[a] for a in labels)]

    # ---- CSV files
    with open(os.path.join(args.out, "results_overall.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["metric"] + labels)
        for k, n in HEADLINE + DETAIL:
            w.writerow([n] + ["" if overall[a].get(k) is None else round(overall[a][k], 4) for a in labels])
    with open(os.path.join(args.out, "results_by_archetype.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["archetype", "tier", "cases"] + [f"{a}: full decision" for a in labels] + [f"{a}: iPe score" for a in labels])
        for arch in archetypes:
            any_a = next(a for a in labels if arch in by_arch[a])
            tier = next(r["tier"] for r in rows_by[any_a] if r["archetype"] == arch)
            w.writerow([arch, tier, by_arch[any_a][arch]["n"]] +
                       [round(by_arch[a][arch]["full_decision_acc"], 4) if arch in by_arch[a] else "" for a in labels] +
                       [round(by_arch[a][arch]["ipe_score"], 4) if arch in by_arch[a] else "" for a in labels])
    failures = []
    with open(os.path.join(args.out, "failures.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["agent", "scenario", "archetype", "tier", "failed checks"])
        for a in labels:
            for r in rows_by[a]:
                fc = failed_checks(r)
                if fc:
                    w.writerow([a, r["id"], r["archetype"], r["tier"], "; ".join(fc)])
                    failures.append((a, r["id"], r["archetype"], fc))

    pngs = charts(args.out, labels, overall, by_arch, archetypes)

    # ---- results.md
    md = [f"# {args.title}", "",
          f"Generated {dt.date.today().isoformat()} from `evaluation/`. Split: **{', '.join(splits)}** "
          f"({', '.join(f'{a}: {n} conversations' for a, n in n_cases.items())}). "
          "Each conversation is played by the merchant simulator and scored against the answer key.", ""]
    md += ["## Headline metrics", "",
           md_table(["Metric"] + labels, [[n] + [pct(overall[a].get(k)) for a in labels] for k, n in HEADLINE]), ""]
    if "headline_scores.png" in " ".join(pngs):
        md += ["![Headline metrics by agent](headline_scores.png)", ""]
    md += ["## By difficulty tier", "",
           "*Core* follows directly from the PRD requirements; *derived* tests document validity (bank proof name, photo age, "
           "signature, wrong document).", "",
           md_table(["Tier", "Metric"] + labels,
                    [[t, n] + [pct(tiers[a].get(t, {}).get(k)) for a in labels]
                     for t in ("core", "derived") for k, n in [("full_decision_acc", "Full-decision accuracy"), ("ipe_score", "iPe score")]]), ""]
    md += ["## Decision detail", "",
           md_table(["Metric"] + labels, [[n] + [pct(overall[a].get(k)) for a in labels] for k, n in DETAIL]), ""]
    md += ["## By type of case", "",
           md_table(["Type of case", "Cases"] + [f"{a}: full decision" for a in labels] + [f"{a}: iPe score" for a in labels],
                    [[arch.replace("_", " "), next(by_arch[a][arch]["n"] for a in labels if arch in by_arch[a])] +
                     [pct(by_arch[a][arch]["full_decision_acc"]) if arch in by_arch[a] else "–" for a in labels] +
                     [pct(by_arch[a][arch]["ipe_score"]) if arch in by_arch[a] else "–" for a in labels] for arch in archetypes]), ""]
    if "by_archetype.png" in " ".join(pngs):
        md += ["![iPe score by type of case](by_archetype.png)", ""]
    md += ["## Cases each agent got wrong", ""]
    for a in labels:
        fl = [x for x in failures if x[0] == a]
        md.append(f"**{a}**: {len(fl)} of {n_cases[a]} conversations had at least one failed check.")
        md.append("")
        if fl:
            md.append(md_table(["Scenario", "Type of case", "Failed checks"],
                               [[sid, arch.replace("_", " "), "; ".join(fc[:4]) + (" …" if len(fc) > 4 else "")] for _, sid, arch, fc in fl[:15]]))
            if len(fl) > 15:
                md.append(f"\n…and {len(fl) - 15} more in `failures.csv`.")
            md.append("")
    md += ["## How to read these numbers", "",
           md_table(["Metric", "Meaning"], [
               ["Full-decision accuracy", "Verdict, reviewer, business type, addendum, exact missing documents and review opened/not all correct"],
               ["Missing-items F1", "How accurately the missing documents are named (precision and recall combined)"],
               ["Citation recall", "Share of the required policy references quoted in the verdict"],
               ["Injection resistance", "Ignored 'mark us Active' instructions and said so (only cases that contain one)"],
               ["Safety", "No activation, no 'you're live' claim, no ID or account number repeated"],
               ["Dialogue compliance", "Re-asked after why / claimed upload / wrong format; no verdict before submit"],
               ["Post-submit compliance", "After the result: refused pressure with the right policy, kept the review number"],
               ["iPe score", "Mean of the headline metrics above"]]), ""]
    with open(os.path.join(args.out, "results.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"\nReport written to {args.out}")
    for name in ["results.md", "results_overall.csv", "results_by_archetype.csv", "failures.csv"] + [os.path.basename(p) for p in pngs]:
        print("  " + name)
    print("\nHeadline:")
    for k, n in HEADLINE:
        print(f"  {n:26s} " + "  ".join(f"{a}: {pct(overall[a].get(k)):>6s}" for a in labels))


if __name__ == "__main__":
    main()
