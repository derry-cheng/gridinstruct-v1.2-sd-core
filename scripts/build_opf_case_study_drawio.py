#!/usr/bin/env python3
"""Build the editable end-to-end OPF case-study diagram from released evidence."""

from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/gridinstruct_v1.2_sd_core_en.jsonl"
RESULTS = ROOT / "simulation_outputs/opf_closed_loop/auxiliary_opf_results.json"
OUTPUT = ROOT / "figures/sd_core_publication/fig_opf_case_study.drawio"
SCENARIO_ID = "ieee118_n1_branch_outage_load70_branchidx_117_67_80"


def load_record() -> dict:
    with DATASET.open(encoding="utf-8") as handle:
        for line in handle:
            if SCENARIO_ID not in line or "opf2_aux" not in line:
                continue
            row = json.loads(line)
            if row.get("scenario_id") == SCENARIO_ID and row.get("metadata", {}).get("variant_index") == 0:
                return row
    raise RuntimeError(f"OPF case-study record not found: {SCENARIO_ID}")


def load_result() -> dict:
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    return next(row for row in payload["results"] if row["scenario_id"] == SCENARIO_ID)


def fmt(value: float, digits: int) -> str:
    return f"{value:.{digits}f}"


def card(
    cell_id: str,
    x: int,
    y: int,
    width: int,
    height: int,
    title: str,
    icon: str,
    color: str,
    lines: list[str],
) -> str:
    body_html = "".join(
        f'<div style="margin:0 0 10px 0;line-height:1.25;">{html.escape(line)}</div>'
        for line in lines
    )
    body = html.escape(body_html, quote=True)
    return f"""
        <mxCell id="{cell_id}" value="{html.escape(title)}" style="swimlane;horizontal=1;startSize=64;rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={color};swimlaneFillColor=#ffffff;strokeColor=#c7ced8;strokeWidth=1.4;fontColor=#ffffff;fontSize=21;fontStyle=1;fontFamily=Helvetica;align=center;verticalAlign=middle;spacingLeft=34;shadow=0;" vertex="1" parent="1">
          <mxGeometry x="{x}" y="{y}" width="{width}" height="{height}" as="geometry"/>
        </mxCell>
        <mxCell id="{cell_id}_icon" value="{html.escape(icon)}" style="ellipse;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#ffffff;fontColor={color};fontSize=25;fontStyle=1;fontFamily=Helvetica;align=center;verticalAlign=middle;shadow=0;" vertex="1" parent="{cell_id}">
          <mxGeometry x="16" y="10" width="42" height="42" as="geometry"/>
        </mxCell>
        <mxCell id="{cell_id}_body" value="{body}" style="text;html=1;strokeColor=none;fillColor=none;fontColor=#263238;fontSize=19;fontFamily=Helvetica;align=left;verticalAlign=top;whiteSpace=wrap;spacing=0;" vertex="1" parent="{cell_id}">
          <mxGeometry x="24" y="86" width="{width - 48}" height="{height - 106}" as="geometry"/>
        </mxCell>
    """


def edge(cell_id: str, source: str, target: str) -> str:
    return f"""
        <mxCell id="{cell_id}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#46525c;strokeWidth=2;endArrow=block;endFill=1;exitX=1;exitY=0.5;entryX=0;entryY=0.5;" edge="1" parent="1" source="{source}" target="{target}">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
    """


