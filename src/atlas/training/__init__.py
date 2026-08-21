"""Offline training layer for the Knowledge Fabric (§67-74, §101-106).

Everything here is OFFLINE (§130): it mines user feedback into training
triplets, tunes rerank weights without gradient descent (free-first), and
registers the result as EXPERIMENTAL adapters that promotion gates
(evaluation layer) must validate before ACTIVE use (§101, §128-129).

Real LoRA fine-tuning of embedders/rerankers is PLANNED, not faked: the
triplet exports are the training data format that pipeline will consume
(see docs/knowledge/FINAL_REPORT_PROMPT_3.md §fine-tuning).
"""

from atlas.training.pipelines import ModelAdapterRegistry, RerankerTrainingPipeline, RetrieverTrainingPipeline
from atlas.training.triplets import ChunkResolver, Triplet, TripletReport, mine_triplets

__all__ = [
    "ChunkResolver",
    "ModelAdapterRegistry",
    "RerankerTrainingPipeline",
    "RetrieverTrainingPipeline",
    "Triplet",
    "TripletReport",
    "mine_triplets",
]
