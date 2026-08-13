#!/usr/bin/env python3
"""Inventory the pinned PGLib inputs and solver dependencies used by V16."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandapower
import pandapower.networks as pn

from gridinstruct_utils import ROOT, read_json, write_json
from power_evidence_common import (
    PGLIB_CASE_FILES,
    PGLIB_CASE_SHA256,
    PGLIB_EXPECTED_COMMIT,
    PGLIB_LICENSE,
    PGLIB_LICENSE_SHA256,
)

PGLIB_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PGLIB_ARCHIVE_REPORT = {
    "title": "The Power Grid Library for Benchmarking AC Optimal Power Flow Algorithms",
    "identifier": "arXiv:1908.02788",
    "url": "https://arxiv.org/abs/1908.02788",
}
PGLIB_ATTRIBUTION_REQUIREMENTS = [
    "give appropriate credit to the original author",
    "provide a link to the license",
    "indicate whether changes were made",
    "identify the repository version in scholarly references",
    "cite source documents named in each case header and the PGLib archive report",
]
AUDIT_SCOPE_LIMIT = (
    "Only the fixed PGLib-OPF v23.07 commit and eleven registered case hashes "
    "are confirmed. Any source revision or case substitution requires re-audit."
)
HEADER_ATTRIBUTION_RECORDS = {
    "uw_ieee_archive": {
        "copyright": (
            "Copyright (c) 1999 Richard D. Christie, University of Washington "
            "Electrical Engineering"
        ),
        "source": (
            "University of Washington Power Systems Test Case Archive IEEE "
            "Common Data Format"
        ),
        "source_citation_requirement": (
            "Retain the file-header source and copyright attribution."
        ),
        "applies_to": ["ieee14", "ieee30", "ieee57", "ieee118", "ieee300"],
    },
    "activsg200": {
        "copyright": (
            "Copyright (c) 2017 A. B. Birchfield, T. Xu, K. M. Gegner, "
            "K. S. Shetye, and T. J. Overbye"
        ),
        "source": (
            "Synthetic Illinois 200-bus system developed under the US ARPA-E "
            "GRID DATA project"
        ),
        "source_citation_requirement": (
            "Cite Birchfield et al., Grid Structural Characteristics as "
            "Validation Criteria for Synthetic Networks, IEEE Transactions on "
            "Power Systems (2017), DOI 10.1109/TPWRS.2016.2616385."
        ),
        "applies_to": ["illinois200"],
    },
    "pegase": {
        "copyright": (
            "Copyright (c) 2015 Cedric Josz, Stephane Fliscounakis, "
            "Jean Maeght, and Patrick Panciatici"
        ),
        "source": (
            "Pan European Grid Advanced Simulation and State Estimation project"
        ),
        "source_citation_requirement": (
            "Cite Fliscounakis et al., IEEE Transactions on Power Systems "
            "28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015."
        ),
        "applies_to": ["pegase89", "pegase1354", "pegase2869"],
    },
    "rte": {
        "copyright": (
            "Copyright (c) 2016 Cedric Josz, Stephane Fliscounakis, "
            "Jean Maeght, and Patrick Panciatici"
        ),
        "source": "RTE and iTesla French-system snapshots",
        "source_citation_requirement": (
            "Cite Josz et al., AC Power Flow Data in MATPOWER and QCQP Format: "
            "iTesla, RTE Snapshots, and PEGASE, arXiv:1603.01533."
        ),
        "applies_to": ["rte1888", "rte2848"],
    },
}
CASE_ATTRIBUTION_METADATA = {
    "ieee14": {
        "header_attribution_record": "uw_ieee_archive",
        "required_source_citation": (
            "Retain the Richard D. Christie and University of Washington Power "
            "Systems Test Case Archive attribution from the case header."
        ),
        "case_header_change_notice": (
            "Converted from IEEE Common Data Format on 20 September 2004; "
            "GridInstruct further generates scaled and contingency-derived "
            "numerical states."
        ),
    },
    "ieee30": {
        "header_attribution_record": "uw_ieee_archive",
        "required_source_citation": (
            "Retain the Richard D. Christie and University of Washington Power "
            "Systems Test Case Archive attribution from the case header."
        ),
        "case_header_change_notice": (
            "Converted from IEEE Common Data Format on 20 September 2004; "
            "GridInstruct further generates scaled and contingency-derived "
            "numerical states."
        ),
    },
    "ieee57": {
        "header_attribution_record": "uw_ieee_archive",
        "required_source_citation": (
            "Retain the Richard D. Christie and University of Washington Power "
            "Systems Test Case Archive attribution from the case header."
        ),
        "case_header_change_notice": (
            "Converted from IEEE Common Data Format; generator 1 Qmax and Qmin "
            "were manually changed to 200 and -140; GridInstruct further "
            "generates scaled and contingency-derived numerical states."
        ),
    },
    "ieee118": {
        "header_attribution_record": "uw_ieee_archive",
        "required_source_citation": (
            "Retain the Richard D. Christie and University of Washington Power "
            "Systems Test Case Archive attribution from the case header."
        ),
        "case_header_change_notice": (
            "Converted from IEEE Common Data Format; base-kV values were "
            "manually added from the PSAP-format file on 10 March 2006; "
            "GridInstruct further generates scaled and contingency-derived "
            "numerical states."
        ),
    },
    "ieee300": {
        "header_attribution_record": "uw_ieee_archive",
        "required_source_citation": (
            "Retain the Richard D. Christie and University of Washington Power "
            "Systems Test Case Archive attribution from the case header."
        ),
        "case_header_change_notice": (
            "Converted from IEEE Common Data Format on 20 September 2004; "
            "GridInstruct uses the pandapower case300 operating point only "
            "while retaining PGLib topology and RATE_A values, then generates "
            "derived states."
        ),
    },
    "illinois200": {
        "header_attribution_record": "activsg200",
        "required_source_citation": (
            "Cite Birchfield et al., Grid Structural Characteristics as "
            "Validation Criteria for Synthetic Networks, IEEE Transactions on "
            "Power Systems (2017), DOI 10.1109/TPWRS.2016.2616385."
        ),
        "case_header_change_notice": (
            "Converted from ACTIV_SG_200.pwb through PowerWorld Simulator and "
            "MATPOWER 6; GridInstruct further generates scaled and "
            "contingency-derived numerical states."
        ),
        "usage_restriction": (
            "Synthetic system with no CEII; it does not represent the actual grid."
        ),
    },
    "pegase89": {
        "header_attribution_record": "pegase",
        "required_source_citation": (
            "Cite Fliscounakis et al., IEEE Transactions on Power Systems "
            "28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015."
        ),
        "case_header_change_notice": (
            "Line-flow limits are 20 MVA above the original PEGASE current-flow "
            "limits; asymmetric branch shunts are represented nodally; "
            "line-flow constraints and the loss-minimizing objective are "
            "modified; GridInstruct further generates derived states."
        ),
        "usage_restriction": (
            "Validation-only fictitious data; do not use for operation or "
            "planning of the European grid."
        ),
    },
    "pegase1354": {
        "header_attribution_record": "pegase",
        "required_source_citation": (
            "Cite Fliscounakis et al., IEEE Transactions on Power Systems "
            "28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015."
        ),
        "case_header_change_notice": (
            "Line-flow limits are 100 MVA below the original PEGASE "
            "current-flow limits; asymmetric branch shunts are represented "
            "nodally; line-flow constraints and the loss-minimizing objective "
            "are modified; GridInstruct further generates derived states."
        ),
        "usage_restriction": (
            "Validation-only fictitious data; do not use for operation or "
            "planning of the European grid."
        ),
    },
    "rte1888": {
        "header_attribution_record": "rte",
        "required_source_citation": (
            "Cite Josz et al., AC Power Flow Data in MATPOWER and QCQP Format: "
            "iTesla, RTE Snapshots, and PEGASE, arXiv:1603.01533."
        ),
        "case_header_change_notice": (
            "PGLib curates a French-system snapshot sampled in the iTesla "
            "offline platform; GridInstruct further generates derived "
            "numerical states."
        ),
        "usage_restriction": (
            "Use only to validate mathematical methods and tools; do not use "
            "for operation or planning of the French or European grids."
        ),
    },
    "rte2848": {
        "header_attribution_record": "rte",
        "required_source_citation": (
            "Cite Josz et al., AC Power Flow Data in MATPOWER and QCQP Format: "
            "iTesla, RTE Snapshots, and PEGASE, arXiv:1603.01533."
        ),
        "case_header_change_notice": (
            "PGLib curates a French-system snapshot sampled in the iTesla "
            "offline platform; GridInstruct further generates derived "
            "numerical states."
        ),
        "usage_restriction": (
            "Use only to validate mathematical methods and tools; do not use "
            "for operation or planning of the French or European grids."
        ),
    },
    "pegase2869": {
        "header_attribution_record": "pegase",
        "required_source_citation": (
            "Cite Fliscounakis et al., IEEE Transactions on Power Systems "
            "28(4), 4909-4917 (2013), DOI 10.1109/TPWRS.2013.2251015."
        ),
        "case_header_change_notice": (
            "Line-flow limits retain the original PEGASE current-flow limits; "
            "asymmetric branch shunts are represented nodally; line-flow "
            "constraints and the loss-minimizing objective are modified; "
            "GridInstruct further generates derived states."
        ),
        "usage_restriction": (
            "Validation-only fictitious data; do not use for operation or "
            "planning of the European grid."
        ),
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_inventory(
    *,
    pglib_root: Path,
    registry: dict[str, Any],
    ieee300_bridge: dict[str, Any] | None,
) -> dict[str, Any]:
    errors: list[str] = []
    license_path = pglib_root / "LICENSE"
    if not license_path.is_file() or sha256(license_path) != PGLIB_LICENSE_SHA256:
        errors.append("pglib_license_hash_mismatch")
    if registry.get("commit") != PGLIB_EXPECTED_COMMIT:
        errors.append("pglib_registry_commit_mismatch")
    if registry.get("license") != PGLIB_LICENSE:
        errors.append("pglib_registry_license_mismatch")
    if registry.get("license_sha256") != PGLIB_LICENSE_SHA256:
        errors.append("pglib_registry_license_hash_mismatch")

    registered_cases = registry.get("cases") or {}
    cases = []
    for system, filename in PGLIB_CASE_FILES.items():
        path = pglib_root / filename
        expected_hash = PGLIB_CASE_SHA256[system]
        registered = registered_cases.get(system) or {}
        actual_hash = sha256(path) if path.is_file() else None
        if registered.get("file") != filename:
            errors.append(f"pglib_registry_filename_mismatch:{system}")
        if registered.get("sha256") != expected_hash:
            errors.append(f"pglib_registry_case_hash_mismatch:{system}")
        if actual_hash != expected_hash:
            errors.append(f"pglib_case_hash_mismatch:{system}")
        attribution = CASE_ATTRIBUTION_METADATA.get(system) or {}
        attribution_record = attribution.get("header_attribution_record")
        header_attribution = HEADER_ATTRIBUTION_RECORDS.get(str(attribution_record))
        if not header_attribution:
            errors.append(f"pglib_case_attribution_missing:{system}")
        if not attribution.get("required_source_citation"):
            errors.append(f"pglib_case_source_citation_missing:{system}")
        if not attribution.get("case_header_change_notice"):
            errors.append(f"pglib_case_change_notice_missing:{system}")
        row = {
            "constructor": f"pglib-opf/{system}",
            "system": system,
            "repository": registry.get("repository"),
            "release": registry.get("release"),
            "commit": registry.get("commit"),
            "case_file": filename,
            "case_file_sha256": actual_hash,
            "registered_case_sha256": registered.get("sha256"),
            "license": PGLIB_LICENSE,
            "license_sha256": PGLIB_LICENSE_SHA256,
            **attribution,
            "header_attribution": (
                header_attribution.get("copyright") if header_attribution else None
            ),
            "raw_case_file_in_release_archive": False,
            "derived_synthetic_states_in_release_archive": True,
            "derived_state_redistribution_status": "confirmed",
            "required_attribution_status": "confirmed",
            "confirmation_basis": (
                "Pinned LICENSE, README attribution conditions, individual case "
                "header, exact SHA-256, required source citation, and explicit "
                "change notice."
            ),
        }
        if system == "ieee300":
            row["operating_point_bridge"] = ieee300_bridge
            if not ieee300_bridge or not ieee300_bridge.get(
                "installed_case_file_sha256"
            ):
                errors.append("ieee300_operating_point_bridge_missing")
        cases.append(row)

    dependency_specs = [
        (
            "pandapower",
            "MATPOWER conversion, AC power flow, AC OPF, and IEEE300 convergent operating-point bridge",
            "BSD-3-Clause",
            "https://pandapower.readthedocs.io/en/stable/about/license.html",
        ),
        (
            "lightsim2grid",
            "native C++ independent fixed-control AC replay",
            "MPL-2.0",
            "https://github.com/Grid2Op/lightsim2grid/blob/master/LICENSE",
        ),
        (
            "power-grid-model",
            "installed alternative independent power-system model; not used for the formal replay metric",
            "MPL-2.0",
            "https://github.com/PowerGridModel/power-grid-model/blob/main/LICENSE",
        ),
    ]
    dependencies = []
    for name, role, license_name, license_url in dependency_specs:
        version = package_version(name)
        if version is None:
            errors.append(f"missing_formal_dependency:{name}")
        dependencies.append(
            {
                "name": name,
                "version": version,
                "role": role,
                "license": license_name,
                "official_license_url": license_url,
                "package_source_redistributed": False,
            }
        )

    external_confirmations: list[dict[str, Any]] = []
    return {
        "schema_version": "2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "local_inventory_complete": not errors,
        "audit_scope": {
            "method": (
                "Hash verification of the pinned checkout plus LICENSE, README, "
                "and individual case-header review"
            ),
            "review_report": (
                "review-stage/" "SD_V16_PGLIB_LICENSE_ATTRIBUTION_AUDIT_20260724.md"
            ),
            "scope_limit": AUDIT_SCOPE_LIMIT,
        },
        "formal_network_source": {
            "provider": "PGLib-OPF",
            "repository": registry.get("repository"),
            "release": registry.get("release"),
            "commit": registry.get("commit"),
            "license": registry.get("license"),
            "license_sha256": registry.get("license_sha256"),
            "case_count": len(cases),
        },
        "licensing_and_attribution_contract": {
            "data_license": "CC-BY-4.0",
            "license_url": PGLIB_LICENSE_URL,
            "requirements_verified_from_upstream_readme": (
                PGLIB_ATTRIBUTION_REQUIREMENTS
            ),
            "pglib_archive_report": PGLIB_ARCHIVE_REPORT,
            "header_attribution_records": HEADER_ATTRIBUTION_RECORDS,
        },
        "software_dependencies": dependencies,
        "network_case_inputs": cases,
        "regulatory_sources": {
            "release_content": "identifiers, short paraphrased rule summaries, clause locators, and URLs; no long source text",
            "source_documents_redistributed": False,
        },
        "pretrained_model_inputs": {
            "release_content": "model names, immutable local snapshot hashes, run specifications, metrics, and predictions",
            "model_weights_redistributed": False,
        },
        "release_scope": {
            "project_license_files": ["LICENSE-CODE", "LICENSE-DATA"],
            "pglib_license_file_in_archive": True,
            "pglib_license_file_required_by_current_archive_builder": True,
            "raw_pglib_case_files_in_archive": False,
            "installed_dependency_source_in_archive": False,
            "derived_scenario_and_instruction_records_in_archive": True,
            "archive_rebuild_required": True,
        },
        "blocking_external_confirmations": external_confirmations,
        "remaining_external_submission_inputs": [
            "final author identities and contribution declarations",
            "public repository URL",
            "data DOI",
            "archived code-release DOI or equivalent permanent identifier",
            (
                "final release archive rebuild so the pinned PGLib LICENSE is "
                "physically present"
            ),
        ],
        "validation_errors": errors,
    }


def render_third_party_assets(inventory: dict[str, Any]) -> str:
    source = inventory["formal_network_source"]
    scope = inventory["release_scope"]
    lines = [
        "# Third-Party Asset Inventory",
        "",
        f"Status: `{inventory['status']}`",
        "",
        (
            "The V16 formal network source is the hash-pinned PGLib-OPF "
            f"`{source['release']}` checkout at commit `{source['commit']}`. "
            "Raw `.m` case files are excluded from the GridInstruct release "
            "archive. Derived states retain case identity, attribution, required "
            "source citations, and change notices."
        ),
        "",
        f"Scope limit: {inventory['audit_scope']['scope_limit']}",
        "",
        (
            "| System | PGLib case SHA-256 | Attribution | Required source "
            "citation | Change or use notice | Redistribution |"
        ),
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in inventory["network_case_inputs"]:
        notice_parts = [row["case_header_change_notice"]]
        if row.get("usage_restriction"):
            notice_parts.append(row["usage_restriction"])
        notice = " ".join(notice_parts)
        lines.append(
            f"| `{row['system']}` | `{row['case_file_sha256']}` | "
            f"{row['header_attribution']} | {row['required_source_citation']} | "
            f"{notice} | `{row['derived_state_redistribution_status']}` |"
        )
    requirements = inventory["licensing_and_attribution_contract"][
        "requirements_verified_from_upstream_readme"
    ]
    report = inventory["licensing_and_attribution_contract"]["pglib_archive_report"]
    lines.extend(
        [
            "",
            "## Upstream attribution contract",
            "",
            (
                "The upstream case data license is CC BY 4.0. Every derived "
                "record must:"
            ),
            "",
            *[f"- {requirement}." for requirement in requirements],
            (
                f"- Cite the PGLib archive report, “{report['title']},” "
                f"{report['identifier']}."
            ),
            "",
            "## Release boundary",
            "",
            (
                "- PGLib `LICENSE` included: "
                f"`{str(scope['pglib_license_file_in_archive']).lower()}`."
            ),
            (
                "- Raw PGLib case files included: "
                f"`{str(scope['raw_pglib_case_files_in_archive']).lower()}`."
            ),
            (
                "- Derived scenario and instruction records included: "
                f"`{str(scope['derived_scenario_and_instruction_records_in_archive']).lower()}`."
            ),
            (
                "- Final archive rebuild required after this inventory update: "
                f"`{str(scope['archive_rebuild_required']).lower()}`."
            ),
            "",
            (
                "See `docs/LICENSES_AND_CITATION.md` and "
                "`review-stage/SD_V16_PGLIB_LICENSE_ATTRIBUTION_AUDIT_20260724.md` "
                "for the audit decision and reusable citation text."
            ),
        ]
    )
    if inventory["validation_errors"]:
        lines.extend(
            [
                "",
                "## Validation errors",
                "",
                *[f"- `{error}`" for error in inventory["validation_errors"]],
            ]
        )
    return "\n".join(lines) + "\n"


def ieee300_bridge_inventory() -> dict[str, Any]:
    function = pn.case300
    source_file = Path(inspect.getsourcefile(function) or "")
    network_root = Path(pandapower.__file__).resolve().parent / "networks"
    case_file = next(iter(network_root.rglob("case300.json")), None)
    return {
        "provider": "pandapower.networks.case300",
        "role": "convergent operating point only; PGLib topology and RATE_A take precedence",
        "installed_source_module_package_relative": (
            str(source_file.relative_to(Path(pandapower.__file__).resolve().parent))
            if source_file.is_file()
            else None
        ),
        "installed_source_module_sha256": sha256(source_file)
        if source_file.is_file()
        else None,
        "installed_case_file_package_relative": (
            str(case_file.relative_to(Path(pandapower.__file__).resolve().parent))
            if case_file
            else None
        ),
        "installed_case_file_sha256": sha256(case_file) if case_file else None,
        "raw_case_file_in_release_archive": False,
    }


def main() -> None:
    pglib_root = ROOT / "third_party/pglib-opf-v23.07"
    registry_path = ROOT / "metadata/pglib_opf_v23_07_case_registry.json"
    registry = read_json(registry_path) if registry_path.is_file() else {}
    inventory = build_inventory(
        pglib_root=pglib_root,
        registry=registry,
        ieee300_bridge=ieee300_bridge_inventory(),
    )
    write_json(ROOT / "metadata/third_party_asset_inventory.json", inventory)
    (ROOT / "docs/THIRD_PARTY_ASSETS.md").write_text(
        render_third_party_assets(inventory),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": inventory["status"],
                "case_count": len(inventory["network_case_inputs"]),
                "errors": inventory["validation_errors"],
            },
            ensure_ascii=False,
        )
    )
    if inventory["validation_errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