def main() -> None:
    record = load_record()
    result = load_result()
    pre = result["pre_action"]
    post = result["post_action"]
    controls = record["executable_control_target"]["controls"]
    replay = record["target_replay_validation"]

    active_changes = []
    for control in controls:
        delta = control["commanded_setpoint"]["delta"]
        if "p_mw" in delta and abs(delta["p_mw"]) > 1e-4:
            active_changes.append((abs(delta["p_mw"]), control["device_id"], delta["p_mw"]))
    largest = sorted(active_changes, reverse=True)[:3]
    ext = next(control for control in controls if control["element"] == "ext_grid")
    ext_pre = ext["commanded_setpoint"]["pre_action"]["vm_pu"]
    ext_post = ext["commanded_setpoint"]["absolute"]["vm_pu"]

    n1 = result["post_action_n1"]
    stress = result["load_uncertainty_construction_gate"]
    cards = [
        card(
            "request",
            20,
            20,
            460,
            300,
            "Dispatcher request",
            "?",
            "#497d89",
            [
                "IEEE118 N-1 branch outage",
                "Restore thermal and voltage margins",
                "Use reversible redispatch and voltage control",
                "Locate → execute → review",
            ],
        ),
        card(
            "state",
            520,
            20,
            460,
            300,
            "Bound input state",
            "∿",
            "#497d89",
            [
                "Load level: 0.70 p.u.",
                f"Maximum branch loading: {fmt(pre['max_branch_loading_percent'], 2)}%",
                f"Minimum bus voltage: {fmt(pre['min_bus_voltage_pu'], 3)} p.u.",
                f"{pre['overloaded_branch_count']} overloaded branches; {pre['voltage_violation_count']} voltage violation",
                f"Violation score: {fmt(pre['constraint_violation_score'], 4)}",
            ],
        ),
        card(
            "target",
            1020,
            20,
            460,
            300,
            "Typed output",
            "{ }",
            "#547aa5",
            [
                "Tool sequence: PF → AC-OPF → PF",
                "Absolute generator P/V and external-grid V set-points",
                f"{replay['validated_control_count']} control entries replayed",
                "Complete vector retained with scenario and rule provenance",
            ],
        ),
        card(
            "control",
            170,
            390,
            560,
            320,
            "Executed controls",
            "⚙",
            "#b98542",
            [
                f"External-grid V: {fmt(ext_pre, 3)} → {fmt(ext_post, 3)} p.u.",
                f"gen43 ΔP: {largest[0][2]:+.2f} MW",
                f"gen11 ΔP: {largest[1][2]:+.2f} MW",
                f"gen28 ΔP: {largest[2][2]:+.2f} MW",
                "All commands and realized P/Q responses checked against native capability",
            ],
        ),
        card(
            "review",
            770,
            390,
            560,
            320,
            "Post-action review",
            "✓",
            "#4f8768",
            [
                f"Maximum branch loading: {fmt(post['max_branch_loading_percent'], 2)}%",
                f"Minimum bus voltage: {fmt(post['min_bus_voltage_pu'], 4)} p.u.",
                "Overload and voltage violations: 0",
                f"N-1 security: {n1['secure_checks']}/{n1['requested_checks']} checks",
                f"Load stress: {stress['safe_case_count']}/{stress['registered_case_count']} states",
                f"Balance residual: {fmt(post['power_balance_residual_mw'], 1)} MW",
            ],
        ),
    ]

    xml = f"""<mxfile host="drawio" version="26.0.0">
  <diagram id="opf-case-study" name="Page-1">
    <mxGraphModel dx="1500" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1500" pageHeight="800" math="0" shadow="0">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        {''.join(cards)}
        {edge('e1', 'request', 'state')}
        {edge('e2', 'state', 'target')}
        {edge('e4', 'control', 'review')}
        <mxCell id="e3" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#46525c;strokeWidth=2;endArrow=block;endFill=1;exitX=0.5;exitY=1;entryX=0.5;entryY=0;" edge="1" parent="1" source="target" target="control">
          <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="1250" y="355"/><mxPoint x="450" y="355"/></Array></mxGeometry>
        </mxCell>
        <mxCell id="accepted" value="Accepted record: typed target + executable control + replay evidence" style="rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor=#eef7f1;strokeColor=#6f9f81;strokeWidth=1.2;fontColor=#315942;fontSize=19;fontStyle=1;fontFamily=Helvetica;align=center;verticalAlign=middle;shadow=0;" vertex="1" parent="1">
          <mxGeometry x="370" y="745" width="760" height="44" as="geometry"/>
        </mxCell>
        <mxCell id="e5" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor=#4f8768;strokeWidth=2;endArrow=block;endFill=1;exitX=0.5;exitY=1;entryX=1;entryY=0.5;" edge="1" parent="1" source="review" target="accepted">
          <mxGeometry relative="1" as="geometry"><Array as="points"><mxPoint x="1050" y="727"/><mxPoint x="1160" y="727"/></Array></mxGeometry>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""
    OUTPUT.write_text(xml, encoding="utf-8")
    print(json.dumps({"status": "pass", "scenario_id": SCENARIO_ID, "output": str(OUTPUT)}, indent=2))


if __name__ == "__main__":
    main()
