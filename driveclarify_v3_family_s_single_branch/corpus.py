"""Dedicated offline builder for A1_ENGINEERING_CAPABILITY_CORPUS_V1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

from .evidence import EXPERT_OWNER, FROZEN_TICKS, content_hash, json_copy


class CorpusConstructionError(RuntimeError):
    """The minimum engineering corpus or leakage contract failed closed."""


@dataclass(frozen=True)
class CorpusBuildResult:
    manifest: Mapping[str, Any]
    train_ticks_family_s: int
    dev_ticks_family_s: int
    test_ticks_family_s: int
    train_ticks_family_o: int
    dev_ticks_family_o: int
    test_ticks_family_o: int


class A1EngineeringCorpusBuilder(object):
    """Seal one fixed 4/1/1 positive plus balanced retention corpus."""

    _SPLITS = ("train", "dev", "held_out_diagnostic")
    _MIN_S_EPISODES = {
        "train": 4, "dev": 1, "held_out_diagnostic": 1,
    }

    @staticmethod
    def _require_entry(entry: Mapping[str, Any]) -> None:
        required = {
            "family", "split", "episode_id", "record_id", "record_sha256",
            "source_artifact_sha256", "source_frame", "route_id",
            "route_suffix_sha256", "maneuver_class", "route_switch_active",
            "road_ids", "expert_owner", "sample_tick_offsets",
            "supervised_tick_count", "input_artifact_sha256",
            "target_artifact_sha256",
        }
        if not isinstance(entry, Mapping) or set(entry) != required:
            raise CorpusConstructionError("CORPUS_ENTRY_FIELD_SET_INVALID")
        if entry["family"] not in ("FAMILY_S", "FAMILY_O"):
            raise CorpusConstructionError("CORPUS_FAMILY_INVALID")
        if entry["split"] not in A1EngineeringCorpusBuilder._SPLITS:
            raise CorpusConstructionError("CORPUS_SPLIT_INVALID")
        if entry["expert_owner"] != EXPERT_OWNER:
            raise CorpusConstructionError("CORPUS_EXPERT_OWNER_INVALID")
        if entry["sample_tick_offsets"] != list(FROZEN_TICKS):
            raise CorpusConstructionError("CORPUS_FROZEN_TICKS_INVALID")
        if entry["supervised_tick_count"] != 20:
            raise CorpusConstructionError("CORPUS_SUPERVISED_TICK_COUNT_INVALID")
        marker = entry["route_switch_active"]
        if not isinstance(marker, bool):
            raise CorpusConstructionError("CORPUS_MARKER_NOT_BOOLEAN")
        if entry["family"] == "FAMILY_S" and marker is not True:
            raise CorpusConstructionError("FAMILY_S_MARKER_MUST_BE_TRUE")
        if entry["family"] == "FAMILY_O" and marker is not False:
            raise CorpusConstructionError("FAMILY_O_MARKER_MUST_BE_FALSE")
        if not isinstance(entry["road_ids"], list) or not all(
                isinstance(value, int) for value in entry["road_ids"]):
            raise CorpusConstructionError("CORPUS_ROAD_IDS_INVALID")

    def build(self, entries: Sequence[Mapping[str, Any]]) -> CorpusBuildResult:
        detached = [json_copy(entry) for entry in entries]
        for entry in detached:
            self._require_entry(entry)
        episodes_by_split = {split: set() for split in self._SPLITS}
        sources_by_split = {split: set() for split in self._SPLITS}
        suffixes_by_split = {split: set() for split in self._SPLITS}
        hashes_by_split = {split: set() for split in self._SPLITS}
        for entry in detached:
            split = entry["split"]
            episode = entry["episode_id"]
            if any(
                    episode in episodes_by_split[other]
                    for other in self._SPLITS if other != split):
                raise CorpusConstructionError("DUPLICATE_EPISODE_SPLIT_LEAKAGE")
            episodes_by_split[split].add(episode)
            source_key = (
                entry["source_artifact_sha256"], entry["source_frame"]
            )
            if any(
                    source_key in sources_by_split[other]
                    for other in self._SPLITS if other != split):
                raise CorpusConstructionError("SOURCE_FRAME_SPLIT_LEAKAGE")
            sources_by_split[split].add(source_key)
            suffixes_by_split[split].add(entry["route_suffix_sha256"])
            hashes_by_split[split].update({
                entry["record_sha256"], entry["input_artifact_sha256"],
                entry["target_artifact_sha256"],
            })
            if split in ("train", "dev") and 886 in entry["road_ids"]:
                raise CorpusConstructionError("ROAD_886_TRAIN_DEV_EXCLUDED")
        if suffixes_by_split["train"] & suffixes_by_split["held_out_diagnostic"]:
            raise CorpusConstructionError("ROUTE_SUFFIX_TRAIN_TEST_LEAKAGE")
        if hashes_by_split["held_out_diagnostic"] & (
                hashes_by_split["train"] | hashes_by_split["dev"]):
            raise CorpusConstructionError("TEST_ARTIFACT_HASH_IN_TRAIN_DEV")

        counts = {
            family: {split: [] for split in self._SPLITS}
            for family in ("FAMILY_S", "FAMILY_O")
        }
        for entry in detached:
            counts[entry["family"]][entry["split"]].append(entry)
        for split, minimum in self._MIN_S_EPISODES.items():
            if len(counts["FAMILY_S"][split]) < minimum:
                raise CorpusConstructionError("FAMILY_S_" + split.upper() + "_COUNT")
            if len(counts["FAMILY_O"][split]) < len(counts["FAMILY_S"][split]):
                raise CorpusConstructionError("FAMILY_O_" + split.upper() + "_RETENTION_COUNT")

        train_s = counts["FAMILY_S"]["train"]
        train_maneuvers = {entry["maneuver_class"] for entry in train_s}
        train_routes = {entry["route_id"] for entry in train_s}
        if len(train_maneuvers) < 2:
            raise CorpusConstructionError("FAMILY_S_TRAIN_MANEUVER_DIVERSITY")
        if len(train_routes) < 3:
            raise CorpusConstructionError("FAMILY_S_TRAIN_ROUTE_DIVERSITY")
        ordinary_maneuvers = {
            entry["maneuver_class"]
            for entry in counts["FAMILY_O"]["train"]
        }
        if not train_maneuvers.issubset(ordinary_maneuvers):
            raise CorpusConstructionError("MARKER_MANEUVER_PERFECT_CORRELATION")

        ordered = sorted(
            detached,
            key=lambda item: (
                self._SPLITS.index(item["split"]), item["family"],
                item["episode_id"], item["record_id"],
            ),
        )
        entry_receipts = []
        for entry in ordered:
            eligibility = {
                "a1_training_eligible": entry["split"] == "train",
                "a1_dev_eligible": entry["split"] == "dev",
                "a1_test_eligible": entry["split"] == "held_out_diagnostic",
            }
            entry_receipts.append({
                "entry": entry,
                "entry_sha256": content_hash(entry),
                "offline_corpus_eligibility": eligibility,
            })
        tick_counts = {
            family: {
                split: sum(
                    item["supervised_tick_count"]
                    for item in counts[family][split]
                )
                for split in self._SPLITS
            }
            for family in counts
        }
        payload = {
            "schema": "driveclarify.v3.a1-engineering-capability-corpus.v1",
            "corpus_name": "A1_ENGINEERING_CAPABILITY_CORPUS_V1",
            "claim_boundary": "BOUNDED_A1_ENGINEERING_CAPABILITY_ONLY",
            "entries": entry_receipts,
            "episode_counts": {
                family: {
                    split: len(counts[family][split])
                    for split in self._SPLITS
                }
                for family in counts
            },
            "supervised_tick_counts": tick_counts,
            "held_out_unconsumed_by_training": True,
            "duplicate_episode_leakage": 0,
            "source_frame_leakage": 0,
            "route_suffix_train_test_leakage": 0,
            "test_artifact_hash_in_train_dev": 0,
            "road_886_train_dev_count": 0,
            "marker_perfectly_correlated_with_maneuver": False,
            "train_maneuver_count": len(train_maneuvers),
            "train_route_count": len(train_routes),
        }
        manifest = {
            "schema": "driveclarify.v3.a1-engineering-capability-corpus-envelope.v1",
            "payload": payload,
            "payload_sha256": content_hash(payload),
        }
        return CorpusBuildResult(
            manifest=manifest,
            train_ticks_family_s=tick_counts["FAMILY_S"]["train"],
            dev_ticks_family_s=tick_counts["FAMILY_S"]["dev"],
            test_ticks_family_s=tick_counts["FAMILY_S"]["held_out_diagnostic"],
            train_ticks_family_o=tick_counts["FAMILY_O"]["train"],
            dev_ticks_family_o=tick_counts["FAMILY_O"]["dev"],
            test_ticks_family_o=tick_counts["FAMILY_O"]["held_out_diagnostic"],
        )

