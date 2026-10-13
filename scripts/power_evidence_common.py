#!/usr/bin/env python3
"""Shared contracts for power-system evidence audits.

The helpers in this module deliberately keep equipment-rating provenance
separate from solver convergence.  A converged power flow with an unknown or
placeholder branch rating is classified as rating-indeterminate, never safe.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandapower as pp
import pandapower.networks as pn


NETWORK_LOADERS = {
    "ieee14": pn.case14,
    "ieee30": pn.case30,
    "ieee57": pn.case57,
    "ieee118": pn.case118,
    "ieee300": pn.case300,
    "illinois200": pn.case_illinois200,
    "pegase89": pn.case89pegase,
    "pegase1354": pn.case1354pegase,
    "rte1888": pn.case1888rte,
    "rte2848": pn.case2848rte,
    "pegase2869": pn.case2869pegase,
}

CORE_N1_NETWORKS = ("ieee14", "ieee118")
EXTERNAL_NETWORKS = (
    "ieee300",
    "illinois200",
    "pegase89",
    "pegase1354",
    "rte1888",
    "rte2848",
    "pegase2869",
)
ENVELOPE_CLASSES = (
    "normal-envelope",
    "operational-stress",
    "emergency-stress",
    "extreme-stress",
)
PGLIB_EXPECTED_COMMIT = "dc6be4b2f85ca0e776952ec22cbd4c22396ea5a3"
PGLIB_LICENSE = "CC-BY-4.0 for data; MIT for software"
PGLIB_LICENSE_SHA256 = "95b1cd9fee1676221d74f7c0cbba622d98ac098e9b317b3848113ef4356ab4fd"
PGLIB_CASE_FILES = {
    "ieee14": "pglib_opf_case14_ieee.m",
    "ieee30": "pglib_opf_case30_ieee.m",
    "ieee57": "pglib_opf_case57_ieee.m",
    "ieee118": "pglib_opf_case118_ieee.m",
    "ieee300": "pglib_opf_case300_ieee.m",
    "illinois200": "pglib_opf_case200_activ.m",
    "pegase89": "pglib_opf_case89_pegase.m",
    "pegase1354": "pglib_opf_case1354_pegase.m",
    "rte1888": "pglib_opf_case1888_rte.m",
    "rte2848": "pglib_opf_case2848_rte.m",
    "pegase2869": "pglib_opf_case2869_pegase.m",
}
PGLIB_CASE_SHA256 = {
    "ieee14": "bd5c568621de65e4b0922317010868bc7fa94173807faa10ea8fdbbe77c28106",
    "ieee30": "cae3290639d989731d32428aacf30c0b918bc91db73bc54791f3aa62d3f76c70",
    "ieee57": "aa3b48f7cbaade2afd69cb3790ef72be981db8494dcef9277bee460f754bdb22",
    "ieee118": "b1af0833849040c04babc3700631cff0d9afa66b79c5d3e13ae79bdf516cec78",
    "ieee300": "7ecf056d5942135765200ad7ae8791c28f0d35fb1dc888ba2c32dfc950f3c2f5",
    "illinois200": "676e6f54a3b6726b199b531e7758ad8bf75ba2b076ea3fee86dc3f5cf5846f6b",
    "pegase89": "0c2ca484db566e587df8565141dbbf053c275e9246968391cc2f560fb4e995ca",
    "pegase1354": "cd6d27dff4a56684f1e4f82cfa346b36d84c4e90733228aa88331cd550e17652",
    "rte1888": "f4bc237298ed7c2e9291070f3c3a0983319565c12b801114a1e29641d0b5bff0",
    "rte2848": "9d1ec26a6aef3e47bc23707a6c81db8d0528ff6ad90068b2f92df3d6e76ef591",
    "pegase2869": "6c8e80fba6fc2fa78d65fce64cf4801425b01a0aa093661caf581b6551d4a7ac",
}

# MATPOWER uses large finite values to encode an absent thermal rating in many
# bundled cases.  pandapower preserves those sentinels during conversion.
PLACEHOLDER_APPARENT_POWER_MVA = (9900.0,)
PLACEHOLDER_CURRENT_KA_MIN = 99_999.0
PLACEHOLDER_RELATIVE_TOLERANCE = 1e-6


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_transformer_contract(net) -> None:
    """Make the pandapower 3.x transformer-table contract explicit."""
    for element in ("trafo", "trafo3w"):
        table = getattr(net, element, None)
        if table is not None and len(table) and "tap_dependency_table" not in table.columns:
            table["tap_dependency_table"] = False


def load_network(system: str):
    if system not in NETWORK_LOADERS:
        raise ValueError(f"unknown network: {system}")
    net = NETWORK_LOADERS[system]()
    normalize_transformer_contract(net)
    return net


def source_descriptor(system: str) -> dict[str, Any]:
    loader = NETWORK_LOADERS[system]
    return {
        "provider": "pandapower.networks",
        "loader": f"{loader.__module__}.{loader.__name__}",
        "pandapower_version": pp.__version__,
        "provenance_scope": "bundled case constructor and its populated equipment fields",
        "external_nameplate_document_bound": False,
    }


def pglib_repository_commit(root: Path) -> str:
    # The release bundles immutable case files rather than the upstream Git
    # object database.  In that self-contained form, the pinned upstream
    # revision is recorded in a small text receipt next to the cases.
    receipt = root / "UPSTREAM_COMMIT"
    if receipt.exists():
        value = receipt.read_text(encoding="utf-8").strip()
        if value:
            return value
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def validate_pglib_repository(
    root: Path,
    expected_commit: str = PGLIB_EXPECTED_COMMIT,
) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"PGLib root is not a directory: {root}")
    commit = pglib_repository_commit(root)
    if commit != expected_commit:
        raise ValueError(f"PGLib commit mismatch: expected {expected_commit}, found {commit}")
    license_path = root / "LICENSE"
    if not license_path.exists():
        raise ValueError("PGLib LICENSE is missing")
    license_sha = sha256_file(license_path)
    if license_sha != PGLIB_LICENSE_SHA256:
        raise ValueError(
            f"PGLib LICENSE hash mismatch: expected {PGLIB_LICENSE_SHA256}, found {license_sha}"
        )
    missing = [
        filename
        for filename in PGLIB_CASE_FILES.values()
        if not (root / filename).is_file()
    ]
    if missing:
        raise ValueError(f"PGLib case files are missing: {missing}")
    return {
        "repository": "https://github.com/power-grid-lib/pglib-opf",
        "commit": commit,
        "license": PGLIB_LICENSE,
        "license_path": str(license_path),
        "license_sha256": license_sha,
    }


def parse_matpower_matrix(text: str, name: str) -> list[list[float]]:
    match = re.search(
        rf"mpc\.{re.escape(name)}\s*=\s*\[(.*?)\];",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"MATPOWER matrix is missing: mpc.{name}")
    rows = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("%", 1)[0].strip().rstrip(";").strip()
        if not line:
            continue
        rows.append([float(token) for token in line.split()])
    return rows


def load_pglib_case(root: Path, system: str) -> dict[str, Any]:
    if system not in PGLIB_CASE_FILES:
        raise ValueError(f"PGLib mapping is unavailable for {system}")
    path = root / PGLIB_CASE_FILES[system]
    text = path.read_text(encoding="utf-8")
    digest = sha256_file(path)
    if digest != PGLIB_CASE_SHA256[system]:
        raise ValueError(
            f"PGLib case hash mismatch for {system}: "
            f"expected {PGLIB_CASE_SHA256[system]}, found {digest}"
        )
    return {
        "system": system,
        "path": path,
        "sha256": digest,
        "bus": parse_matpower_matrix(text, "bus"),
        "gen": parse_matpower_matrix(text, "gen"),
        "branch": parse_matpower_matrix(text, "branch"),
    }


def pglib_source_descriptor(
    repository: dict[str, Any],
    case: dict[str, Any],
    locator: str,
) -> dict[str, Any]:
    return {
        "document_id": case["path"].name,
        "document_sha256": case["sha256"],
        "locator": locator,
        "repository": repository["repository"],
        "repository_commit": repository["commit"],
        "license": repository["license"],
        "license_sha256": repository["license_sha256"],
    }


def infer_pglib_bus_offset(net, pglib_bus_rows: list[list[float]]) -> int:
    source_names = {
        int(float(value))
        for value in net.bus["name"].tolist()
        if finite_number(value) is not None
    }
    pglib_ids = {int(row[0]) for row in pglib_bus_rows}
    for offset in (0, 1, -1):
        if {value + offset for value in source_names} == pglib_ids:
            return offset
    raise ValueError(
        "pandapower bus names cannot be bijectively mapped to the PGLib case"
    )


def pglib_rating_overrides_for_network(
    net,
    system: str,
    root: Path,
    repository: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map PGLib RATE_A values onto pandapower line and transformer rows."""
    case = load_pglib_case(root, system)
    offset = infer_pglib_bus_offset(net, case["bus"])
    branch_pools: dict[tuple[int, int], list[tuple[int, list[float]]]] = defaultdict(list)
    for row_number, row in enumerate(case["branch"], start=1):
        if int(row[10]) == 0:
            continue
        key = tuple(sorted((int(row[0]), int(row[1]))))
        branch_pools[key].append((row_number, row))
    overrides = []
    for element, from_field, to_field in (
        ("line", "from_bus", "to_bus"),
        ("trafo", "hv_bus", "lv_bus"),
    ):
        table = getattr(net, element)
        for index, row in table.iterrows():
            if not bool(row.get("in_service", True)):
                continue
            from_id = int(float(net.bus.at[int(row[from_field]), "name"])) + offset
            to_id = int(float(net.bus.at[int(row[to_field]), "name"])) + offset
            key = tuple(sorted((from_id, to_id)))
            if not branch_pools[key]:
                raise ValueError(
                    f"PGLib branch mapping missing for {system}/{element}/{index} endpoints {key}"
                )
            row_number, source_row = branch_pools[key].pop(0)
            rating = finite_number(source_row[5])
            if rating is None or rating <= 0:
                continue
            semantics = pglib_rate_a_semantics(rating)
            overrides.append(
                {
                    "network": system,
                    "element": element,
                    "index": int(index),
                    "rating_value": rating,
                    "rating_unit": "MVA",
                    "rating_semantics": semantics,
                    "source": pglib_source_descriptor(
                        repository,
                        case,
                        (
                            f"mpc.branch row {row_number}, endpoints "
                            f"{int(source_row[0])}-{int(source_row[1])}, RATE_A"
                        ),
                    ),
                }
            )
    leftovers = sum(len(rows) for rows in branch_pools.values())
    if leftovers:
        raise ValueError(f"{system} has {leftovers} unmapped in-service PGLib branches")
    return overrides


