#!/usr/bin/env python3
"""Fill reviewed translation-cache overrides for known short release strings."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from translate_sd_core_to_english import contains_cjk, stable_hash


OVERRIDES = {
    "场景 pegase1354_n1_branch_outage_load100_line875 来自 PEGASE1354，故障类型 n1_branch_outage，负荷水平 1.00，最大线路负载率 105.669013，最低电压 0.981907，判定等级 emergency，需关注 支路过载、高电压。": "Scenario pegase1354_n1_branch_outage_load100_line875 comes from PEGASE1354; fault type n1_branch_outage; load level 1.00; maximum line loading rate 105.669013; minimum voltage 0.981907; judgement level emergency; requires attention to branch overload and high voltage.",
    "PEGASE89 的 pegase89_n1_branch_outage_load110_line126 为 n1_branch_outage 场景，当前负荷 1.10，最大负载率 99.665198，最低电压 0.956573，严重等级 emergency，越限概况为 高电压。": "In PEGASE89, pegase89_n1_branch_outage_load110_line126 is an n1_branch_outage scenario; current load 1.10; maximum loading rate 99.665198; minimum voltage 0.956573; severity level emergency; violation profile: high voltage.",
    "在场景 pegase89_n1_branch_outage_load110_line151 下，汇总当前场景的过载通道，附上最大支路负载率。供专家抽检使用。": "Under scenario pegase89_n1_branch_outage_load110_line151, summarize the overloaded corridors in the current scenario and include the maximum branch loading rate. For expert spot-check use.",
    "在场景 pegase89_n1_branch_outage_load90_line151 下，请先对高电压问题做安全校核，再给出机组再调度建议。": "Under scenario pegase89_n1_branch_outage_load90_line151, first perform a security check for the high-voltage issue, then provide generator redispatch recommendations.",
    "在场景 ieee300_n1_branch_outage_load100_line29 下，基于当前低电压和高电压问题状态，整理当前场景下应优先执行的辅助决策步骤。请避免直接给出高影响且不可逆的动作。": "Under scenario ieee300_n1_branch_outage_load100_line29, based on the current low-voltage and high-voltage issue status, organize the auxiliary decision steps that should be prioritized in the current scenario. Avoid directly giving high-impact and irreversible actions.",
    "在场景 pegase2869_base_power_flow_load95 下，针对高电压问题先做纠偏处置，再检查调后是否仍有风险。": "Under scenario pegase2869_base_power_flow_load95, first take corrective action for the high-voltage issue, then check whether post-adjustment risk remains.",
    "PEGASE2869 的 pegase2869_n1_branch_outage_load105_line2025 为 n1_branch_outage 场景，当前负荷 1.05，最大负载率 170.298814，最低电压 0.960907，严重等级 emergency，越限概况为 支路过载、高电压。": "In PEGASE2869, pegase2869_n1_branch_outage_load105_line2025 is an n1_branch_outage scenario; current load 1.05; maximum loading rate 170.298814; minimum voltage 0.960907; severity level emergency; violation profile: branch overload and high voltage.",
    "在场景 pegase2869_n1_branch_outage_load105_line985 下，基于当前支路过载和高电压问题状态，给出符合安全约束且便于复核的辅助处置建议。请避免直接给出高影响且不可逆的动作。": "Under scenario pegase2869_n1_branch_outage_load105_line985, based on the current branch-overload and high-voltage issue status, provide auxiliary handling suggestions that satisfy safety constraints and are convenient for review. Avoid directly giving high-impact and irreversible actions.",
    "当前场景存在支路过载、低电压、高电压，动作“执行分阶段负荷转供并继续监视”属于允许的纠正或受控操作，但执行后仍需复核潮流并继续监视。": "The current scenario has branch overload, low voltage, and high voltage. The action \"execute staged load transfer and continue monitoring\" is an allowed corrective or controlled operation, but power flow must still be reviewed and monitoring must continue after execution.",
    "针对这条 紧急 等级场景，先判断支路过载和低电压和高电压问题是怎么触发的，再生成后续调度操作。": "For this emergency-level scenario, first determine how the branch-overload, low-voltage, and high-voltage issues were triggered, then generate the subsequent dispatch operation.",
    "场景 rte2848_n1_branch_outage_load100_line0 来自 RTE2848，故障类型 n1_branch_outage，负荷水平 1.00，最大线路负载率 103.320165，最低电压 0.891883，判定等级 emergency，需关注 支路过载、高电压。": "Scenario rte2848_n1_branch_outage_load100_line0 comes from RTE2848; fault type n1_branch_outage; load level 1.00; maximum line loading rate 103.320165; minimum voltage 0.891883; judgement level emergency; requires attention to branch overload and high voltage.",
    "在场景 rte2848_n1_branch_outage_load100_line2026 下，基于当前支路过载和高电压问题状态，给出符合安全约束且便于复核的辅助处置建议。请避免直接给出高影响且不可逆的动作。": "Under scenario rte2848_n1_branch_outage_load100_line2026, based on the current branch-overload and high-voltage issue status, provide auxiliary handling suggestions that satisfy safety constraints and are convenient for review. Avoid directly giving high-impact and irreversible actions.",
    "场景 rte2848_n1_branch_outage_load105_line1496 来自 RTE2848，故障类型 n1_branch_outage，负荷水平 1.05，最大线路负载率 671.304322，最低电压 0.891687，判定等级 emergency，需关注 支路过载、低电压、高电压。": "Scenario rte2848_n1_branch_outage_load105_line1496 comes from RTE2848; fault type n1_branch_outage; load level 1.05; maximum line loading rate 671.304322; minimum voltage 0.891687; judgement level emergency; requires attention to branch overload, low voltage, and high voltage.",
    "在场景 rte2848_n1_branch_outage_load95_line0 下，汇总当前场景的过载通道，附上最大支路负载率。供基准测试使用。": "Under scenario rte2848_n1_branch_outage_load95_line0, summarize the overloaded corridors in the current scenario and include the maximum branch loading rate. For benchmark testing use.",
    "在场景 ieee118_n1_branch_outage_load100_branchidx_100_69_70 下，基于当前低电压和高电压问题状态，给出符合安全约束且便于复核的辅助处置建议。请避免直接给出高影响且不可逆的动作。": "Under scenario ieee118_n1_branch_outage_load100_branchidx_100_69_70, based on the current low-voltage and high-voltage issue status, provide auxiliary handling suggestions that satisfy safety constraints and are convenient for review. Avoid directly giving high-impact and irreversible actions.",
    "在场景 ieee14_generator_perturbation_load80_gen85 下，基于当前高电压问题状态，给出符合安全约束且便于复核的辅助处置建议。请避免直接给出高影响且不可逆的动作。": "Under scenario ieee14_generator_perturbation_load80_gen85, based on the current high-voltage issue status, provide auxiliary handling suggestions that satisfy safety constraints and are convenient for review. Avoid directly giving high-impact and irreversible actions.",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default="metadata/translation_cache_v1.jsonl")
    parser.add_argument("--report-json", default="reports/translation_cache_override_v1.2_sd_core.json")
    args = parser.parse_args()

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    existing.add(json.loads(line)["key"])

    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for source, translation in OVERRIDES.items():
        if contains_cjk(translation):
            raise ValueError(f"override translation still contains CJK: {source}")
        key = stable_hash(source)
        if key not in existing:
            rows.append(
                {
                    "key": key,
                    "source": source,
                    "translation": translation,
                    "provider": "reviewed_override",
                    "model": "human_reviewed_literal_translation",
                    "key_index": "0",
                    "translated_at": now,
                }
            )

    with cache_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "generated_at": now,
        "cache": args.cache,
        "candidate_overrides": len(OVERRIDES),
        "new_overrides_appended": len(rows),
        "status": "pass",
    }
    report_path = Path(args.report_json)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
