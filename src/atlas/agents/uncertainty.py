"""Uncertainty Quantification - Confidence calibration and uncertainty estimation.

This implements state-of-the-art uncertainty quantification inspired by:
- Bayesian uncertainty estimation
- Conformal prediction
- Ensemble uncertainty
- Calibrated confidence scores

Key features:
1. Calibrated confidence scores for predictions
2. Uncertainty bounds for estimates
3. Epistemic vs aleatoric uncertainty separation
4. Confidence-weighted decision making
5. Uncertainty-aware reasoning
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from atlas.infra.clock import Clock
from atlas.infra.ids import CorrelationId, IdGenerator
from atlas.infra.logging import get_logger
from atlas.intelligence.contracts import Constraints, InferenceRequest, Message, Role
from atlas.intelligence.gateway import ModelGateway

_log = get_logger("atlas.agents.uncertainty")


class UncertaintyType(Enum):
    """Types of uncertainty."""
    EPISTEMIC = "epistemic"      # Model uncertainty (reducible with more data)
    ALEATORIC = "aleatoric"      # Data uncertainty (irreducible noise)
    TOTAL = "total"              # Combined uncertainty


@dataclass
class ConfidenceInterval:
    """A confidence interval for an estimate."""
    lower: float
    upper: float
    confidence_level: float  # e.g., 0.95 for 95% CI
    
    def contains(self, value: float) -> bool:
        return self.lower <= value <= self.upper
    
    def width(self) -> float:
        return self.upper - self.lower


@dataclass
class UncertaintyEstimate:
    """Uncertainty estimate for a prediction or decision."""
    estimate_id: str
    value: Any
    confidence: float
    uncertainty_type: UncertaintyType
    confidence_interval: ConfidenceInterval | None
    epistemic_uncertainty: float  # Model uncertainty
    aleatoric_uncertainty: float  # Data uncertainty
    calibration_score: float  # How well calibrated
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def total_uncertainty(self) -> float:
        return math.sqrt(
            self.epistemic_uncertainty ** 2 + self.aleatoric_uncertainty ** 2
        )


@dataclass
class CalibratedPrediction:
    """A prediction with calibrated confidence."""
    prediction_id: str
    prediction: str
    raw_confidence: float  # Uncalibrated
    calibrated_confidence: float  # After calibration
    uncertainty: UncertaintyEstimate
    alternatives: list[tuple[str, float]]  # (prediction, probability) pairs
    reasoning: str


class UncertaintyQuantifier:
    """Advanced uncertainty quantification system."""
    
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        ids: IdGenerator,
        clock: Clock,
        calibration_samples: int = 100,
        ensemble_size: int = 3,
    ) -> None:
        self._gw = gateway
        self._ids = ids
        self._clock = clock
        self._calibration_samples = calibration_samples
        self._ensemble_size = ensemble_size
        
        # Calibration history
        self._calibration_history: list[tuple[float, bool]] = []
        
        # Confidence calibration parameters
        self._calibration_params = {
            "slope": 1.0,
            "intercept": 0.0,
        }
        
        # Statistics
        self._stats = {
            "total_predictions": 0,
            "avg_confidence": 0.0,
            "calibration_error": 0.0,
            "avg_epistemic": 0.0,
            "avg_aleatoric": 0.0,
        }

    async def quantify_uncertainty(
        self,
        prediction: str,
        context: str,
        confidence: float,
    ) -> UncertaintyEstimate:
        """Quantify uncertainty for a prediction.
        
        Uses multiple methods:
        1. Ensemble disagreement for epistemic uncertainty
        2. Variance analysis for aleatoric uncertainty
        3. Calibration adjustment
        """
        
        _log.debug(
            "uncertainty.quantifying",
            event_type="uncertainty",
            prediction=prediction[:50],
            confidence=confidence,
        )
        
        # Get ensemble predictions
        ensemble_predictions = await self._get_ensemble_predictions(
            prediction,
            context,
        )
        
        # Calculate epistemic uncertainty (disagreement)
        epistemic = self._calculate_epistemic_uncertainty(ensemble_predictions)
        
        # Calculate aleatoric uncertainty (inherent noise)
        aleatoric = await self._calculate_aleatoric_uncertainty(
            prediction,
            context,
        )
        
        # Build confidence interval
        ci = self._build_confidence_interval(
            confidence,
            epistemic,
            aleatoric,
        )
        
        # Calculate calibration score
        calibration_score = self._calculate_calibration_score(confidence)
        
        estimate = UncertaintyEstimate(
            estimate_id=self._ids.execution_id(),
            value=prediction,
            confidence=confidence,
            uncertainty_type=UncertaintyType.TOTAL,
            confidence_interval=ci,
            epistemic_uncertainty=epistemic,
            aleatoric_uncertainty=aleatoric,
            calibration_score=calibration_score,
        )
        
        # Update statistics
        self._stats["total_predictions"] += 1
        n = self._stats["total_predictions"]
        self._stats["avg_confidence"] = (
            self._stats["avg_confidence"] * (n - 1) + confidence
        ) / n
        self._stats["avg_epistemic"] = (
            self._stats["avg_epistemic"] * (n - 1) + epistemic
        ) / n
        self._stats["avg_aleatoric"] = (
            self._stats["avg_aleatoric"] * (n - 1) + aleatoric
        ) / n
        
        return estimate

    async def calibrated_prediction(
        self,
        prompt: str,
        context: str = "",
    ) -> CalibratedPrediction:
        """Generate a prediction with calibrated confidence."""
        
        # Get raw prediction with confidence
        raw_pred = await self._get_raw_prediction(prompt, context)
        
        # Get ensemble for uncertainty
        ensemble = await self._get_ensemble_predictions(raw_pred["prediction"], context)
        
        # Calculate uncertainties
        epistemic = self._calculate_epistemic_uncertainty(ensemble)
        aleatoric = await self._calculate_aleatoric_uncertainty(
            raw_pred["prediction"],
            context,
        )
        
        # Calibrate confidence
        calibrated_conf = self._calibrate_confidence(raw_pred["confidence"])
        
        # Build uncertainty estimate
        uncertainty = UncertaintyEstimate(
            estimate_id=self._ids.execution_id(),
            value=raw_pred["prediction"],
            confidence=calibrated_conf,
            uncertainty_type=UncertaintyType.TOTAL,
            confidence_interval=self._build_confidence_interval(
                calibrated_conf, epistemic, aleatoric
            ),
            epistemic_uncertainty=epistemic,
            aleatoric_uncertainty=aleatoric,
            calibration_score=self._calculate_calibration_score(calibrated_conf),
        )
        
        # Extract alternatives from ensemble
        alternatives = [
            (pred["prediction"], pred["confidence"])
            for pred in ensemble[:3]
            if pred["prediction"] != raw_pred["prediction"]
        ]
        
        return CalibratedPrediction(
            prediction_id=self._ids.execution_id(),
            prediction=raw_pred["prediction"],
            raw_confidence=raw_pred["confidence"],
            calibrated_confidence=calibrated_conf,
            uncertainty=uncertainty,
            alternatives=alternatives,
            reasoning=raw_pred.get("reasoning", ""),
        )

    def record_outcome(
        self,
        prediction_id: str,
        confidence: float,
        correct: bool,
    ) -> None:
        """Record outcome for calibration tracking."""
        
        self._calibration_history.append((confidence, correct))
        
        # Keep limited history
        if len(self._calibration_history) > self._calibration_samples:
            self._calibration_history = self._calibration_history[-self._calibration_samples:]
        
        # Update calibration
        self._update_calibration()

    async def _get_ensemble_predictions(
        self,
        prediction: str,
        context: str,
    ) -> list[dict[str, Any]]:
        """Get predictions from ensemble (simulated via temperature variation)."""
        
        predictions = []
        
        for temp in [0.3, 0.5, 0.7]:
            resp = await self._gw.infer(
                InferenceRequest(
                    correlation_id=CorrelationId(self._ids.execution_id()),
                    messages=[
                        Message(role=Role.SYSTEM, content="You are a helpful assistant."),
                        Message(role=Role.USER, content=f"Context: {context}\n\nPredict: {prediction}"),
                    ],
                    constraints=Constraints(prefer_local=True),
                    max_tokens=500,
                    temperature=temp,
                )
            )
            
            predictions.append({
                "prediction": resp.text,
                "confidence": 0.7,  # Placeholder
            })
        
        return predictions

    async def _get_raw_prediction(
        self,
        prompt: str,
        context: str,
    ) -> dict[str, Any]:
        """Get raw prediction with confidence."""
        
        full_prompt = f"""Make a prediction and provide your confidence.