def line_apparent_rating_mva(net, index: int) -> float | None:
    row = net.line.loc[index]
    current = finite_number(row.get("max_i_ka"))
    df = finite_number(row.get("df"))
    parallel = finite_number(row.get("parallel"))
    bus = int(row["from_bus"])
    voltage = finite_number(net.bus.at[bus, "vn_kv"])
    if (
        current is None
        or df is None
        or parallel is None
        or voltage is None
        or current <= 0
        or df <= 0
        or parallel <= 0
        or voltage <= 0
    ):
        return None
    return math.sqrt(3.0) * voltage * current * df * parallel


def is_placeholder_mva(value: float | None) -> bool:
    if value is None:
        return False
    return any(
        math.isclose(
            value,
            sentinel,
            rel_tol=PLACEHOLDER_RELATIVE_TOLERANCE,
            abs_tol=1e-3,
        )
        for sentinel in PLACEHOLDER_APPARENT_POWER_MVA
    )


def pglib_rate_a_semantics(value: float | None) -> str:
    if value is None or value <= 0:
        return "missing_or_nonpositive"
    if is_placeholder_mva(value):
        return "source_explicit_high_limit"
    return "source_bound_rate_a"


def rating_record(net, system: str, element: str, index: int) -> dict[str, Any]:
    table = getattr(net, element)
    row = table.loc[index]
    common = {
        "network": system,
        "element": element,
        "index": int(index),
        "in_service": bool(row.get("in_service", True)),
        "source": source_descriptor(system),
    }
    reasons: list[str] = []
    if element == "line":
        current = finite_number(row.get("max_i_ka"))
        rating_mva = line_apparent_rating_mva(net, index)
        if current is None or rating_mva is None:
            reasons.append("missing_or_nonfinite_rating")
        elif current >= PLACEHOLDER_CURRENT_KA_MIN * (1.0 - PLACEHOLDER_RELATIVE_TOLERANCE):
            reasons.append("placeholder_current_sentinel")
        elif is_placeholder_mva(rating_mva):
            reasons.append("placeholder_apparent_power_sentinel")
        return {
            **common,
            "rating_field": "max_i_ka",
            "rating_value": current,
            "derived_apparent_rating_mva": rating_mva,
            "rating_status": "unknown" if reasons else "known",
            "reasons": reasons,
        }
    if element == "trafo":
        rating = finite_number(row.get("sn_mva"))
        if rating is None or rating <= 0:
            reasons.append("missing_or_nonpositive_rating")
        elif is_placeholder_mva(rating):
            reasons.append("placeholder_apparent_power_sentinel")
        return {
            **common,
            "rating_field": "sn_mva",
            "rating_value": rating,
            "rating_status": "unknown" if reasons else "known",
            "reasons": reasons,
        }
    if element == "trafo3w":
        values = {
            field: finite_number(row.get(field))
            for field in ("sn_hv_mva", "sn_mv_mva", "sn_lv_mva")
        }
        for field, rating in values.items():
            if rating is None or rating <= 0:
                reasons.append(f"{field}:missing_or_nonpositive_rating")
            elif is_placeholder_mva(rating):
                reasons.append(f"{field}:placeholder_apparent_power_sentinel")
        return {
            **common,
            "rating_field": "sn_hv_mva,sn_mv_mva,sn_lv_mva",
            "rating_value": values,
            "rating_status": "unknown" if reasons else "known",
            "reasons": reasons,
        }
    if element == "gen":
        minimum = finite_number(row.get("min_p_mw"))
        maximum = finite_number(row.get("max_p_mw"))
        minimum_q = finite_number(row.get("min_q_mvar"))
        maximum_q = finite_number(row.get("max_q_mvar"))
        if minimum is None or maximum is None:
            reasons.append("missing_or_nonfinite_active_power_bound")
        elif minimum > maximum:
            reasons.append("invalid_active_power_bound")
        if (
            (minimum_q is None) != (maximum_q is None)
            or (
                minimum_q is not None
                and maximum_q is not None
                and minimum_q > maximum_q
            )
        ):
            reasons.append("invalid_reactive_power_bound")
        return {
            **common,
            "rating_field": "min_p_mw,max_p_mw",
            "rating_value": {
                "min_p_mw": minimum,
                "max_p_mw": maximum,
                "min_q_mvar": minimum_q,
                "max_q_mvar": maximum_q,
            },
            "rating_status": "unknown" if reasons else "known",
            "reasons": reasons,
        }
    raise ValueError(f"unsupported equipment element: {element}")


