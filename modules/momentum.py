"""
Conversational Momentum-Adaptive Routing (CMAR) - Self-learning system
that studies dialogue flow to optimize speed, reliability, and token use.
"""

import time, threading
from collections import deque
from pathlib import Path

# Fixed-point math configuration (Q10 format: value * 1024 = integer representation)
Q_SHIFT = 10
Q_ONE = 1 << Q_SHIFT  # 1024
Q_MASK = Q_ONE - 1    # 1023

def q_mul(a, b): return (a * b) >> Q_SHIFT
def q_add(a, b): return a + b
def q_sub(a, b): return a - b
def q_to_float(q): return q / Q_ONE
def float_to_q(f): return int(f * Q_ONE)

class ConversationalMomentum:
    def __init__(self):
        # ESCM state (exponentially smoothed conversational momentum)
        # All values in Q10 fixed-point
        self.delta = 0        # Confidence deficit
        self.gamma = 0        # Complexity momentum
        self.sigma = 0        # Specificity momentum
        self.lam = 0          # Latency surplus

        # Welford's algorithm for variance (fixed-point adapted)
        self.n = 0            # Sample count
        self.mean = 0         # Running mean
        self.M2 = 0           # Running variance numerator

        # Circular buffer for recent exchanges (16 entries)
        self.buffer_size = 16
        self.delta_buf = deque(maxlen=self.buffer_size)
        self.gamma_buf = deque(maxlen=self.buffer_size)
        self.sigma_buf = deque(maxlen=self.buffer_size)
        self.lam_buf = deque(maxlen=self.buffer_size)

        # Configuration (Q10 format)
        self.alpha_min = float_to_q(0.1)   # Minimum smoothing factor
        self.alpha_max = float_to_q(0.4)   # Maximum smoothing factor
        self.beta = float_to_q(0.05)       # Volatility sensitivity

        # Thresholds for background learning decisions (Q10)
        self.theta_delta = float_to_q(0.3)   # Confusion threshold
        self.theta_gamma = float_to_q(0.2)   # Complexity stability threshold
        self.theta_sigma = float_to_q(0.25)  # Specificity threshold
        self.theta_lam = float_to_q(0.15)    # Latency surplus threshold

        # Adaptation gains (Q10 format)
        self.k1 = float_to_q(0.15)   # Complexity -> LLM threshold adjustment
        self.k2 = float_to_q(0.1)    # Specificity -> memory depth adjustment
        self.k3 = float_to_q(0.05)   # Latency surplus -> trust adjustment

        self._lock = threading.Lock()
        self.base_tau = float_to_q(0.35)  # Base LLM invocation threshold

    def update_exchange(self, confidence, complexity, specificity, latency, expected_latency):
        """
        Update momentum metrics from a completed exchange.
        All inputs should be in 0-1000 range (representing 0.0-1.0 scaled by 1024).
        """
        with self._lock:
            # Convert inputs to Q10 if they aren't already
            C = confidence if confidence < Q_ONE else confidence // Q_ONE
            H = complexity if complexity < Q_ONE else complexity // Q_ONE
            S = specificity if specificity < Q_ONE else specificity // Q_ONE
            L = latency if latency < Q_ONE else latency // Q_ONE
            L_hat = expected_latency if expected_latency < Q_ONE else expected_latency // Q_ONE

            # Calculate layer confidence (inverse of deficit)
            layer_confidence = C  # Already 0-1000

            # Update ESCM metrics
            # α = α_min + (α_max - α_min) * (1 - e^(-β·ν))
            # Simplified: α increases with volatility
            alpha = self._calculate_adaptive_alpha()

            # δₜ = α·(1 - Cₜ) + (1-α)·δₜ₋₁
            one_minus_c = q_sub(Q_ONE, C)
            self.delta = q_add(q_mul(alpha, one_minus_c), q_mul(q_sub(Q_ONE, alpha), self.delta))

            # γₜ = α·Hₜ + (1-α)·γₜ₋₁
            self.gamma = q_add(q_mul(alpha, H), q_mul(q_sub(Q_ONE, alpha), self.gamma))

            # σₜ = α·Sₜ + (1-α)·σₜ₋₁
            self.sigma = q_add(q_mul(alpha, S), q_mul(q_sub(Q_ONE, alpha), self.sigma))

            # λₜ = α·(Lₜ - L̂ₜ) + (1-α)·λₜ₋₁
            latency_surplus = q_sub(L, L_hat)
            self.lam = q_add(q_mul(alpha, latency_surplus), q_mul(q_sub(Q_ONE, alpha), self.lam))

            # Store in circular buffers
            self.delta_buf.append(self.delta)
            self.gamma_buf.append(self.gamma)
            self.sigma_buf.append(self.sigma)
            self.lam_buf.append(self.lam)

            # Update running statistics (Welford's algorithm for variance)
            self._update_statistics(layer_confidence)

    def _calculate_adaptive_alpha(self):
        """Calculate adaptive smoothing factor based on volatility"""
        if self.n < 2:
            return self.alpha_max  # Start with high reactivity

        # Calculate variance (simplified fixed-point)
        variance = self.M2 // max(self.n - 1, 1) if self.n > 1 else 0
        volatility = variance  # In Q10 format

        # α = α_min + (α_max - α_min) * (1 - e^(-β·ν))
        # Linear approximation for small values: 1 - e^(-x) ≈ x for x < 1.5
        beta_nu = q_mul(self.beta, volatility)
        if beta_nu > float_to_q(1.5):  # Clamp for stability
            beta_nu = float_to_q(1.5)
        one_minus_exp = q_sub(Q_ONE, beta_nu)  # Approximation

        alpha_range = q_sub(self.alpha_max, self.alpha_min)
        alpha = q_add(self.alpha_min, q_mul(alpha_range, one_minus_exp))
        return alpha

    def _update_statistics(self, value):
        """Update running mean and variance (Welford's algorithm)"""
        self.n += 1
        delta = q_sub(value, self.mean)
        self.mean = q_add(self.mean, q_mul(delta, self.n // self.n if self.n > 0 else 0))
        # Simplified variance update for fixed-point
        delta2 = q_sub(value, self.mean)
        self.M2 = q_add(self.M2, q_mul(delta, delta2))

    def get_tau_adjustment(self):
        """
        Get adjustment to LLM invocation threshold τ based on momentum.
        Returns value in Q10 format to be SUBTRACTED from base_tau.
        Higher return = lower threshold = more likely to use LLM.
        """
        with self._lock:
            # τ = τ_base
            #     - k₁·max(0, γ - γ₀)      // High complexity → engage LLM sooner
            #     + k₂·min(0, σ - σ₀)      // High specificity → rely more on memory
            #     - k₃·λ                   // Negative latency surplus → trust predictions more

            gamma_term = 0
            if self.gamma > float_to_q(0.4):  # γ₀ = 0.4
                gamma_excess = q_sub(self.gamma, float_to_q(0.4))
                gamma_term = q_mul(self.k1, gamma_excess)

            sigma_term = 0
            if self.sigma < float_to_q(0.2):  # σ₀ = 0.2
                sigma_deficit = q_sub(self.sigma, float_to_q(0.2))  # Negative value
                sigma_term = q_mul(self.k2, sigma_deficit)  # k₂ * negative = negative adjustment

            lambda_term = q_mul(self.k3, self.lam)  # -k₃·λ, so if λ negative, term becomes positive

            adjustment = q_sub(q_sub(gamma_term, sigma_term), lambda_term)
            return adjustment

    def get_specificity_bonus(self):
        """Get bonus to add to memory retrieval depth (top-κ) based on specificity momentum"""
        with self._lock:
            # effective_k = base_k + int(3 * tanh(σ/50))
            # Simplified: bonus = 0-3 based on sigma
            if self.sigma > float_to_q(0.8):  # High specificity
                return 3
            elif self.sigma > float_to_q(0.5):  # Medium specificity
                return 2
            elif self.sigma > float_to_q(0.3):  # Low specificity
                return 1
            else:
                return 0

    def should_intensify_background_learning(self):
        """Determine if background learning should be intensified based on momentum trends"""
        with self._lock:
            if len(self.delta_buf) < 4:
                return False

            # Check if confusion is rising: mean(δ) > θ_δ AND trend(δ) > 0
            recent_deltas = list(self.delta_buf)[-4:]
            mean_delta = sum(recent_deltas) // len(recent_deltas)

            # Simple trend: last vs first of recent 4
            trend_positive = recent_deltas[-1] > recent_deltas[0]

            return (mean_delta > self.theta_delta) and trend_positive

    def get_heuristic_rule_scale(self):
        """Get scaling factor for heuristic engine rule evaluation depth"""
        with self._lock:
            # Active rules = R₀ · (1 - tanh(γ/500))
            # Simplified: returns 0.0-1.0 scale factor
            if self.gamma > float_to_q(0.8):  # High complexity
                return float_to_q(0.3)  # Use only 30% of rules
            elif self.gamma > float_to_q(0.5):  # Medium complexity
                return float_to_q(0.6)  # Use 60% of rules
            elif self.gamma > float_to_q(0.3):  # Low-medium complexity
                return float_to_q(0.8)  # Use 80% of rules
            else:  # Low complexity
                return Q_ONE  # Use 100% of rules

    def get_status(self):
        """Get current momentum status for debugging/monitoring"""
        with self._lock:
            return {
                'delta': q_to_float(self.delta),
                'gamma': q_to_float(self.gamma),
                'sigma': q_to_float(self.sigma),
                'lam': q_to_float(self.lam),
                'tau_adjustment': q_to_float(self.get_tau_adjustment()),
                'specificity_bonus': self.get_specificity_bonus(),
                'heuristic_scale': q_to_float(self.get_heuristic_rule_scale()),
                'should_intensify': self.should_intensify_background_learning()
            }

# Global instance
momentum = ConversationalMomentum()