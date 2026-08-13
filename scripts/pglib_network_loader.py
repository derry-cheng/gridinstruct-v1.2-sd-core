#!/usr/bin/env python3
"""Load the eleven formal network cases from a pinned PGLib-OPF checkout.

The IEEE-300 PGLib operating point does not converge under the formal
pandapower Newton-Raphson settings.  Its topology is bijective with
``pandapower.networks.case300``.  For that case only, this module uses the
convergent bundled operating point, transfers every PGLib RATE_A value onto the
matching branch, and records the bridge explicitly in the returned network.
"""

from __future__ import annotations

import copy
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandapower.networks as pn
from pandapower.converter.matpower.from_mpc import from_mpc

from power_evidence_common import (
    PGLIB_CASE_FILES,
    PGLIB_EXPECTED_COMMIT,
    load_pglib_case,
    normalize_transformer_contract,
    validate_pglib_repository,
)


FORMAL_SYSTEMS = tuple(PGLIB_CASE_FILES)


def _bus_source_id(net, bus_index: int) -> int:
    value = net.bus.at[int(bus_index), "name"]
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bus {bus_index} has no numeric source identifier: {value!r}") from exc


def _pglib_branch_pools(
    branch_rows: list[list[float]],
) -> dict[tuple[int, int], list[tuple[int, list[float]]]]:
    pools: dict[tuple[int, int], list[tuple[int, list[float]]]] = defaultdict(list)
    for row_number, row in enumerate(branch_rows, start=1):
        if int(row[10]) == 0:
            continue
        key = tuple(sorted((int(row[0]), int(row[1]))))
        pools[key].append((row_number, row))
    return pools


def _preserve_trafo_impedance_while_setting_rating(
    net,
    index: int,
    rating_mva: float,
) -> None:
    old_rating = float(net.trafo.at[index, "sn_mva"])
    if not math.isfinite(old_rating) or old_rating <= 0:
        raise ValueError(f"IEEE-300 transformer {index} has invalid original sn_mva")
    scale = rating_mva / old_rating
    for field in ("vk_percent", "vkr_percent"):
        net.trafo.at[index, field] = float(net.trafo.at[index, field]) * scale
    net.trafo.at[index, "sn_mva"] = rating_mva


def _apply_ieee300_pglib_ratings(net, case: dict[str, Any]) -> dict[str, Any]:
    pools = _pglib_branch_pools(case["branch"])
    mapped_rows: list[dict[str, Any]] = []
    endpoint_counts = Counter()
    for element, from_field, to_field in (
        ("line", "from_bus", "to_bus"),
        ("trafo", "hv_bus", "lv_bus"),
    ):
        table = getattr(net, element)
        for index, row in table.iterrows():
            if not bool(row.get("in_service", True)):
                continue
            source_from = _bus_source_id(net, int(row[from_field]))
            source_to = _bus_source_id(net, int(row[to_field]))
            key = tuple(sorted((source_from, source_to)))
            if not pools[key]:
                raise ValueError(
                    f"IEEE-300 PGLib branch mapping is missing for "
                    f"{element}/{index} endpoints {key}"
                )
            source_row, values = pools[key].pop(0)
            rating_mva = float(values[5])
            if not math.isfinite(rating_mva) or rating_mva <= 0:
                raise ValueError(f"invalid PGLib RATE_A at branch row {source_row}")
            if element == "line":
                voltage_kv = float(net.bus.at[int(row[from_field]), "vn_kv"])
                derating = float(row.get("df", 1.0))
                parallel = float(row.get("parallel", 1.0))
                net.line.at[index, "max_i_ka"] = rating_mva / (
                    math.sqrt(3.0) * voltage_kv * derating * parallel
                )
            else:
                _preserve_trafo_impedance_while_setting_rating(net, int(index), rating_mva)
            mapped_rows.append(
                {
                    "element": element,
                    "index": int(index),
                    "pglib_source_row": source_row,
                    "endpoints": [source_from, source_to],
                    "rate_a_mva": rating_mva,
                    "source_explicit_high_limit": math.isclose(rating_mva, 9900.0),
                }
            )
            endpoint_counts[key] += 1
    leftovers = sum(len(rows) for rows in pools.values())
    if leftovers:
        raise ValueError(f"IEEE-300 has {leftovers} unmapped PGLib branches")
    if len(mapped_rows) != len(case["branch"]):
        raise ValueError(
            f"IEEE-300 branch cardinality mismatch: "
            f"{len(mapped_rows)} mapped versus {len(case['branch'])} source rows"
        )
    return {
        "mapped_branch_count": len(mapped_rows),
        "source_explicit_high_limit_count": sum(
            row["source_explicit_high_limit"] for row in mapped_rows
        ),
        "endpoint_multiset_size": len(endpoint_counts),
        "topology_mapping_bijective": True,
        "rating_rows": mapped_rows,
    }


def _add_deterministic_slack_if_needed(net) -> dict[str, Any] | None:
    if len(net.ext_grid) or (
        len(net.gen)
        and "slack" in net.gen.columns
        and bool(net.gen["slack"].fillna(False).any())
    ):
        return None
    candidates = net.gen.loc[net.gen["in_service"].astype(bool)]
    if candidates.empty:
        raise ValueError("network has no in-service generator available as reference")
    index = int(candidates["p_mw"].abs().idxmax())
    net.gen.at[index, "slack"] = True
    return {
        "policy": "largest_absolute_active_power_in_service_generator",
        "generator_index": index,
        "bus": int(net.gen.at[index, "bus"]),
        "p_mw": float(net.gen.at[index, "p_mw"]),
    }


