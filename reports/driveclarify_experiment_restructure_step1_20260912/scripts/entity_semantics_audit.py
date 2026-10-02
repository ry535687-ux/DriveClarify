#!/usr/bin/env python3
"""任务 B 审计：参照实体 ID 与任务目标在现有比较器中是否被混同。

只读历史保存输入与冻结标签，不重标、不改阈值。
核心问题：两候选的 referring_expression_id / 参照实体不同时，
现有实现是否**仅因此**判 TASK_DIVERGENT？
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/buaa/wrh/DriveClarify")

from driveclarify_rq1_conditional_supplement import paths  # noqa: E402

TOPOLOGY_PROJECTION_FIELDS = (
    "maneuver", "public_topology_target", "ordering", "constraint", "completion_predicate",
)


def audit_c(split: str) -> dict:
    inputs = [json.loads(line) for line in
              (paths.SRC_C_INPUTS / f"{split}_METHOD_INPUTS.jsonl").open(encoding="utf-8")]
    labels = {row["sample_id"]: row for row in
              (json.loads(line) for line in
               (paths.SRC_C_LABELS / f"{split}_LABELS.jsonl").open(encoding="utf-8"))}

    cross = Counter()
    proof_entity_differs_but_equivalent = []
    proof_entity_differs_and_divergent = []
    entity_only_divergence = []
    for row in inputs:
        obligations = row.get("runtime_context", {}).get("candidate_obligations", [])
        if len(obligations) != 2:
            continue
        label = labels.get(row["sample_id"], {})
        truth = label.get("task_relation") or label.get("truth") or ""
        ref_ids = [str((o.get("binding_evidence") or {}).get("referring_expression_id"))
                   for o in obligations]
        entity_differs = ref_ids[0] != ref_ids[1]
        if any(o.get("available") is not True for o in obligations):
            projection_equal = None
        else:
            projections = [{k: o.get(k) for k in TOPOLOGY_PROJECTION_FIELDS} for o in obligations]
            projection_equal = projections[0] == projections[1]
        cross[(entity_differs, projection_equal, truth)] += 1
        record = {"sample_id": row["sample_id"], "unit_id": row.get("unit_id"),
                  "referring_expression_ids": ref_ids, "truth": truth,
                  "topology_projection_equal": projection_equal}
        if entity_differs and projection_equal is True:
            proof_entity_differs_but_equivalent.append(record)
        if entity_differs and projection_equal is False:
            proof_entity_differs_and_divergent.append(record)
        # 「仅因实体不同而分歧」：实体不同、但任务投影字段全等 → 若判分歧就是混同。
        if entity_differs and projection_equal is True and truth == "TASK_DIVERGENT":
            entity_only_divergence.append(record)

    return {
        "split": split,
        "n_samples": len(inputs),
        "cross_tab_entity_differs__projection_equal__truth":
            {f"entity_differs={k[0]}|projection_equal={k[1]}|truth={k[2] or 'UNDEFINED'}": v
             for k, v in sorted(cross.items(), key=lambda kv: str(kv[0]))},
        "n_entity_differs_but_topology_equivalent": len(proof_entity_differs_but_equivalent),
        "n_entity_differs_and_topology_divergent": len(proof_entity_differs_and_divergent),
        "n_divergent_explained_only_by_entity_id": len(entity_only_divergence),
        "examples_entity_differs_but_topology_equivalent": proof_entity_differs_but_equivalent[:5],
        "examples_entity_differs_and_topology_divergent": proof_entity_differs_and_divergent[:5],
        "examples_divergence_explained_only_by_entity_id": entity_only_divergence[:5],
    }


def audit_b() -> dict:
    rows = []
    entity_differs_same_region = []
    for path in sorted(paths.SRC_B_CONFIGS.glob("*.json")):
        config = json.loads(path.read_text(encoding="utf-8"))
        method_input = config["method_input"]
        signatures = method_input.get("task_signatures", [])
        alternatives = method_input.get("alternatives", [])
        components = sorted({c for row in signatures for c in row.get("relevant_components", [])})
        regions = [row.get("task_completion_region") for row in signatures]
        # 候选描述文本不同 ⟹ 指向的参照实体不同（近/远建筑、前/后车等）。
        descriptions = [row.get("description") for row in alternatives]
        evidence_ids = [row.get("evidence_id") for row in alternatives]
        connector_ids = [row.get("connector_id") for row in alternatives]
        record = {
            "config": path.stem,
            "arm": "ABL_TRAJ_ONLY" if path.stem.endswith("ABL_TRAJ_ONLY") else "ABL_FULL",
            "relevant_components": components,
            "task_completion_regions": regions,
            "reference_descriptions_differ": len(set(descriptions)) > 1,
            "evidence_ids_differ": len(set(evidence_ids)) > 1,
            "connector_ids_differ": len(set(connector_ids)) > 1,
            "regions_equal": (regions[0] == regions[1]) if len(regions) == 2 else None,
        }
        rows.append(record)
        if record["reference_descriptions_differ"] and record["regions_equal"] is True:
            entity_differs_same_region.append(record)
    return {
        "n_configs": len(rows),
        "projection_components_observed":
            {(",".join(k) or "<NONE_STRIPPED_BY_ABLATION>"): v for k, v in
             Counter(tuple(r["relevant_components"]) for r in rows).most_common()},
        "n_reference_entity_differs_but_same_task_completion_region": len(entity_differs_same_region),
        "examples_reference_entity_differs_but_same_region": entity_differs_same_region[:4],
        "all_configs": rows,
    }


def main() -> int:
    out = {
        "question": "参照实体 ID 不同是否被现有实现无条件当作任务不同？",
        "c_topology_projection_fields_compared": list(TOPOLOGY_PROJECTION_FIELDS),
        "c_referring_expression_id_is_compared": False,
        "c_referring_expression_id_location": "candidate_obligations[].binding_evidence "
                                             "(记录进 evidence，不进入比较投影)",
        "c_code_location": "driveclarify_rq1_grounded_relation_v3/methods.py:103-121",
        "b_projection_field": "task_signatures[].relevant_components -> task_completion_region",
        "b_code_location": "driveclarify_rq1_conditional_supplement/judges.py:94-108",
        "C_DEV": audit_c("DEV"),
        "C_HIST": audit_c("HIST"),
        "B": audit_b(),
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
