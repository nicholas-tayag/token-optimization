from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from agenvantage.repo_context import build_context_package, chunks_for_repo, source_files
from agenvantage.repo_index import build_repository_index
from agenvantage.tokenizer import TokenCounter

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = REPO_ROOT / "examples" / "cold_start_sufficiency_cases.json"
DEFAULT_OUTPUT_JSON = REPO_ROOT / "artifacts" / "cold-start-sufficiency.json"


def _fixture_sources(case: dict[str, Any]) -> dict[str, str]:
    sources = dict(case["fixture"])
    expansion = case.get("corpus_expansion", {})
    style_padding = int(expansion.get("oracle_style_padding_lines", 0))
    if style_padding:
        sources["src/styles.css"] += "".join(
            f".dashboard .history-row-{index:02d} {{ display: grid; grid-template-columns: 1fr auto; gap: 12px; padding: 10px; }}\n"
            for index in range(style_padding)
        )
    file_count = int(expansion.get("distractor_file_count", 0))
    line_count = int(expansion.get("distractor_lines_per_file", 0))
    for file_index in range(file_count):
        sources[f"src/distractors/ArchivedDashboard{file_index:02d}.tsx"] = "".join(
            f'export const archivedWorkoutDashboard{file_index:02d}_{line_index:02d} = "weekly workout streak dashboard visualization current longest missed days";\n'
            for line_index in range(line_count)
        )
    return sources


def _materialize(case: dict[str, Any], root: Path) -> tuple[Path, dict[str, str]]:
    repo = root / case["repository"]
    sources = _fixture_sources(case)
    for relative, content in sources.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return repo, sources


def _source_tokens(text: str) -> set[str]:
    split_camel = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return set(re.findall(r"[a-z0-9]+", split_camel.lower()))


def _evaluate_annotations(
    case: dict[str, Any],
    fixture: dict[str, str],
    report: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[str], list[dict[str, Any]]]:
    invalid: list[str] = []
    annotations: list[dict[str, Any]] = []
    for slot, annotation in case["evidence_slots"].items():
        path = annotation["path"]
        start, end = annotation["line_range"]
        source = fixture.get(path)
        lines = source.splitlines() if source is not None else []
        region = "\n".join(lines[start - 1 : end]) if 1 <= start <= end <= len(lines) else ""
        anchors = _source_tokens(" ".join(annotation["all_anchor_tokens"]))
        valid = bool(source) and bool(region) and anchors <= _source_tokens(region)
        if not valid:
            invalid.append(slot)
        annotations.append({"slot": slot, "path": path, "start_line": start, "end_line": end, "anchors": anchors, "valid": valid})
    selected = report["selected_chunks"]
    def chunk_has_anchors(chunk: dict[str, Any], item: dict[str, Any]) -> bool:
        lines = (fixture[chunk["path"]]).splitlines()
        text = "\n".join(lines[chunk["start_line"] - 1 : chunk["end_line"]])
        return item["anchors"] <= _source_tokens(text)
    covered, missing = [], []
    reasons: list[str] = []
    for item in annotations:
        if not item["valid"]:
            missing.append(item["slot"])
            reasons.append(f"{item['slot']}:invalid_annotation")
            continue
        matching = [chunk for chunk in selected if chunk["path"] == item["path"] and chunk["start_line"] <= item["end_line"] and chunk["end_line"] >= item["start_line"] and chunk_has_anchors(chunk, item)]
        if matching:
            covered.append(item["slot"])
        else:
            missing.append(item["slot"])
            reasons.append(
                f"{item['slot']}:region_not_selected"
                if any(chunk["path"] == item["path"] for chunk in selected)
                else f"{item['slot']}:path_not_selected"
            )
    return covered, missing, invalid, reasons, annotations


def _oracle_recovery(
    annotations: list[dict[str, Any]],
    chunks: list[Any],
) -> tuple[list[str], list[str], list[str], int]:
    """Find the minimum generated chunk for each valid evidence slot."""
    covered: list[str] = []
    missing: list[str] = []
    oracle_chunks: list[Any] = []
    for item in annotations:
        matches = [
            chunk
            for chunk in chunks
            if item["valid"]
            and chunk.relative_path == item["path"]
            and chunk.start_line <= item["end_line"]
            and chunk.end_line >= item["start_line"]
            and item["anchors"] <= _source_tokens(chunk.text)
        ]
        if matches:
            covered.append(item["slot"])
            oracle_chunks.append(min(matches, key=lambda chunk: (chunk.addition_tokens, chunk.chunk_id)))
        else:
            missing.append(item["slot"])

    unique_chunks = {chunk.chunk_id: chunk for chunk in oracle_chunks}
    oracle_ids = sorted(unique_chunks)
    return covered, missing, oracle_ids, sum(
        chunk.addition_tokens for chunk in unique_chunks.values()
    )