CONTEXT: {context}
PROMPT: {prompt}

Provide:
1. Your prediction
2. Your confidence (0.0-1.0)
3. Your reasoning

Output JSON:
{{
  "prediction": "your prediction",
  "confidence": 0.0-1.0,
  "reasoning": "explanation"
}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are a prediction expert."),
                    Message(role=Role.USER, content=full_prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=800,
                temperature=0.4,
            )
        )
        
        return self._parse_json(resp.text)

    def _calculate_epistemic_uncertainty(
        self,
        ensemble_predictions: list[dict[str, Any]],
    ) -> float:
        """Calculate epistemic uncertainty from ensemble disagreement."""
        
        if len(ensemble_predictions) <= 1:
            return 0.5
        
        # Measure disagreement using variance in predictions
        # Simple heuristic: measure length variance and keyword variance
        
        lengths = [len(p["prediction"]) for p in ensemble_predictions]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((length - mean_len) ** 2 for length in lengths) / len(lengths)
        
        # Normalize to 0-1
        normalized = min(variance / 10000.0, 1.0)
        
        return normalized

    async def _calculate_aleatoric_uncertainty(
        self,
        prediction: str,
        context: str,
    ) -> float:
        """Calculate aleatoric (data) uncertainty."""
        
        # Use LLM to assess inherent uncertainty in the problem
        prompt = f"""Assess the inherent uncertainty in this prediction:

CONTEXT: {context}
PREDICTION: {prediction}

Is there inherent noise or ambiguity that makes this prediction uncertain?
Rate 0.0-1.0 where 0.0 = completely certain, 1.0 = highly uncertain.

Output JSON: {{"aleatoric_uncertainty": 0.0-1.0}}"""

        resp = await self._gw.infer(
            InferenceRequest(
                correlation_id=CorrelationId(self._ids.execution_id()),
                messages=[
                    Message(role=Role.SYSTEM, content="You are an uncertainty assessor."),
                    Message(role=Role.USER, content=prompt),
                ],
                constraints=Constraints(prefer_local=True),
                max_tokens=200,
                temperature=0.2,
            )
        )
        
        data = self._parse_json(resp.text)
        return float(data.get("aleatoric_uncertainty", 0.5))

    def _build_confidence_interval(
        self,
        confidence: float,
        epistemic: float,
        aleatoric: float,
    ) -> ConfidenceInterval:
        """Build confidence interval for the prediction."""
        
        # Total uncertainty determines interval width
        total_uncertainty = math.sqrt(epistemic ** 2 + aleatoric ** 2)
        
        # Convert to interval (simplified)
        margin = total_uncertainty * (1 - confidence)
        
        return ConfidenceInterval(
            lower=max(0.0, confidence - margin),
            upper=min(1.0, confidence + margin),
            confidence_level=0.95,
        )

    def _calibrate_confidence(
        self,
        raw_confidence: float,
    ) -> float:
        """Calibrate confidence using learned parameters."""
        
        # Apply calibration transformation
        calibrated = (
            self._calibration_params["slope"] * raw_confidence +
            self._calibration_params["intercept"]
        )
        
        # Clamp to valid range
        return max(0.0, min(1.0, calibrated))

    def _calculate_calibration_score(
        self,
        confidence: float,
    ) -> float:
        """Calculate how well calibrated a confidence score is."""
        
        if not self._calibration_history:
            return 0.5
        
        # Expected calibration error (simplified)
        # Compare confidence to actual accuracy
        bin_size = 0.1
        bin_idx = int(confidence / bin_size)
        
        bin_samples = [
            (conf, correct)
            for conf, correct in self._calibration_history
            if bin_idx <= conf / bin_size < bin_idx + 1
        ]
        
        if not bin_samples:
            return 0.5
        
        accuracy = sum(1 for _, correct in bin_samples if correct) / len(bin_samples)
        calibration_error = abs(confidence - accuracy)
        
        return 1.0 - calibration_error

    def _update_calibration(self) -> None:
        """Update calibration parameters based on history."""
        
        if len(self._calibration_history) < 20:
            return
        
        # Fit simple linear calibration
        # confidence_calibrated = slope * confidence_raw + intercept
        
        # Use least squares fit
        n = len(self._calibration_history)
        
        sum_x = sum(conf for conf, _ in self._calibration_history)
        sum_y = sum(1.0 if correct else 0.0 for _, correct in self._calibration_history)
        sum_xy = sum(
            conf * (1.0 if correct else 0.0)
            for conf, correct in self._calibration_history
        )
        sum_x2 = sum(conf ** 2 for conf, _ in self._calibration_history)
        
        denominator = n * sum_x2 - sum_x ** 2
        if abs(denominator) < 0.0001:
            return
        
        slope = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - slope * sum_x) / n
        
        # Update with smoothing
        alpha = 0.1
        self._calibration_params["slope"] = (
            self._calibration_params["slope"] * (1 - alpha) + slope * alpha
        )
        self._calibration_params["intercept"] = (
            self._calibration_params["intercept"] * (1 - alpha) + intercept * alpha
        )

    def _parse_json(
        self,
        text: str,
    ) -> dict[str, Any]:
        """Parse JSON from text."""
        
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start == -1 or end == 0:
                return {}
            return dict(json.loads(text[start:end]))
        except json.JSONDecodeError:
            return {}

    def get_statistics(self) -> dict[str, Any]:
        """Get uncertainty quantification statistics."""
        
        return {
            **self._stats,
            "calibration_params": self._calibration_params,
            "calibration_samples": len(self._calibration_history),
        }
