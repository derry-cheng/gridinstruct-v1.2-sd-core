#!/usr/bin/env python3
"""Build and audit a label-sealed compositional shortcut-control benchmark.

The benchmark targets the two C3 tasks whose released lexical baselines are
near perfect.  Labels are computed from typed relations before any language is
rendered.  Complete wording families are then assigned to one split, so split
membership never consults a label or model score.  All cross-split character
similarities are evaluated exhaustively; no MinHash or sampled candidate index
is used.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.svm import LinearSVC

from gridinstruct_utils import ROOT, ensure_dirs, write_json, write_jsonl


TASK_TICKET = "operation_ticket_check"
TASK_ROUTE = "dispatcher_intent_tool_call"
LABELS = {
    TASK_TICKET: ("compliant", "compliant_with_monitoring", "non_compliant"),
    TASK_ROUTE: ("diagnose_and_dispatch", "mitigate_violations", "security_check_and_redispatch"),
}
TRAIN_FAMILIES = frozenset(range(8))
VALIDATION_FAMILIES = frozenset({8, 9})
TEST_FAMILIES = frozenset({10, 11})

CONTEXT = (
    "day-ahead preparation",
    "shift handover",
    "control-room review",
    "outage coordination",
    "maintenance release",
    "real-time supervision",
    "restoration planning",
    "operator training",
    "dispatch audit",
    "pre-switch briefing",
    "field coordination",
    "post-event verification",
)
CHANNEL = (
    "voice transcription",
    "console request",
    "signed work order",
    "dispatcher worksheet",
    "shift log",
    "control-center message",
    "supervisor note",
    "operations terminal",
    "crew handoff",
    "planning record",
    "event dossier",
    "review queue",
)
SCOPE = (
    "single-bay action",
    "line switching",
    "transformer transfer",
    "bus reconfiguration",
    "generator support",
    "voltage correction",
    "thermal relief",
    "contingency follow-up",
    "permit reconciliation",
    "protection coordination",
    "security screening",
    "state diagnosis",
)
PHASE = (
    "before field release",
    "before verbal repeat-back",
    "before control execution",
    "during shift approval",
    "during crew alignment",
    "during dispatch confirmation",
    "at the pre-action gate",
    "at the supervisory gate",
    "for the signed review",
    "for the operating record",
    "for the control-room check",
    "for the final authorization",
)


TICKET_TEMPLATES = (
    "During {context}, assess a {scope} from the {channel}. The planned start is {start}; the permit record shows {permit}. The ticket names {ticket_code}, the field state names {state_code}, and required/assigned observation is {required}/{assigned} min {phase}.",
    "The {channel} concerns a {scope} in {context}. Record these facts {phase}: start {start}, authorization {permit}, ticket object {ticket_code}, state object {state_code}, minimum watch {required} min, scheduled watch {assigned} min.",
    "For {context}, review the {scope} received by {channel}. Use start={start} and permit={permit}; compare object entries {ticket_code} and {state_code}; compare observation durations {required} and {assigned} minutes {phase}.",
    "A {scope} is queued through {channel} for {context}. {phase}, read the execution time {start}, permit time {permit}, ticket/state objects {ticket_code}/{state_code}, and watch requirement/allocation {required}/{assigned} min.",
    "In {context}, the {channel} submits a {scope}. The execution register gives {start}; the permit register gives {permit}. Object fields are {ticket_code} and {state_code}; observation fields are {required} and {assigned} min {phase}.",
    "Review this {scope} for {context} as entered in the {channel}. {phase}, the four comparisons use {start} versus {permit}, {ticket_code} versus {state_code}, and {required} versus {assigned} minutes.",
    "The {context} dossier carries a {scope} from {channel}. Its planned/authorized times are {start}/{permit}, its ticket/state objects are {ticket_code}/{state_code}, and its required/assigned watch periods are {required}/{assigned} min {phase}.",
    "Process a {scope} in the {context} queue supplied by {channel}. Evidence {phase}: planned time {start}; permit time {permit}; declared object {ticket_code}; observed object {state_code}; watch minimum {required} min; watch allocation {assigned} min.",
    "For the {scope} under {context}, the {channel} records permit {permit} and action {start}. It also records state object {state_code}, ticket object {ticket_code}, assigned observation {assigned} min, and required observation {required} min {phase}.",
    "A {channel} entry in {context} requests review of a {scope}. Inspect {phase}: object pair {state_code}/{ticket_code}, timing pair {permit}/{start}, and observation pair {assigned}/{required} minutes.",
    "At {phase}, examine the {scope} listed by {channel} for {context}. The evidence tuple is action time {start}, authority time {permit}, listed device {ticket_code}, state device {state_code}, watch need {required} min, watch plan {assigned} min.",
    "The {context} record for a {scope} arrived through {channel}. Complete the gate {phase} using authority/action times {permit}/{start}, state/ticket devices {state_code}/{ticket_code}, and planned/required observation {assigned}/{required} minutes.",
)

ROUTE_TEMPLATES = (
    "For {context}, route a {scope} request from {channel}. Thermal state/limit is {thermal}/{thermal_limit}%, voltage state/lower bound is {voltage}/{voltage_limit} p.u., and contingency exposure/reserve is {risk}/{reserve} {phase}.",
    "The {channel} submits a {scope} during {context}. Use {phase}: loading {thermal}% against {thermal_limit}%, voltage {voltage} p.u. against {voltage_limit} p.u., exposure {risk} against reserve {reserve}.",
    "In {context}, select the tool family for a {scope} received via {channel}. Compare thermal {thermal} with {thermal_limit}, voltage {voltage} with floor {voltage_limit}, and contingency index {risk} with available reserve {reserve} {phase}.",
    "A {scope} is awaiting routing in {context} through {channel}. The typed measurements {phase} are thermal={thermal}/{thermal_limit}%, voltage={voltage}/{voltage_limit} p.u., contingency={risk}/{reserve}.",
    "Route the {channel} request for {scope} in {context}. {phase}, the operating pairs are loading-limit {thermal}-{thermal_limit}, voltage-floor {voltage}-{voltage_limit}, and exposure-reserve {risk}-{reserve}.",
    "The {context} queue contains a {scope} from {channel}. Determine its route using loading {thermal}% and ceiling {thermal_limit}%, bus voltage {voltage} and floor {voltage_limit}, plus exposure {risk} and reserve {reserve} {phase}.",
    "For a {scope} under {context}, process the {channel} entry {phase}. It reports {thermal}%/{thermal_limit}% thermal, {voltage}/{voltage_limit} p.u. voltage, and {risk}/{reserve} contingency-to-reserve values.",
    "The {channel} record asks for {scope} routing during {context}. Read {phase}: thermal pair {thermal},{thermal_limit}; voltage pair {voltage},{voltage_limit}; security pair {risk},{reserve}.",
    "During {context}, a {scope} reaches the dispatcher by {channel}. The route gate {phase} compares reserve {reserve} with exposure {risk}, thermal ceiling {thermal_limit}% with state {thermal}%, and voltage floor {voltage_limit} with state {voltage} p.u.",
    "A {channel} item for {scope} is open in {context}. {phase}, evaluate exposure/reserve {risk}/{reserve}, voltage/floor {voltage}/{voltage_limit}, and loading/limit {thermal}/{thermal_limit}%.",
    "At {phase}, route the {scope} logged through {channel} for {context}. Evidence gives reserve {reserve}, contingency exposure {risk}, lower-voltage boundary {voltage_limit}, measured voltage {voltage}, thermal boundary {thermal_limit}, and measured loading {thermal}.",
    "The {context} routing record concerns {scope} and originates in {channel}. Apply the gate {phase} to the ordered values reserve-exposure {reserve}-{risk}, floor-voltage {voltage_limit}-{voltage}, and limit-loading {thermal_limit}-{thermal}.",
)


def split_for_family(family: int) -> str:
    if family in TRAIN_FAMILIES:
        return "train"
    if family in VALIDATION_FAMILIES:
        return "validation"
    if family in TEST_FAMILIES:
        return "test"
    raise ValueError(f"unknown family: {family}")


def clock(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


@lru_cache(maxsize=32)
def context_permutation(family: int, task_offset: int) -> tuple[int, ...]:
    population = list(range(len(CONTEXT) * len(CHANNEL) * len(SCOPE) * len(PHASE)))
    random.Random(20260901 + 101 * family + task_offset).shuffle(population)
    return tuple(population)


def lexical_context(family: int, within_family: int, label_index: int, task_offset: int) -> dict[str, str]:
    # A fixed permutation assigns disjoint, label-neutral operational contexts
    # round-robin to the three classes.  This removes the class-specific Latin
    # residue that a lexical model could otherwise exploit.
    position = 3 * within_family + label_index
    code = context_permutation(family, task_offset)[position]
    context_index = code % len(CONTEXT)
    code //= len(CONTEXT)
    channel_index = code % len(CHANNEL)
    code //= len(CHANNEL)
    scope_index = code % len(SCOPE)
    code //= len(SCOPE)
    phase_index = code % len(PHASE)
    return {
        "context": CONTEXT[context_index],
        "channel": CHANNEL[channel_index],
        "scope": SCOPE[scope_index],
        "phase": PHASE[phase_index],
    }


def ticket_label(fields: dict[str, Any]) -> str:
    permit_lead = int(fields["start_minute"]) - int(fields["permit_minute"])
    object_match = fields["ticket_object"] == fields["state_object"]
    watch_margin = int(fields["assigned_watch_min"]) - int(fields["required_watch_min"])
    if permit_lead < 0 or not object_match or watch_margin < 0:
        return "non_compliant"
    if watch_margin < 15:
        return "compliant_with_monitoring"
    return "compliant"


def route_label(fields: dict[str, Any]) -> str:
    if float(fields["contingency_exposure"]) > float(fields["redispatch_reserve"]):
        return "security_check_and_redispatch"
    if (
        float(fields["thermal_loading_pct"]) > float(fields["thermal_limit_pct"])
        or float(fields["voltage_pu"]) < float(fields["voltage_lower_bound_pu"])
    ):
        return "mitigate_violations"
    return "diagnose_and_dispatch"


def render_ticket(fields: dict[str, Any], family: int, context: dict[str, str]) -> str:
    return TICKET_TEMPLATES[family].format(
        **context,
        start=clock(int(fields["start_minute"])),
        permit=clock(int(fields["permit_minute"])),
        ticket_code=fields["ticket_object"],
        state_code=fields["state_object"],
        required=fields["required_watch_min"],
        assigned=fields["assigned_watch_min"],
    )


def render_route(fields: dict[str, Any], family: int, context: dict[str, str]) -> str:
    return ROUTE_TEMPLATES[family].format(
        **context,
        thermal=f"{fields['thermal_loading_pct']:.1f}",
        thermal_limit=f"{fields['thermal_limit_pct']:.1f}",
        voltage=f"{fields['voltage_pu']:.3f}",
        voltage_limit=f"{fields['voltage_lower_bound_pu']:.3f}",
        risk=f"{fields['contingency_exposure']:.1f}",
        reserve=f"{fields['redispatch_reserve']:.1f}",
    )


def make_ticket(label: str, label_index: int, i: int) -> dict[str, Any]:
    family = i % 12
    start = 360 + ((i * 37 + label_index * 113) % 960)
    required = 12 + ((i * 11 + label_index * 17) % 49)
    base_code = f"EQ-{(i * 97 + label_index * 131) % 10007:04d}"
    if label == "non_compliant":
        mode = i % 3
        permit_lead = -5 - (i * 7 % 36) if mode == 0 else 5 + (i * 13 % 46)
        watch_margin = -5 - (i * 5 % 21) if mode == 2 else i * 3 % 46
        state_code = f"EQ-{(i * 193 + 17) % 10007:04d}" if mode == 1 else base_code
    elif label == "compliant_with_monitoring":
        permit_lead = 3 + (i * 7 % 43)
        watch_margin = i * 5 % 15
        state_code = base_code
    else:
        permit_lead = 3 + (i * 11 % 43)
        watch_margin = 15 + (i * 5 % 31)
        state_code = base_code
    fields = {
        "start_minute": start,
        "permit_minute": start - permit_lead,
        "ticket_object": base_code,
        "state_object": state_code,
        "required_watch_min": required,
        "assigned_watch_min": required + watch_margin,
    }
    derived = ticket_label(fields)
    if derived != label:
        raise AssertionError((label, derived, fields))
    instruction = render_ticket(fields, family, lexical_context(family, i // 12, label_index, 0))
    return {
        "id": f"c3-ticket-{label_index}-{i:05d}",
        "task_type": TASK_TICKET,
        "template_family": family,
        "split": split_for_family(family),
        "instruction": instruction,
        "observations": fields,
        "label": label,
        "label_contract": "ticket_temporal_identity_watch_v1",
    }


def make_route(label: str, label_index: int, i: int) -> dict[str, Any]:
    family = i % 12
    thermal_limit = 92.0 + ((i * 7 + label_index * 11) % 17)
    voltage_limit = 0.930 + ((i * 3 + label_index * 5) % 31) / 1000.0
    reserve = 35.0 + ((i * 13 + label_index * 19) % 91)
    if label == "security_check_and_redispatch":
        exposure = reserve + 1.0 + (i * 7 % 35)
        thermal_delta = (-12.0 + (i * 5 % 29))
        voltage_delta = (-0.020 + (i * 3 % 41) / 1000.0)
    elif label == "mitigate_violations":
        exposure = reserve - (1.0 + i * 7 % 35)
        if i % 2:
            thermal_delta = 1.0 + (i * 5 % 18)
            voltage_delta = (i * 3 % 21) / 1000.0
        else:
            thermal_delta = -(i * 5 % 18)
            voltage_delta = -(0.001 + (i * 3 % 20) / 1000.0)
    else:
        exposure = reserve - (1.0 + i * 11 % 35)
        thermal_delta = -(1.0 + i * 5 % 18)
        voltage_delta = (0.001 + (i * 3 % 20) / 1000.0)
    fields = {
        "thermal_loading_pct": round(thermal_limit + thermal_delta, 1),
        "thermal_limit_pct": round(thermal_limit, 1),
        "voltage_pu": round(voltage_limit + voltage_delta, 3),
        "voltage_lower_bound_pu": round(voltage_limit, 3),
        "contingency_exposure": round(exposure, 1),
        "redispatch_reserve": round(reserve, 1),
    }
    derived = route_label(fields)
    if derived != label:
        raise AssertionError((label, derived, fields))
    instruction = render_route(fields, family, lexical_context(family, i // 12, label_index, 10000))
    return {
        "id": f"c3-route-{label_index}-{i:05d}",
        "task_type": TASK_ROUTE,
        "template_family": family,
        "split": split_for_family(family),
        "instruction": instruction,
        "observations": fields,
        "label": label,
        "label_contract": "route_operating_margin_priority_v1",
    }


def build_rows(per_label: int) -> list[dict[str, Any]]:
    if per_label % 12:
        raise ValueError("per-label count must be divisible by 12")
    rows: list[dict[str, Any]] = []
    for task, labels in LABELS.items():
        for label_index, label in enumerate(labels):
            for i in range(per_label):
                maker = make_ticket if task == TASK_TICKET else make_route
                rows.append(maker(label, label_index, i))
    return sorted(rows, key=lambda row: str(row["id"]))


def model_text(row: dict[str, Any]) -> str:
    return str(row["instruction"])


def fit_tfidf(
    train: list[dict[str, Any]],
    test: list[dict[str, Any]],
    *,
    mask_values: bool,
) -> dict[str, Any]:
    transform = normalized_surface if mask_values else (lambda value: value)
    vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(3, 5), min_df=2, sublinear_tf=True)
    x_train = vectorizer.fit_transform([transform(model_text(row)) for row in train])
    x_test = vectorizer.transform([transform(model_text(row)) for row in test])
    model = LinearSVC(C=1.0, class_weight="balanced", random_state=20260901, dual="auto")
    model.fit(x_train, [row["label"] for row in train])
    prediction = model.predict(x_test)
    gold = [row["label"] for row in test]
    return {
        "model": "character_tfidf_3_5_linear_svc",
        "input_profile": "value_masked_surface" if mask_values else "full_instruction",
        "n_train": len(train),
        "n_test": len(test),
        "vocabulary_size": len(vectorizer.vocabulary_),
        "accuracy": float(accuracy_score(gold, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(gold, prediction)),
        "macro_f1": float(f1_score(gold, prediction, average="macro", zero_division=0)),
        "gold_counts": dict(sorted(Counter(gold).items())),
        "prediction_counts": dict(sorted(Counter(map(str, prediction)).items())),
    }


def contract_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    predicted = []
    gold = []
    for row in rows:
        solver = ticket_label if row["task_type"] == TASK_TICKET else route_label
        predicted.append(solver(row["observations"]))
        gold.append(row["label"])
    return {
        "model": "typed_relation_contract_evaluator",
        "n": len(rows),
        "accuracy": float(accuracy_score(gold, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(gold, predicted)),
        "macro_f1": float(f1_score(gold, predicted, average="macro", zero_division=0)),
    }


MASK_PATTERN = re.compile(r"\b(?:eq-)?[a-z]*\d[\w.-]*\b|\d+(?:\.\d+)?", re.IGNORECASE)


def normalized_surface(text: str) -> str:
    value = MASK_PATTERN.sub("{value}", text.lower())
    value = re.sub(r"[^a-z{}]+", " ", value)
    return " ".join(value.split())


def exhaustive_similarity(left: list[str], right: list[str], chunk: int = 256) -> dict[str, Any]:
    vectorizer = CountVectorizer(analyzer="char", ngram_range=(5, 5), binary=True, lowercase=True)
    matrix = vectorizer.fit_transform(left + right).astype(np.float64)
    a = matrix[: len(left)].tocsr()
    b = matrix[len(left) :].tocsr()
    a_size = np.asarray(a.sum(axis=1)).ravel()
    b_size = np.asarray(b.sum(axis=1)).ravel()
    max_jaccard = 0.0
    max_cosine = 0.0
    argmax_jaccard = (-1, -1)
    argmax_cosine = (-1, -1)
    for start in range(0, b.shape[0], chunk):
        stop = min(start + chunk, b.shape[0])
        intersections = (b[start:stop] @ a.T).tocoo()
        for local_i, j, intersection in zip(intersections.row, intersections.col, intersections.data, strict=True):
            i = start + int(local_i)
            union = b_size[i] + a_size[j] - intersection
            jaccard = float(intersection / max(union, 1.0))
            cosine = float(intersection / max(math.sqrt(b_size[i] * a_size[j]), 1.0))
            if jaccard > max_jaccard:
                max_jaccard, argmax_jaccard = jaccard, (int(j), i)
            if cosine > max_cosine:
                max_cosine, argmax_cosine = cosine, (int(j), i)
    return {
        "comparison": "full_exhaustive_binary_character_5gram",
        "pair_count": len(left) * len(right),
        "max_jaccard": max_jaccard,
        "max_cosine": max_cosine,
        "max_jaccard_pair_index": list(argmax_jaccard),
        "max_cosine_pair_index": list(argmax_cosine),
    }


def counts(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(rows)
    return {
        "records": len(values),
        "task_counts": dict(sorted(Counter(str(row["task_type"]) for row in values).items())),
        "label_counts": {
            task: dict(sorted(Counter(str(row["label"]) for row in values if row["task_type"] == task).items()))
            for task in LABELS
        },
        "template_families": sorted({int(row["template_family"]) for row in values}),
    }


def plot_report(task_reports: dict[str, Any], path: Path) -> None:
    ensure_dirs(path.parent)
    tasks = [TASK_TICKET, TASK_ROUTE]
    labels = ["Ticket review", "Tool routing"]
    full = [task_reports[task]["tfidf_test"]["macro_f1"] for task in tasks]
    masked = [task_reports[task]["value_masked_tfidf_test"]["macro_f1"] for task in tasks]
    contract = [task_reports[task]["contract_test"]["macro_f1"] for task in tasks]
    jaccard = [task_reports[task]["train_test_surface_similarity"]["max_jaccard"] for task in tasks]
    cosine = [task_reports[task]["train_test_surface_similarity"]["max_cosine"] for task in tasks]

    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.7), gridspec_kw={"width_ratios": [1.35, 1.0]})
    x = np.arange(len(tasks))
    width = 0.24
    colors = ["#4472C4", "#70AD47", "#ED7D31"]
    for offset, values, name, color in zip(
        (-width, 0.0, width),
        (full, masked, contract),
        ("Full instruction TF–IDF", "Value-masked TF–IDF", "Typed contract"),
        colors,
        strict=True,
    ):
        bars = axes[0].bar(x + offset, values, width=width, label=name, color=color, edgecolor="white")
        axes[0].bar_label(bars, fmt="%.3f", padding=2, fontsize=8)
    axes[0].set_xticks(x, labels)
    axes[0].set_ylim(0, 1.12)
    axes[0].set_ylabel("Macro-F1")
    axes[0].set_title("(a) Relation use versus lexical predictability")
    axes[0].grid(axis="y", color="#D9D9D9", linewidth=0.7)
    axes[0].legend(frameon=False, fontsize=8, loc="upper left")

    width2 = 0.34
    bars_j = axes[1].bar(x - width2 / 2, jaccard, width2, label="Max 5-gram Jaccard", color="#5B9BD5")
    bars_c = axes[1].bar(x + width2 / 2, cosine, width2, label="Max 5-gram cosine", color="#A5A5A5")
    axes[1].bar_label(bars_j, fmt="%.3f", padding=2, fontsize=8)
    axes[1].bar_label(bars_c, fmt="%.3f", padding=2, fontsize=8)
    axes[1].set_xticks(x, labels)
    axes[1].set_ylim(0, 0.70)
    axes[1].set_ylabel("Maximum train–test similarity")
    axes[1].set_title("(b) Exhaustive surface isolation")
    axes[1].grid(axis="y", color="#D9D9D9", linewidth=0.7)
    axes[1].legend(frameon=False, fontsize=8, loc="upper left")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(w_pad=2.0)
    fig.savefig(path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-label", type=int, default=480)
    parser.add_argument("--data-dir", default="data/c3_compositional_v1")
    parser.add_argument("--benchmark-dir", default="benchmark/c3_compositional_v1")
    parser.add_argument("--report", default="reports/c3/compositional_shortcut_control_v1.json")
    parser.add_argument("--figure", default="figures/sd_core_quality/fig_c3_compositional_shortcut_control.png")
    args = parser.parse_args()

    rows = build_rows(args.per_label)
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_split[str(row["split"])].append(row)

    data_dir = ROOT / args.data_dir
    benchmark_dir = ROOT / args.benchmark_dir
    report_path = ROOT / args.report
    ensure_dirs(data_dir, benchmark_dir, report_path.parent)
    for split, values in by_split.items():
        write_jsonl(data_dir / f"{split}.jsonl", values)

    task_reports: dict[str, Any] = {}
    for task in LABELS:
        task_train = [row for row in by_split["train"] if row["task_type"] == task]
        task_validation = [row for row in by_split["validation"] if row["task_type"] == task]
        task_test = [row for row in by_split["test"] if row["task_type"] == task]
        lexical = fit_tfidf(task_train, task_test, mask_values=False)
        masked_lexical = fit_tfidf(task_train, task_test, mask_values=True)
        formal = contract_metrics(task_test)
        similarity = exhaustive_similarity(
            [normalized_surface(model_text(row)) for row in task_train],
            [normalized_surface(model_text(row)) for row in task_test],
        )
        task_reports[task] = {
            "train": counts(task_train),
            "validation": counts(task_validation),
            "test": counts(task_test),
            "tfidf_test": lexical,
            "value_masked_tfidf_test": masked_lexical,
            "contract_test": formal,
            "train_test_surface_similarity": similarity,
        }

    surfaces = [normalized_surface(model_text(row)) for row in rows]
    split_surface_sets = {
        split: {normalized_surface(model_text(row)) for row in values}
        for split, values in by_split.items()
    }
    cross_collisions = {
        "train_validation": len(split_surface_sets["train"] & split_surface_sets["validation"]),
        "train_test": len(split_surface_sets["train"] & split_surface_sets["test"]),
        "validation_test": len(split_surface_sets["validation"] & split_surface_sets["test"]),
    }
    unique_rate = len(set(surfaces)) / len(surfaces)
    hard_gates = {
        "all_rows_retained": sum(len(values) for values in by_split.values()) == len(rows),
        "template_families_disjoint": not (
            TRAIN_FAMILIES & VALIDATION_FAMILIES
            or TRAIN_FAMILIES & TEST_FAMILIES
            or VALIDATION_FAMILIES & TEST_FAMILIES
        ),
        "all_labels_in_every_split": all(
            set(Counter(row["label"] for row in by_split[split] if row["task_type"] == task)) == set(labels)
            for split in ("train", "validation", "test")
            for task, labels in LABELS.items()
        ),
        "zero_normalized_surface_collisions": all(value == 0 for value in cross_collisions.values()),
        "normalized_surface_unique_rate_at_least_0_95": unique_rate >= 0.95,
        "full_cross_split_max_jaccard_below_0_85": all(
            item["train_test_surface_similarity"]["max_jaccard"] < 0.85 for item in task_reports.values()
        ),
        "typed_contract_macro_f1_equals_1": all(item["contract_test"]["macro_f1"] == 1.0 for item in task_reports.values()),
        "value_masked_tfidf_macro_f1_at_most_0_50": all(
            item["value_masked_tfidf_test"]["macro_f1"] <= 0.50 for item in task_reports.values()
        ),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(hard_gates.values()) else "fail",
        "objective": "C3 compositional shortcut control for operation-ticket review and dispatch-tool routing",
        "construction_contract": {
            "label_before_language": True,
            "split_key": "template_family",
            "split_assignment_uses_labels": False,
            "model_score_used_in_generation_or_split": False,
            "similarity_candidate_sampling": False,
            "label_contracts": ["ticket_temporal_identity_watch_v1", "route_operating_margin_priority_v1"],
        },
        "counts": {split: counts(values) for split, values in sorted(by_split.items())},
        "normalized_surface": {
            "normalization": "lowercase, mask identifiers and numeric values, retain operational wording",
            "records": len(surfaces),
            "unique": len(set(surfaces)),
            "unique_rate": unique_rate,
            "cross_split_collisions": cross_collisions,
        },
        "tasks": task_reports,
        "figure": args.figure,
        "hard_gates": hard_gates,
        "interpretation": "This controlled benchmark tests compositional relation use under fully held-out wording families. The typed evaluator validates the published task contracts; the TF-IDF score measures residual lexical predictability and is not an operational-capability score.",
    }
    write_json(report_path, report)
    write_json(benchmark_dir / "tfidf_and_contract_report.json", report)
    plot_report(task_reports, ROOT / args.figure)
    print(json.dumps({
        "status": report["status"],
        "records": len(rows),
        "normalized_surface_unique_rate": unique_rate,
        "tasks": {
            task: {
                "tfidf_macro_f1": item["tfidf_test"]["macro_f1"],
                "value_masked_tfidf_macro_f1": item["value_masked_tfidf_test"]["macro_f1"],
                "contract_macro_f1": item["contract_test"]["macro_f1"],
                "max_jaccard": item["train_test_surface_similarity"]["max_jaccard"],
                "max_cosine": item["train_test_surface_similarity"]["max_cosine"],
            }
            for task, item in task_reports.items()
        },
    }, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