@lru_cache(maxsize=len(FORMAL_SYSTEMS))
def _load_pglib_network_cached(
    system: str,
    pglib_root_text: str,
    *,
    expected_commit: str = PGLIB_EXPECTED_COMMIT,
):
    """Construct one immutable cache template per pinned formal source case."""
    if system not in FORMAL_SYSTEMS:
        raise ValueError(f"unknown formal network: {system}")
    pglib_root = Path(pglib_root_text)
    repository = validate_pglib_repository(pglib_root, expected_commit)
    case = load_pglib_case(pglib_root, system)
    if system == "ieee300":
        net = pn.case300()
        rating_bridge = _apply_ieee300_pglib_ratings(net, case)
        operating_point = {
            "provider": "pandapower.networks.case300",
            "role": "convergent operating point only",
            "pglib_topology_and_rating_precedence": True,
            "branch_topology_bijective": True,
            **rating_bridge,
        }
    else:
        net = from_mpc(str(case["path"]), validate_conversion=False)
        operating_point = {
            "provider": "PGLib MATPOWER case",
            "role": "topology, limits, and operating point",
        }
    source_bus_ids = [int(row[0]) for row in case["bus"]]
    by_minus_one = {source_id - 1: source_id for source_id in source_bus_ids}
    by_identity = {source_id: source_id for source_id in source_bus_ids}
    current_name_map = {}
    try:
        current_name_map = {
            int(index): int(float(value))
            for index, value in net.bus["name"].items()
        }
    except (TypeError, ValueError):
        current_name_map = {}
    if set(current_name_map.values()) == set(source_bus_ids):
        source_id_by_index = current_name_map
    elif set(net.bus.index) == set(by_minus_one):
        source_id_by_index = by_minus_one
    elif set(net.bus.index) == set(by_identity):
        source_id_by_index = by_identity
    else:
        raise ValueError(
            f"{system} bus indices cannot be bijectively bound to PGLib bus IDs"
        )
    for bus_index, source_id in source_id_by_index.items():
        net.bus.at[bus_index, "name"] = source_id
    if len(set(net.bus["name"])) != len(source_bus_ids):
        raise ValueError(f"{system} source bus identifiers are not unique")
    source_generator_rows: dict[int, list[int]] = defaultdict(list)
    for row_number, row in enumerate(case["gen"], start=1):
        if int(row[7]) != 0:
            source_generator_rows[int(row[0])].append(row_number)
    for table_name in ("ext_grid", "gen"):
        table = getattr(net, table_name)
        if len(table):
            table["pglib_source_row"] = -1
    for source_bus_id, row_numbers in source_generator_rows.items():
        candidates: list[tuple[str, int]] = []
        for table_name in ("ext_grid", "gen"):
            table = getattr(net, table_name)
            for index, row in table.iterrows():
                if not bool(row.get("in_service", True)):
                    continue
                if _bus_source_id(net, int(row["bus"])) == source_bus_id:
                    candidates.append((table_name, int(index)))
        if len(candidates) != len(row_numbers) and system in {"ieee14", "ieee118"}:
            raise ValueError(
                f"{system} generator mapping mismatch at source bus {source_bus_id}: "
                f"{len(candidates)} pandapower rows versus {len(row_numbers)} PGLib rows"
            )
        for (table_name, index), row_number in zip(candidates, row_numbers):
            getattr(net, table_name).at[index, "pglib_source_row"] = row_number
    unmapped_generators = [
        f"{table_name}/{int(index)}"
        for table_name in ("ext_grid", "gen")
        for index, row in getattr(net, table_name).iterrows()
        if bool(row.get("in_service", True)) and int(row["pglib_source_row"]) <= 0
    ]
    if unmapped_generators and system in {"ieee14", "ieee118"}:
        raise ValueError(f"{system} has unmapped generators: {unmapped_generators}")
    normalize_transformer_contract(net)
    slack_policy = _add_deterministic_slack_if_needed(net)
    provenance = {
        "contract_version": "gridinstruct-pglib-network-source-v1",
        "system": system,
        "repository": repository["repository"],
        "repository_commit": repository["commit"],
        "license": repository["license"],
        "license_sha256": repository["license_sha256"],
        "case_file": case["path"].name,
        "case_sha256": case["sha256"],
        "operating_point": operating_point,
        "deterministic_slack_assignment": slack_policy,
    }
    net["pglib_provenance"] = provenance
    return net


def load_pglib_network(
    system: str,
    pglib_root: Path,
    *,
    expected_commit: str = PGLIB_EXPECTED_COMMIT,
):
    """Return an isolated deep copy of a cached, source-verified base case."""
    template = _load_pglib_network_cached(
        system,
        str(pglib_root.resolve()),
        expected_commit=expected_commit,
    )
    return copy.deepcopy(template)


def network_provenance(net) -> dict[str, Any]:
    value = net.get("pglib_provenance")
    if not isinstance(value, dict):
        raise ValueError("network is missing the PGLib provenance contract")
    return value
