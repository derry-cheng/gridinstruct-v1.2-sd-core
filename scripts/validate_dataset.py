"""Validate GridInstruct JSONL records and write parseable reports."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft7Validator

from gridinstruct_utils import ROOT, ensure_dirs, read_json, read_jsonl, write_json
from query_contract import is_legacy_query, validate_query_contract

LEGACY_SCENARIO_PREFIX = "sani" + "ty_"


def has_overload(scenario: dict) -> bool:
    if scenario.get("overloaded_branches"):
        return True
    max_loading = float(scenario.get("max_branch_loading_percent") or 0)
    return max_loading > 100.0


def low_voltage_violations(scenario: dict) -> list[dict]:
    return [
        item
        for item in scenario.get("voltage_violations", [])
        if isinstance(item, dict) and float(item.get("vm_pu", 1.0)) < 1.0
    ]


def high_voltage_violations(scenario: dict) -> list[dict]:
    return [
        item
        for item in scenario.get("voltage_violations", [])
        if isinstance(item, dict) and float(item.get("vm_pu", 1.0)) > 1.0
    ]


def active_security_issues(scenario: dict) -> list[str]:
    issues = []
    if has_overload(scenario):
        issues.append("overload")
    if low_voltage_violations(scenario):
        issues.append("low_voltage")
    if high_voltage_violations(scenario):
        issues.append("high_voltage")
    return issues


def issue_profile(scenario: dict) -> str:
    issues = set(active_security_issues(scenario))
    if "overload" in issues and ("low_voltage" in issues or "high_voltage" in issues):
        return "combined"
    if "overload" in issues:
        return "thermal"
    if "low_voltage" in issues or "high_voltage" in issues:
        return "voltage"
    return "normal"


def near_limit_scenario(scenario: dict) -> bool:
    max_loading = float(scenario.get("max_branch_loading_percent") or 0.0)
    min_vm = float(scenario.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(scenario.get("max_bus_voltage_pu") or 1.0)
    return max_loading >= 98.0 or min_vm < 0.97 or max_vm > 1.04


def compliance_category_for_action(action: str | None) -> str | None:
    category = {
        "继续增加受限断面潮流": "risky_increase",
        "继续提升受限断面潮流": "risky_increase",
        "继续提升受限断面输送功率": "risky_increase",
        "持续增加受限断面输送功率": "risky_increase",
        "进一步提高受限断面送出": "risky_increase",
        "增加受限通道输送水平": "risky_increase",
        "继续扩大受限断面传输功率": "risky_increase",
        "维持增送计划并提高断面潮流": "risky_increase",
        "维持当前潮流并启动监视": "monitor_only",
        "保持当前潮流并开展连续监视": "monitor_only",
        "暂不调整潮流并加强监视": "monitor_only",
        "维持现有运行方式并跟踪越限变化": "monitor_only",
        "不改变潮流分布，仅执行实时监视": "monitor_only",
        "保持当前断面送出并启动复核监视": "monitor_only",
        "先执行无功支撑后复核潮流": "corrective_voltage",
        "先实施无功支持措施，随后验证潮流分布": "corrective_voltage",
        "先执行无功补偿再复核潮流": "corrective_voltage",
        "先投切无功补偿再复核电压和潮流": "corrective_voltage",
        "先进行电压支撑后重新校核潮流": "corrective_voltage",
        "先安排无功调节并复核电压越限": "corrective_voltage",
        "调整邻近机组出力降低断面负载": "corrective_redispatch",
        "先压降受限断面送出再复算潮流": "corrective_redispatch",
        "重新分配邻近机组出力以降低断面负载": "corrective_redispatch",
        "下调受限断面送出并复核潮流": "corrective_redispatch",
        "通过机组再调度释放断面裕度": "corrective_redispatch",
        "调整发电出力组合以缓解过载断面": "corrective_redispatch",
        "先切换局部拓扑后检查越限是否消除": "corrective_topology",
        "执行分阶段负荷转供并继续监视": "corrective_topology",
        "先调整局部网络拓扑再复核越限": "corrective_topology",
        "分阶段转供负荷后持续监视潮流": "corrective_topology",
        "先实施拓扑转移再校核低压和过载": "corrective_topology",
        "局部倒换供电路径后检查安全裕度": "corrective_topology",
        "不校核设备状态直接恢复故障线路运行": "premature_restore",
        "直接恢复故障线路运行，不校核设备状态": "premature_restore",
        "直接恢复故障线路运行，不进行设备状态校核": "premature_restore",
        "未进行设备状态确认即恢复故障线路运行": "premature_restore",
        "未确认设备状态即恢复故障线路运行": "premature_restore",
        "跳过状态校核直接送电故障线路": "premature_restore",
        "不做设备状态确认即恢复退出线路": "premature_restore",
    }.get(action)
    if category:
        return category
    normalized = str(action or "").strip().lower()
    if not normalized:
        return None
    if any(token in normalized for token in ("continue increasing", "continue to increase", "further increase", "increase transmission", "increase constrained")) and "power" in normalized:
        return "risky_increase"
    if "monitor" in normalized and any(token in normalized for token in ("maintain", "keep", "start", "current")):
        return "monitor_only"
    if any(token in normalized for token in ("reactive power", "reactive support", "reactive compensation", "voltage support")):
        return "corrective_voltage"
    if any(token in normalized for token in ("adjust output", "redispatch", "reduce section load", "reduce output of constrained", "output allocation")):
        return "corrective_redispatch"
    if any(token in normalized for token in ("topology", "load transfer", "transfer load", "switch local")):
        return "corrective_topology"
    if any(token in normalized for token in ("restore", "restoring")) and any(token in normalized for token in ("fault", "without verify", "without checking", "do not verify")):
        return "premature_restore"
    return None


def expected_compliance_label(
    scenario: dict,
    action: str | None = None,
    action_category: str | None = None,
) -> str | None:
    category = action_category or compliance_category_for_action(action)
    if category is None:
        return None

    profile = issue_profile(scenario)
    severity = scenario.get("severity_level") or "normal"
    max_loading = float(scenario.get("max_branch_loading_percent") or 0.0)
    min_vm = float(scenario.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(scenario.get("max_bus_voltage_pu") or 1.0)
    near_limit = near_limit_scenario(scenario)
    severe_thermal = max_loading >= 115.0
    severe_voltage = min_vm < 0.92 or max_vm > 1.08

    if category == "premature_restore":
        return "non_compliant" if profile != "normal" or severity != "normal" else "compliant_with_monitoring"

    if category == "risky_increase":
        return "non_compliant" if profile != "normal" or near_limit else "compliant_with_monitoring"

    if profile == "normal":
        if category == "monitor_only":
            return "compliant" if not near_limit else "compliant_with_monitoring"
        if category in {"corrective_voltage", "corrective_redispatch"}:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "monitor_only":
        if severity == "emergency" or profile == "combined" or severe_thermal or severe_voltage:
            return "non_compliant"
        return "compliant_with_monitoring"

    if category == "corrective_voltage":
        if profile == "thermal" and severe_thermal:
            return "non_compliant"
        if profile == "thermal" and max_loading < 108.0:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "corrective_redispatch":
        if profile == "voltage" and severe_voltage:
            return "non_compliant"
        if profile == "thermal" and max_loading < 105.0:
            return "compliant"
        return "compliant_with_monitoring"

    if category == "corrective_topology":
        if profile == "voltage" and severe_voltage and not has_overload(scenario):
            return "non_compliant"
        if profile == "thermal" and max_loading < 108.0:
            return "compliant"
        return "compliant_with_monitoring"

    return "compliant_with_monitoring" if active_security_issues(scenario) else "compliant"


def validate_compliance_row(row: dict, scenario: dict | None) -> list[str]:
    if not scenario:
        return []
    issues = active_security_issues(scenario)
    action = row.get("input", {}).get("proposed_action")
    metadata = row.get("metadata") or {}
    action_category = metadata.get("action_category")
    label = row.get("compliance_label")
    rationale = row.get("rationale", "")
    rationale_lower = str(rationale).lower()
    errors = []
    expected_label = expected_compliance_label(scenario, action, action_category)
    if expected_label is not None and label != expected_label:
        errors.append("compliance_label_mismatch_with_generation_logic")
    denies_existing_issues = (
        "未见热稳定或电压越限" in rationale
        or "no thermal stability or voltage violation" in rationale_lower
        or "no thermal or voltage violation" in rationale_lower
        or "no overload or voltage violation" in rationale_lower
    )
    near_limit_rationale = (
        "接近边界" in rationale
        or "near the boundary" in rationale_lower
        or "approaching the boundary" in rationale_lower
        or "close to the boundary" in rationale_lower
        or "near limit" in rationale_lower
        or "close to limit" in rationale_lower
        or "approaching the limit" in rationale_lower
        or "security margin" in rationale_lower
        or "safety margin" in rationale_lower
    )
    if issues and denies_existing_issues:
        errors.append("rationale_denies_existing_issues")
    if not issues and label == "non_compliant" and not (near_limit_scenario(scenario) or near_limit_rationale):
        errors.append("non_compliant_without_detected_issue")
    if "当前场景存在未见热稳定或电压越限" in rationale:
        errors.append("self_contradictory_rationale")
    return errors


def validate_query_row(row: dict, scenario: dict | None) -> list[str]:
    if not scenario:
        return []
    structured_query = row.get("structured_query") or {}
    filter_expr = structured_query.get("filter")
    query_result = row.get("query_result")
    tool_plan = row.get("tool_plan", [])
    rationale = row.get("rationale", "")
    rationale_lower = str(rationale).lower()
    errors = validate_query_contract(row, scenario)
    if not tool_plan or tool_plan[0].get("args", {}).get("filter") != filter_expr:
        errors.append("tool_plan_filter_mismatch")
    if filter_expr == "loading_percent > 100":
        if is_legacy_query(structured_query):
            if not isinstance(query_result, list):
                errors.append("legacy_overload_query_result_not_list")
        elif not isinstance(query_result, dict):
            errors.append("overload_query_result_not_object")
    elif filter_expr == "vm_pu < 0.95":
        if not isinstance(query_result, list):
            errors.append("low_voltage_query_result_not_list")
        elif any(float(item.get("vm_pu", 1.0)) >= 0.95 for item in query_result if isinstance(item, dict)):
            errors.append("low_voltage_query_result_contains_non_low_voltage")
        mentions_low_voltage_threshold = (
            "低于 0.95" in rationale
            or ("0.95" in rationale and ("below" in rationale_lower or "<" in rationale))
        )
        if not mentions_low_voltage_threshold:
            errors.append("low_voltage_rationale_mismatch")
    elif filter_expr == "violation_type != none":
        if not isinstance(query_result, dict) or "violation_type" not in query_result or "severity_level" not in query_result:
            errors.append("severity_summary_query_result_invalid")
        mentions_violation_type = "越限类型" in rationale or "violation type" in rationale_lower
        mentions_severity = "严重" in rationale or "severity" in rationale_lower
        if not (mentions_violation_type and mentions_severity):
            errors.append("severity_summary_rationale_mismatch")
    return errors


def validate_mirrored_fields(row: dict) -> list[str]:
    errors = []
    task_type = row.get("task_type")
    output = row.get("output")

    if task_type == "dispatcher_intent_tool_call":
        if not isinstance(output, dict):
            errors.append("dispatcher_output_not_dict")
        else:
            if row.get("intent") != output.get("intent"):
                errors.append("dispatcher_intent_output_mismatch")
            if row.get("slots") != output.get("slots"):
                errors.append("dispatcher_slots_output_mismatch")
    elif task_type == "intelligent_data_query":
        if not isinstance(output, dict):
            errors.append("query_output_not_dict")
        else:
            if row.get("structured_query") != output.get("structured_query"):
                errors.append("structured_query_output_mismatch")
            if row.get("query_result") != output.get("query_result"):
                errors.append("query_result_output_mismatch")
    elif task_type == "auxiliary_decision" and row.get("chosen_response") != output:
        errors.append("auxiliary_output_mismatch")

    return errors


def validate_records(input_path: Path) -> dict:
    rows = read_jsonl(input_path)
    schema = read_json(ROOT / "metadata/schema.json")
    rules = {row["rule_id"] for row in read_json(ROOT / "rules/regulation_rules.json")}
    scenario_path = ROOT / "simulation_outputs/contingency/scenarios_converged.json"
    scenarios = {}
    if scenario_path.exists():
        scenarios = {row["scenario_id"]: row for row in read_json(scenario_path)}
    # Records may reference scenarios that are registered in additional
    # authoritative sources merged into the release AFTER scenarios_converged.json
    # is finalized (e.g. the IEEE14 OPF security-candidate register). Resolve
    # scenario links against the union so valid references are not flagged.
    for extra in (
        "simulation_outputs/opf_closed_loop/ieee14_secure_candidate_scenarios_v1.json",
    ):
        extra_path = ROOT / extra
        if extra_path.exists():
            try:
                for row in read_json(extra_path):
                    scenarios.setdefault(row["scenario_id"], row)
            except (OSError, KeyError, ValueError, TypeError):
                pass

    validator = Draft7Validator(schema)
    schema_errors = []
    ids = []
    invalid_rule_links = []
    invalid_scenario_links = []
    tool_plan_errors = []
    task_field_errors = []
    semantic_errors = []
    text_fingerprints = []

    for idx, row in enumerate(rows):
        ids.append(row.get("id"))
        for err in validator.iter_errors(row):
            schema_errors.append({"line": idx + 1, "id": row.get("id"), "error": err.message})

        for rule_id in row.get("source_regulation_ids", []):
            if rule_id not in rules:
                invalid_rule_links.append({"line": idx + 1, "id": row.get("id"), "rule_id": rule_id})

        scenario_id = row.get("scenario_id")
        if scenario_id and scenarios and scenario_id not in scenarios and not str(scenario_id).startswith(LEGACY_SCENARIO_PREFIX):
            invalid_scenario_links.append({"line": idx + 1, "id": row.get("id"), "scenario_id": scenario_id})

        for step in row.get("tool_plan", []):
            if not isinstance(step, dict) or "tool" not in step or "args" not in step:
                tool_plan_errors.append({"line": idx + 1, "id": row.get("id"), "step": step})

        task_type = row.get("task_type")
        if task_type in {"operation_ticket_check", "regulation_compliance_check"} and not row.get("compliance_label"):
            task_field_errors.append({"line": idx + 1, "id": row.get("id"), "missing": "compliance_label"})
        if task_type == "dispatcher_intent_tool_call" and (not row.get("intent") or not isinstance(row.get("slots"), dict)):
            task_field_errors.append({"line": idx + 1, "id": row.get("id"), "missing": "intent_or_slots"})
        if task_type == "intelligent_data_query" and (not row.get("structured_query") or "query_result" not in row):
            task_field_errors.append({"line": idx + 1, "id": row.get("id"), "missing": "structured_query_or_query_result"})
        if task_type == "auxiliary_decision" and (not row.get("chosen_response") or not row.get("rejected_response")):
            task_field_errors.append({"line": idx + 1, "id": row.get("id"), "missing": "preference_pair"})

        scenario = scenarios.get(scenario_id) if scenario_id else None
        if task_type == "regulation_compliance_check":
            for error in validate_compliance_row(row, scenario):
                semantic_errors.append({"line": idx + 1, "id": row.get("id"), "error": error})
        if task_type == "intelligent_data_query":
            for error in validate_query_row(row, scenario):
                semantic_errors.append({"line": idx + 1, "id": row.get("id"), "error": error})
        for error in validate_mirrored_fields(row):
            semantic_errors.append({"line": idx + 1, "id": row.get("id"), "error": error})

        text_fingerprints.append(
            (
                row.get("task_type"),
                row.get("instruction"),
                str(row.get("input"))[:300],
                str(row.get("output"))[:300],
            )
        )

    id_counts = Counter(ids)
    duplicate_ids = [item for item, count in id_counts.items() if count > 1]
    dup_counts = Counter(text_fingerprints)
    near_duplicate_count = sum(count - 1 for count in dup_counts.values() if count > 1)

    task_counts = Counter(row.get("task_type") for row in rows)
    stage_counts = Counter(row.get("task_stage") for row in rows)
    topology_records = [row for row in rows if row.get("scenario_id")]

    near_duplicate_rate = near_duplicate_count / max(len(rows), 1)
    near_duplicate_threshold = 0.02
    try:
        report_input = str(input_path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        report_input = str(input_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not (
            schema_errors
            or duplicate_ids
            or invalid_rule_links
            or invalid_scenario_links
            or tool_plan_errors
            or task_field_errors
            or semantic_errors
            or near_duplicate_rate > near_duplicate_threshold
        ) else "fail",
        "input": report_input,
        "total_records": len(rows),
        "schema_errors": schema_errors,
        "schema_pass_rate": 1 - (len(schema_errors) / max(len(rows), 1)),
        "duplicate_ids": duplicate_ids,
        "id_unique_rate": 1 - (len(duplicate_ids) / max(len(rows), 1)),
        "near_duplicate_count": near_duplicate_count,
        "near_duplicate_rate": near_duplicate_rate,
        "near_duplicate_threshold": near_duplicate_threshold,
        "invalid_rule_links": invalid_rule_links,
        "invalid_scenario_links": invalid_scenario_links,
        "topology_record_count": len(topology_records),
        "topology_link_valid_rate": 1 - (len(invalid_scenario_links) / max(len(topology_records), 1)),
        "tool_plan_errors": tool_plan_errors,
        "task_field_errors": task_field_errors,
        "semantic_errors": semantic_errors,
        "task_counts": dict(task_counts),
        "stage_counts": dict(stage_counts),
        "passed": not (
            schema_errors
            or duplicate_ids
            or invalid_rule_links
            or invalid_scenario_links
            or tool_plan_errors
            or task_field_errors
            or semantic_errors
            or near_duplicate_rate > near_duplicate_threshold
        ),
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# Data Validation Report",
        "",
        f"- Input: `{report['input']}`",
        f"- Total records: {report['total_records']}",
        f"- Schema pass rate: {report['schema_pass_rate']:.4f}",
        f"- ID unique rate: {report['id_unique_rate']:.4f}",
        f"- Topology link valid rate: {report['topology_link_valid_rate']:.4f}",
        f"- Near duplicate rate: {report['near_duplicate_rate']:.4f} (threshold {report['near_duplicate_threshold']:.4f})",
        f"- Passed: {report['passed']}",
        "",
        "## Task Counts",
        "",
    ]
    for task, count in sorted(report["task_counts"].items()):
        lines.append(f"- {task}: {count}")
    lines.extend(["", "## Blocking Issues", ""])
    for key in ["schema_errors", "duplicate_ids", "invalid_rule_links", "invalid_scenario_links", "tool_plan_errors", "task_field_errors", "semantic_errors"]:
        value = report[key]
        lines.append(f"- {key}: {len(value)}")
    for key in ["schema_errors", "invalid_rule_links", "invalid_scenario_links", "tool_plan_errors", "task_field_errors", "semantic_errors"]:
        if report[key]:
            lines.extend(["", f"## {key} Examples", ""])
            for item in report[key][:20]:
                lines.append(f"- `{item}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/gridinstruct_v0.1.jsonl")
    parser.add_argument("--report-prefix", default="reports/data_validation_report")
    args = parser.parse_args()

    report = validate_records(ROOT / args.input)
    ensure_dirs(ROOT / "reports")
    prefix = ROOT / args.report_prefix
    write_json(Path(str(prefix) + ".json"), report)
    write_markdown(report, Path(str(prefix) + ".md"))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
