#!/usr/bin/env python3

"""Simple LocBench scoring (file/entity recall)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minisweagent.locbench.utils import load_jsonl


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path)


def score_locbench(pred_path: Path, dataset_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    preds = {p["instance_id"]: p for p in _load_jsonl(pred_path) if p.get("instance_id")}
    dataset = {d["instance_id"]: d for d in _load_jsonl(dataset_path) if d.get("instance_id")}

    results: list[dict[str, Any]] = []
    for instance_id, pred in preds.items():
        if instance_id not in dataset:
            continue
        gt = dataset[instance_id]

        gt_files: set[str] = set()
        gt_entities: set[str] = set()
        for func in gt.get("edit_functions", []) + gt.get("added_functions", []):
            parts = func.rsplit(":", 1)
            if len(parts) == 2:
                gt_files.add(parts[0])
                gt_entities.add(func)

        pred_files = set(pred.get("found_files", []))
        pred_entities = set(pred.get("found_entities", []))

        file_recall = len(gt_files & pred_files) / len(gt_files) if gt_files else 0.0
        entity_recall = len(gt_entities & pred_entities) / len(gt_entities) if gt_entities else 0.0

        results.append(
            {
                "instance_id": instance_id,
                "gt_files": len(gt_files),
                "pred_files": len(pred_files),
                "file_hits": len(gt_files & pred_files),
                "file_recall": file_recall,
                "gt_entities": len(gt_entities),
                "pred_entities": len(pred_entities),
                "entity_hits": len(gt_entities & pred_entities),
                "entity_recall": entity_recall,
            }
        )

    avg_file_recall = sum(r["file_recall"] for r in results) / len(results) if results else 0.0
    avg_entity_recall = sum(r["entity_recall"] for r in results) / len(results) if results else 0.0
    summary = {
        "instances": len(results),
        "avg_file_recall": avg_file_recall,
        "avg_entity_recall": avg_entity_recall,
    }
    return results, summary


def write_scores(path: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "results": results}
    path.write_text(json.dumps(payload, indent=2))
