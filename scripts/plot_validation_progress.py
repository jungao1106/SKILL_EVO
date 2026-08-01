#!/usr/bin/env python3
"""Plot validation score progression from a skill-evolution run."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


def pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{100.0 * value:.1f}"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def metric(metrics: dict[str, Any], key: str) -> float | None:
    value = metrics.get(key)
    if value is None:
        return None
    return float(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "iteration",
        "batch_index",
        "candidate_version",
        "accepted_version_before",
        "accepted_version_after",
        "gate",
        "effective_resolved_rate",
        "effective_mean_reward",
        "resolved",
        "effective_trials",
        "diagnostic_trials",
        "diagnostic_rate",
        "delta_vs_reference",
        "reference_effective_resolved_rate",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def svg_line(points: list[tuple[float, float]], color: str, width: int = 3) -> str:
    if not points:
        return ""
    return (
        f'<polyline fill="none" stroke="{color}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round" points="'
        + " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        + '" />'
    )


def render_svg(
    *,
    path: Path,
    title: str,
    rows: list[dict[str, Any]],
    baseline_rate: float | None,
    final_rate: float | None,
) -> None:
    width = 1100
    height = 640
    left = 92
    right = 38
    top = 78
    bottom = 92
    plot_w = width - left - right
    plot_h = height - top - bottom
    xs = [float(row["batch_index"]) for row in rows]
    ys = [float(row["effective_resolved_rate"]) for row in rows]
    x_min = min(xs) if xs else 0.0
    x_max = max(xs) if xs else 1.0
    y_values = ys + [value for value in [baseline_rate, final_rate] if value is not None]
    y_min = max(0.0, min(y_values) - 0.06) if y_values else 0.0
    y_max = min(1.0, max(y_values) + 0.06) if y_values else 1.0
    if y_max - y_min < 0.18:
        center = (y_max + y_min) / 2.0
        y_min = max(0.0, center - 0.09)
        y_max = min(1.0, center + 0.09)

    def sx(value: float) -> float:
        if x_max == x_min:
            return left + plot_w / 2
        return left + (value - x_min) / (x_max - x_min) * plot_w

    def sy(value: float) -> float:
        if y_max == y_min:
            return top + plot_h / 2
        return top + (y_max - value) / (y_max - y_min) * plot_h

    y_ticks = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    y_ticks = [tick for tick in y_ticks if y_min <= tick <= y_max]
    x_ticks = [5, 25, 50, 75, 100]
    x_ticks = [tick for tick in x_ticks if x_min <= tick <= x_max]

    score_points = [(sx(float(row["batch_index"])), sy(float(row["effective_resolved_rate"]))) for row in rows]
    final_x = sx(x_max)

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="640" viewBox="0 0 1100 640">',
        "<style>",
        "text{font-family:Arial,Helvetica,sans-serif;fill:#1f2933} .muted{fill:#697386} .axis{stroke:#344054;stroke-width:1.2} .grid{stroke:#d9dee7;stroke-width:1} .label{font-size:16px} .small{font-size:13px} .title{font-size:28px;font-weight:700}",
        "</style>",
        '<rect width="1100" height="640" fill="#ffffff"/>',
        f'<text x="{left}" y="42" class="title">{escape(title)}</text>',
        f'<text x="{left}" y="66" class="small muted">Validation effective resolved rate over training gates; x-axis uses completed train batch / iteration.</text>',
    ]

    for tick in y_ticks:
        y = sy(tick)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" class="grid"/>')
        parts.append(f'<text x="{left - 14}" y="{y + 5:.2f}" text-anchor="end" class="small muted">{pct(tick)}%</text>')
    for tick in x_ticks:
        x = sx(float(tick))
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_h}" class="grid"/>')
        parts.append(f'<text x="{x:.2f}" y="{top + plot_h + 28}" text-anchor="middle" class="small muted">{tick}</text>')

    parts.extend(
        [
            f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" class="axis"/>',
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="axis"/>',
            f'<text x="{left + plot_w / 2}" y="{height - 26}" text-anchor="middle" class="label">Training batch / validation gate iteration</text>',
            f'<text x="28" y="{top + plot_h / 2}" text-anchor="middle" class="label" transform="rotate(-90 28 {top + plot_h / 2})">Effective resolved rate</text>',
        ]
    )

    if baseline_rate is not None:
        y = sy(baseline_rate)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#8a94a6" stroke-width="2" stroke-dasharray="7 7"/>')
        parts.append(f'<text x="{left + plot_w - 4}" y="{y - 8:.2f}" text-anchor="end" class="small muted">no-skill baseline {pct(baseline_rate)}%</text>')
    if final_rate is not None:
        y = sy(final_rate)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#0f766e" stroke-width="2" stroke-dasharray="4 6"/>')
        parts.append(f'<text x="{left + plot_w - 4}" y="{y - 8:.2f}" text-anchor="end" class="small" fill="#0f766e">final accepted {pct(final_rate)}%</text>')

    parts.append(svg_line(score_points, "#2563eb", 3))
    for row, (x, y) in zip(rows, score_points):
        gate = str(row.get("gate") or "")
        color = "#16a34a" if gate == "accept" else "#dc2626"
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5.2" fill="{color}" stroke="#ffffff" stroke-width="1.5"/>')
        if int(row["iteration"]) in {1, 5, 10, 15, 20}:
            parts.append(f'<text x="{x:.2f}" y="{y - 12:.2f}" text-anchor="middle" class="small">{pct(float(row["effective_resolved_rate"]))}%</text>')

    legend_x = left + 18
    legend_y = top + 20
    parts.extend(
        [
            f'<rect x="{legend_x - 12}" y="{legend_y - 18}" width="266" height="78" rx="6" fill="#ffffff" stroke="#d9dee7"/>',
            f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 32}" y2="{legend_y}" stroke="#2563eb" stroke-width="3"/>',
            f'<text x="{legend_x + 42}" y="{legend_y + 5}" class="small">validation gate score</text>',
            f'<circle cx="{legend_x + 10}" cy="{legend_y + 25}" r="5" fill="#16a34a"/>',
            f'<text x="{legend_x + 42}" y="{legend_y + 30}" class="small">accepted candidate version</text>',
            f'<line x1="{legend_x}" y1="{legend_y + 50}" x2="{legend_x + 32}" y2="{legend_y + 50}" stroke="#8a94a6" stroke-width="2" stroke-dasharray="7 7"/>',
            f'<text x="{legend_x + 42}" y="{legend_y + 55}" class="small">no-skill baseline</text>',
        ]
    )

    parts.append("</svg>")
    path.write_text("\n".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("run_logs/swegym_skill_evo/swegym_novita_glm52_c15_resume_merged_20260630_071956"),
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    run_dir = args.run_dir
    output_dir = args.output_dir or run_dir / "validation" / "plots"
    output_dir.mkdir(parents=True, exist_ok=True)

    history = load_json(run_dir / "validation" / "validation_gate_history.json")
    baseline = load_json(run_dir / "validation" / "baseline_effective_metrics.json")
    final = load_json(run_dir / "validation" / "accepted_skill_effective_metrics.json")

    rows: list[dict[str, Any]] = []
    for gate in history.get("gates", []):
        metrics = gate.get("metrics") or {}
        decision = gate.get("decision") or {}
        rows.append(
            {
                "iteration": gate.get("iteration"),
                "batch_index": gate.get("batch_index"),
                "candidate_version": gate.get("candidate_version"),
                "accepted_version_before": gate.get("accepted_version_before"),
                "accepted_version_after": gate.get("accepted_version_after"),
                "gate": decision.get("gate"),
                "effective_resolved_rate": metric(metrics, "effective_resolved_rate"),
                "effective_mean_reward": metric(metrics, "effective_mean_reward"),
                "resolved": metrics.get("resolved"),
                "effective_trials": metrics.get("effective_trials"),
                "diagnostic_trials": metrics.get("diagnostic_trials"),
                "diagnostic_rate": metric(metrics, "diagnostic_rate"),
                "delta_vs_reference": decision.get("delta"),
                "reference_effective_resolved_rate": decision.get("reference_effective_resolved_rate"),
            }
        )
    rows.sort(key=lambda row: (int(row["iteration"]), int(row["batch_index"])))

    csv_path = output_dir / "validation_progress.csv"
    svg_path = output_dir / "validation_progress.svg"
    json_path = output_dir / "validation_progress_summary.json"
    write_csv(csv_path, rows)
    render_svg(
        path=svg_path,
        title="Validation Score Progression",
        rows=rows,
        baseline_rate=metric(baseline, "effective_resolved_rate"),
        final_rate=metric(final, "effective_resolved_rate"),
    )
    summary = {
        "run_dir": str(run_dir),
        "num_validation_gates": len(rows),
        "baseline_effective_resolved_rate": metric(baseline, "effective_resolved_rate"),
        "final_accepted_effective_resolved_rate": metric(final, "effective_resolved_rate"),
        "best_gate": max(rows, key=lambda row: float(row["effective_resolved_rate"])) if rows else None,
        "last_gate": rows[-1] if rows else None,
        "csv_path": str(csv_path),
        "svg_path": str(svg_path),
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {svg_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