def _run_case(case: dict[str, Any], counter: TokenCounter, root: Path) -> dict[str, Any]:
    repo, fixture_sources = _materialize(case, root)
    started = time.perf_counter()
    _, legacy_report = build_context_package(
        repo,
        case["task"],
        1_800,
        counter,
        top_k=case["top_k"],
        workflow="feature",
    )
    _, report = build_context_package(repo, case["task"], case["budget"], counter, top_k=case["top_k"], workflow="feature")
    graph_report: dict[str, Any] | None = None
    if isinstance(case.get("graph"), dict):
        graph_path = root / f"{case['case_id']}-graph.json"
        graph_path.write_text(json.dumps(case["graph"]), encoding="utf-8")
        _, graph_report = build_context_package(
            repo,
            case["task"],
            case["budget"],
            counter,
            top_k=case["top_k"],
            workflow="feature",
            graph_backend="graphify",
            graph_json=graph_path,
        )
    accounting = report["prompt_token_accounting"]
    covered, missing, invalid, miss_reasons, annotations = _evaluate_annotations(
        case, fixture_sources, report
    )
    legacy_covered, legacy_missing, legacy_invalid, legacy_reasons, _ = _evaluate_annotations(
        case, fixture_sources, legacy_report
    )
    files = source_files(repo)
    index_result = build_repository_index(repo, files)
    all_chunks = chunks_for_repo(
        repo,
        counter,
        overlap_lines=4,
        files=files,
        file_index=index_result.entries,
    )
    oracle_covered, oracle_missing, oracle_ids, oracle_tokens = _oracle_recovery(annotations, all_chunks)
    selected_paths = sorted({chunk["path"] for chunk in report["selected_chunks"]})
    result = {
        "case_id": case["case_id"], "repository": case["repository"],
        "requested_tokens": case["budget"], "effective_budget": report["effective_budget"],
        "packed_tokens": accounting["packed_prompt_tokens"], "full_scan_tokens": accounting["full_scan_prompt_tokens"],
        "selected_paths": selected_paths,
        "selected_line_regions": [{"path": c["path"], "start_line": c["start_line"], "end_line": c["end_line"]} for c in report["selected_chunks"]],
        "covered_evidence_slots": covered, "missing_evidence_slots": missing,
        "readiness": not missing and not invalid, "missing_slot_reasons": miss_reasons,
        "fixture_invalid": bool(invalid), "invalid_annotations": invalid,
        "annotation_mismatch_count": len(invalid), "selected_coverage_percent": round(len(covered) / len(annotations) * 100, 2) if annotations else 0.0,
        "oracle_covered_evidence_slots": oracle_covered,
        "missing_oracle_evidence_slots": oracle_missing,
        "oracle_coverage_percent": round(len(oracle_covered) / len(annotations) * 100, 2) if annotations else 0.0,
        "oracle_chunk_ids": oracle_ids, "oracle_tokens": oracle_tokens,
        "missing_signals": (report.get("change_surface") or {}).get("missing_signals", []),
        "legacy": {
            "requested_tokens": 1_800,
            "effective_budget": legacy_report["effective_budget"],
            "packed_tokens": legacy_report["prompt_token_accounting"]["packed_prompt_tokens"],
            "covered_evidence_slots": legacy_covered,
            "missing_evidence_slots": legacy_missing,
            "missing_slot_reasons": legacy_reasons,
            "selected_coverage_percent": round(
                len(legacy_covered) / len(annotations) * 100, 2
            )
            if annotations
            else 0.0,
            "readiness": not legacy_missing and not legacy_invalid,
            "fixture_invalid": bool(legacy_invalid),
        },
        "pack_runtime_ms": round((time.perf_counter() - started) * 1000, 2)
    }
    if graph_report is not None:
        graph_covered, graph_missing, graph_invalid, graph_reasons, _ = _evaluate_annotations(
            case,
            fixture_sources,
            graph_report,
        )
        result["graphify"] = {
            "packed_tokens": graph_report["prompt_token_accounting"]["packed_prompt_tokens"],
            "covered_evidence_slots": graph_covered,
            "missing_evidence_slots": graph_missing,
            "missing_slot_reasons": graph_reasons,
            "selected_coverage_percent": round(
                len(graph_covered) / len(annotations) * 100,
                2,
            )
            if annotations
            else 0.0,
            "readiness": not graph_missing and not graph_invalid,
            "fixture_invalid": bool(graph_invalid),
            "graph": graph_report.get("graph"),
        }
    return result


def run_cold_start_sufficiency(fixture: Path = DEFAULT_FIXTURE, output_json: Path | None = DEFAULT_OUTPUT_JSON) -> dict[str, Any]:
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    counter = TokenCounter()
    with tempfile.TemporaryDirectory(prefix="agenvantage-cold-start-") as directory:
        cases = [_run_case(case, counter, Path(directory)) for case in payload["cases"]]
    summary = {
        "case_count": len(cases),
        "ready_case_count": sum(case["readiness"] for case in cases),
        "cases_with_missing_evidence": sum(bool(case["missing_evidence_slots"]) for case in cases),
        "legacy_ready_case_count": sum(case["legacy"]["readiness"] for case in cases),
        "cases_improved": sum(
            case["readiness"] and not case["legacy"]["readiness"] for case in cases
        ),
    }
    result = {"summary": summary, "cases": cases}
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure cold-start evidence sufficiency for a packed repository context.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    result = run_cold_start_sufficiency(args.fixture, args.output_json)
    print(json.dumps(result["summary"] if args.summary else result, indent=2))
    print(f"\nJSON report written to {args.output_json.resolve()}")


if __name__ == "__main__":
    main()