def load_rating_overrides(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("ratings") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("rating override must contain a ratings list")
    required_source = {"document_id", "document_sha256", "locator"}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each rating override must be an object")
        if not required_source.issubset(row.get("source") or {}):
            raise ValueError(
                "each rating override needs source.document_id, "
                "source.document_sha256, and source.locator"
            )
        digest = str(row["source"]["document_sha256"]).lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("rating source document_sha256 must be a 64-character hex digest")
    return rows


def apply_rating_overrides(net, system: str, overrides: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """Validate source-bound ratings and return overridden equipment keys.

    Line and transformer ratings are intentionally kept outside pandapower's
    electrical parameter tables. In particular, changing ``trafo.sn_mva``
    would also change the transformer impedance base and therefore the solved
    physics. Audits recompute thermal utilization from terminal MVA instead.
    """
    applied: set[tuple[str, int]] = set()
    for row in overrides:
        if str(row.get("network", "")).lower() != system:
            continue
        element = str(row["element"])
        index = int(row["index"])
        table = getattr(net, element)
        if index not in table.index:
            raise ValueError(f"rating override target is absent: {system}/{element}/{index}")
        value = finite_number(row.get("rating_value"))
        if value is None or value <= 0:
            raise ValueError(f"rating override must be finite and positive: {system}/{element}/{index}")
        if element == "gen":
            table.at[index, "max_p_mw"] = value
        elif element not in {"line", "trafo"}:
            raise ValueError("trafo3w overrides require side-specific ratings and are not accepted here")
        applied.add((element, index))
    return applied


def envelope_class(row: dict[str, Any]) -> str:
    if row.get("solver_status") != "converged":
        return "solver-not-converged"
    max_loading = float(row.get("max_branch_loading_percent") or 0.0)
    min_vm = float(row.get("min_bus_voltage_pu") or 1.0)
    max_vm = float(row.get("max_bus_voltage_pu") or 1.0)
    if max_loading <= 100.0 and 0.95 <= min_vm and max_vm <= 1.05:
        return "normal-envelope"
    if max_loading <= 120.0 and 0.90 <= min_vm and max_vm <= 1.10:
        return "operational-stress"
    if max_loading <= 300.0 and 0.80 <= min_vm and max_vm <= 1.20:
        return "emergency-stress"
    return "extreme-stress"


def source_bound_rating_map_from_loaded_network(net) -> dict[tuple[str, int], float]:
    """Recover the source RATE_A-equivalent MVA bound from a loaded PGLib net.

    ``from_mpc`` stores line RATE_A as a current limit and transformer RATE_A
    as ``sn_mva``.  This helper reverses that representation without moving
    between the index spaces of a bundled pandapower case and the pinned
    PGLib-loaded case.
    """

    ratings: dict[tuple[str, int], float] = {}
    for index, row in net.line.iterrows():
        from_bus = int(row["from_bus"])
        voltage_kv = finite_number(net.bus.at[from_bus, "vn_kv"])
        max_i_ka = finite_number(row.get("max_i_ka"))
        derating = finite_number(row.get("df", 1.0))
        parallel = finite_number(row.get("parallel", 1.0))
        if (
            voltage_kv is None
            or max_i_ka is None
            or derating is None
            or parallel is None
        ):
            continue
        rating = math.sqrt(3.0) * voltage_kv * max_i_ka * derating * parallel
        if math.isfinite(rating) and rating > 0:
            ratings[("line", int(index))] = rating
    for index, row in net.trafo.iterrows():
        rating = finite_number(row.get("sn_mva"))
        parallel = finite_number(row.get("parallel", 1.0))
        derating = finite_number(row.get("df", 1.0))
        if rating is None or parallel is None or derating is None:
            continue
        rating *= parallel * derating
        if math.isfinite(rating) and rating > 0:
            ratings[("trafo", int(index))] = rating
    return ratings


def source_bound_loading_from_state_rows(
    line_results: list[dict[str, Any]],
    transformer_results: list[dict[str, Any]],
    rating_map: dict[tuple[str, int], float],
) -> dict[str, Any]:
    """Compute canonical branch loading as terminal apparent MVA / RATE_A."""

    loading_values: dict[str, list[float]] = {"line": [], "trafo": []}
    missing: list[dict[str, Any]] = []
    nonfinite: list[dict[str, Any]] = []
    specifications = (
        (
            "line",
            line_results,
            "line",
            (("p_from_mw", "q_from_mvar"), ("p_to_mw", "q_to_mvar")),
        ),
        (
            "trafo",
            transformer_results,
            "transformer",
            (("p_hv_mw", "q_hv_mvar"), ("p_lv_mw", "q_lv_mvar")),
        ),
    )
    for element, states, index_key, terminals in specifications:
        for state in states:
            if not bool(state.get("in_service")):
                continue
            index = int(state[index_key])
            rating = rating_map.get((element, index))
            if rating is None:
                missing.append({"element": element, "index": index})
                continue
            apparent_values = []
            for active_key, reactive_key in terminals:
                active = finite_number(state.get(active_key))
                reactive = finite_number(state.get(reactive_key))
                if active is None or reactive is None:
                    nonfinite.append(
                        {
                            "element": element,
                            "index": index,
                            "terminal": [active_key, reactive_key],
                        }
                    )
                    continue
                apparent_values.append(math.hypot(active, reactive))
            if len(apparent_values) == len(terminals):
                loading_values[element].append(100.0 * max(apparent_values) / rating)
    max_line = max(loading_values["line"], default=0.0)
    max_trafo = max(loading_values["trafo"], default=0.0)
    return {
        "max_line_loading_percent": max_line,
        "max_transformer_loading_percent": max_trafo,
        "max_branch_loading_percent": max(max_line, max_trafo),
        "missing_source_bound_ratings": missing,
        "nonfinite_source_bound_terminal_states": nonfinite,
        "thermal_rating_status": (
            "source_bound" if not missing and not nonfinite else "rating_indeterminate"
        ),
    }
