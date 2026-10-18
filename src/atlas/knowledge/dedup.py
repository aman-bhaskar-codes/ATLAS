"""Research Crawler Deduplication Engine: Bloom Filter & MinHash LSH.

1. Bloom Filter (O(k)):
   Bit-array Bloom Filter (m = 100,000 bits, k = 7 hash functions) to reject
   already-visited search and crawl URLs before issuing HTTP requests.

2. 128-Permutation MinHash with Locality-Sensitive Hashing (LSH, O(1)):
   128-permutation MinHash signature over 5-word shingles.
   16 bands of 8 rows (threshold ~ 0.85). Rejects web pages with Jaccard
   similarity J >= 0.85 via LSH bucket lookup, preventing redundant document
   crawling, storage, and context blowout.
"""

from __future__ import annotations

import hashlib
import re


class UrlBloomFilter:
    """Bit-array Bloom Filter for O(k) visited URL tracking."""

    def __init__(self, m: int = 100_000, k: int = 7) -> None:
        self.m = m
        self.k = k
        # Byte array for m bits: ceil(m / 8) bytes
        self._bits = bytearray((m + 7) // 8)
        self._count = 0

    def _hashes(self, item: str) -> list[int]:
        """Generate k bit indices using double hashing (Kirsch-Mitzenmacher)."""
        b = item.encode("utf-8")
        h1 = int.from_bytes(hashlib.sha256(b).digest()[:8], "big")
        h2 = int.from_bytes(hashlib.md5(b).digest()[:8], "big")
        indices: list[int] = []
        for i in range(self.k):
            idx = (h1 + i * h2) % self.m
            indices.append(idx)
        return indices

    def add(self, item: str) -> None:
        """Mark an item as present in the filter."""
        for idx in self._hashes(item):
            byte_idx = idx // 8
            bit_idx = idx % 8
            self._bits[byte_idx] |= 1 << bit_idx
        self._count += 1

    def __contains__(self, item: str) -> bool:
        """Return True if item might be present; False if definitely not."""
        for idx in self._hashes(item):
            byte_idx = idx // 8
            bit_idx = idx % 8
            if not (self._bits[byte_idx] & (1 << bit_idx)):
                return False
        return True

    def should_visit(self, url: str) -> bool:
        """Atomically test if URL was unvisited and register it.

        Returns True if unvisited (should be crawled), False if already visited.
        """
        if url in self:
            return False
        self.add(url)
        return True

    @property
    def size(self) -> int:
        return self._count


class MinHashLSH:
    """128-Permutation MinHash generator and LSH Index for near-duplicate detection."""

    def __init__(
        self,
        num_perm: int = 128,
        num_bands: int = 16,
        similarity_threshold: float = 0.85,
    ) -> None:
        assert num_perm % num_bands == 0, "num_perm must be divisible by num_bands"
        self.num_perm = num_perm
        self.num_bands = num_bands
        self.rows_per_band = num_perm // num_bands
        self.similarity_threshold = similarity_threshold
        # Seeds for the 128 hash permutations
        self._a = [
            (i * 1000003 + 3) & 0xFFFFFFFFFFFFFFFF for i in range(1, num_perm + 1)
        ]
        self._b = [
            (i * 9999991 + 7) & 0xFFFFFFFFFFFFFFFF for i in range(1, num_perm + 1)
        ]
        self._prime = (1 << 61) - 1  # Mersenne prime 2^61 - 1
        # LSH buckets: dict[band_idx, dict[band_hash, list[doc_id]]]
        self._buckets: list[dict[int, list[str]]] = [{} for _ in range(self.num_bands)]
        self._signatures: dict[str, list[int]] = {}

    def shingle_text(self, text: str, shingle_size: int = 5) -> set[str]:
        """Convert text into normalized n-word shingles."""
        words = re.findall(r"\w+", text.lower())
        if len(words) < shingle_size:
            return {" ".join(words)} if words else set()
        return {
            " ".join(words[i : i + shingle_size])
            for i in range(len(words) - shingle_size + 1)
        }

    def compute_signature(self, text: str, shingle_size: int = 5) -> list[int]:
        """Compute 128-permutation MinHash signature for text."""
        shingles = self.shingle_text(text, shingle_size)
        if not shingles:
            return [0] * self.num_perm

        shingle_hashes: list[int] = []
        for s in shingles:
            h = int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:8], "big")
            shingle_hashes.append(h)

        sig = [0xFFFFFFFFFFFFFFFF] * self.num_perm
        for h in shingle_hashes:
            for i in range(self.num_perm):
                val = (self._a[i] * h + self._b[i]) % self._prime
                if val < sig[i]:
                    sig[i] = val
        return sig

    def jaccard_similarity(self, sig1: list[int], sig2: list[int]) -> float:
        """Estimate Jaccard similarity from two MinHash signatures."""
        if not sig1 or not sig2 or len(sig1) != len(sig2):
            return 0.0
        matches = sum(1 for a, b in zip(sig1, sig2, strict=False) if a == b)
        return matches / len(sig1)

    def is_duplicate_or_add(
        self, doc_id: str, text: str
    ) -> tuple[bool, str | None, float]:
        """Check if document is near-duplicate (J >= threshold) of any indexed doc.

        If near-duplicate, returns (True, matched_doc_id, estimated_jaccard).
        Otherwise, indexes the document into LSH bands and returns (False, None, max_jaccard).
        """
        sig = self.compute_signature(text)

        # 1. Query LSH candidate matches
        candidate_ids: set[str] = set()
        for band_idx in range(self.num_bands):
            start = band_idx * self.rows_per_band
            end = start + self.rows_per_band
            band_tuple = tuple(sig[start:end])
            band_hash = hash(band_tuple)
            bucket = self._buckets[band_idx].get(band_hash)
            if bucket:
                candidate_ids.update(bucket)

        # 2. Check candidate exact MinHash similarity
        max_sim = 0.0
        best_match: str | None = None
        for cand_id in candidate_ids:
            cand_sig = self._signatures.get(cand_id)
            if cand_sig:
                sim = self.jaccard_similarity(sig, cand_sig)
                if sim > max_sim:
                    max_sim = sim
                    best_match = cand_id
                if sim >= self.similarity_threshold:
                    return True, cand_id, sim

        # 3. Not a duplicate: store signature and index into buckets
        self._signatures[doc_id] = sig
        for band_idx in range(self.num_bands):
            start = band_idx * self.rows_per_band
            end = start + self.rows_per_band
            band_tuple = tuple(sig[start:end])
            band_hash = hash(band_tuple)
            if band_hash not in self._buckets[band_idx]:
                self._buckets[band_idx][band_hash] = []
            self._buckets[band_idx][band_hash].append(doc_id)

        return False, best_match, max_sim
