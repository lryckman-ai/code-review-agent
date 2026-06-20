"""Data processing utilities — contains O(n²) algorithms and memory issues."""
from typing import List, Tuple


def find_duplicates(items: List[str]) -> List[str]:
    # PERF: O(n²) — nested loop + linear `in` check; should use a Counter/set
    duplicates = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates


def pairwise_jaccard(texts: List[str]) -> List[Tuple[int, int, float]]:
    # PERF: O(n²) pairs; tokenization recomputed inside inner loop
    results = []
    for i, t1 in enumerate(texts):
        for j, t2 in enumerate(texts):
            if i >= j:
                continue
            # Re-splitting and lowercasing on every pair
            tokens1 = set(t1.lower().split())
            tokens2 = set(t2.lower().split())
            union = tokens1 | tokens2
            score = len(tokens1 & tokens2) / len(union) if union else 0.0
            results.append((i, j, score))
    return results


class EventLog:
    def __init__(self):
        self._events: list = []    # PERF: unbounded growth
        self._raw_payloads: list = []  # PERF: retains all raw bytes forever

    def record(self, event_type: str, payload: bytes) -> None:
        self._events.append({"type": event_type, "size": len(payload)})
        self._raw_payloads.append(payload)   # never evicted

    def search(self, keyword: str) -> list:
        # PERF: full linear scan every call; no index
        return [e for e in self._events if keyword in e["type"]]


def batch_enrich(records: List[dict], lookup_table: List[dict]) -> List[dict]:
    enriched = []
    for record in records:
        for entry in lookup_table:          # PERF: O(n·m) — should build dict first
            if entry["id"] == record["ref_id"]:
                record["label"] = entry["label"]
                break
        enriched.append(record)
    return enriched
