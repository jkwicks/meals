"""
The following module provides standalone implementations for state-space Kalman filtering, Holt's linear trend smoothing, dynamic TDEE calculation, and Alpert-bounded adaptive deficit stepping.
deterministic_nutrition_engine.py
Modular bioenergetic and state-space nutrition calculations.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple
import numpy as np


# ----------------------------------------------------------------------
# 1. State-Space Weight Filtering (Kalman Filter)
# ----------------------------------------------------------------------

class KalmanWeightSmoother:
    """
    Two-state Kinematic Kalman Filter tracking true somatic body weight (w)
    and continuous rate of mass change / velocity (v = dw/dt in kg/day).
    
    Robust to missing observations (NaN / None) via state uncertainty propagation.
    """
    def __init__(
        self,
        initial_weight: float,
        process_noise_std: float = 0.05,      # Somatic tissue change variance (kg/day^2)
        measurement_noise_std: float = 0.75,  # Acute water/glycogen noise std (kg)
    ) -> None:
        # State vector: [mass (kg), velocity (kg/day)]^T
        self.x = np.array([[initial_weight], [0.0]], dtype=np.float64)
        
        # State estimation covariance matrix P
        self.P = np.diag([measurement_noise_std**2, 0.05**2]).astype(np.float64)
        
        self.q_std = process_noise_std
        self.r_std = measurement_noise_std
        self.R = np.array([[measurement_noise_std**2]], dtype=np.float64)
        self.H = np.array([[1.0, 0.0]], dtype=np.float64)
        self.I = np.eye(2, dtype=np.float64)

    def update(self, dt: float, measurement: Optional[float]) -> Tuple[float, float, float]:
        """
        Executes prediction and conditional observation update over time step dt (days).
        
        Returns:
            Tuple of (smoothed_weight_kg, velocity_kg_per_day, estimation_variance)
        """
        # State Transition Matrix F
        F = np.array([[1.0, dt],
                      [0.0, 1.0]], dtype=np.float64)
        
        # Continuous white noise acceleration process noise covariance Q
        q = self.q_std**2
        Q = q * np.array([[ (dt**3) / 3.0, (dt**2) / 2.0 ],
                          [ (dt**2) / 2.0,  dt          ]], dtype=np.float64)
        
        # --- PREDICTION STEP ---
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        
        # --- CORRECTION / MEASUREMENT STEP ---
        if measurement is not None and not np.isnan(measurement):
            z = np.array([[measurement]], dtype=np.float64)
            y = z - (self.H @ self.x)                 # Innovation
            S = self.H @ self.P @ self.H.T + self.R    # Innovation covariance
            K = self.P @ self.H.T @ np.linalg.inv(S)   # Optimal Kalman Gain
            
            self.x = self.x + (K @ y)
            self.P = (self.I - (K @ self.H)) @ self.P
            
        smoothed_weight = float(self.x[0, 0])
        velocity = float(self.x[1, 0])
        variance = float(self.P[0, 0])
        
        return smoothed_weight, velocity, variance


# ----------------------------------------------------------------------
# 2. Holt-Winters Linear Exponential Weight Smoother
# ----------------------------------------------------------------------

def holt_linear_weight_smoothing(
    measurements: List[Optional[float]],
    alpha: float = 0.2,
    beta: float = 0.1,
) -> List[Tuple[float, float]]:
    """
    Two-parameter Holt's Linear Trend Exponential Smoothing.
    
    Returns:
        List of (smoothed_level, smoothed_trend_per_day) for each observation index.
    """
    valid_indices = [i for i, m in enumerate(measurements) if m is not None and not np.isnan(m)]
    if not valid_indices:
        return []
    
    # Initialize level and trend
    first_idx = valid_indices[0]
    level = float(measurements[first_idx])
    
    if len(valid_indices) > 1:
        second_idx = valid_indices[1]
        trend = (float(measurements[second_idx]) - level) / (second_idx - first_idx)
    else:
        trend = 0.0
        
    results: List[Tuple[float, float]] = []
    
    for m in measurements:
        if m is not None and not np.isnan(m):
            prev_level = level
            level = alpha * m + (1.0 - alpha) * (level + trend)
            trend = beta * (level - prev_level) + (1.0 - beta) * trend
        else:
            level = level + trend
            trend = trend
            
        results.append((level, trend))
        
    return results


# ----------------------------------------------------------------------
# 3. Dynamic TDEE & Adaptive Deficit Allocation Engine
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class MacroTargets:
    calories: float
    protein_g: float
    net_carbs_g: float
    fat_g: float
    dynamic_tdee: float
    applied_deficit: float
    alpert_deficit_ceiling: float


class DynamicNutritionEngine:
    KCAL_PER_KG_TISSUE: float = 7700.0
    ALPERT_KCAL_PER_KG_FAT: float = 69.3

    @staticmethod
    def calculate_bmr(
        weight_kg: float,
        height_cm: float,
        age: int,
        gender: str = "male",
        body_fat_pct: Optional[float] = None,
    ) -> float:
        """
        Computes Basal Metabolic Rate using Katch-McArdle (if LBM is available)
        or Mifflin-St Jeor (fallback).
        """
        if body_fat_pct is not None and 0.0 < body_fat_pct < 60.0:
            lbm_kg = weight_kg * (1.0 - (body_fat_pct / 100.0))
            return 370.0 + (21.6 * lbm_kg)
        
        if gender.lower() == "male":
            return (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age) + 5.0
        return (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age) - 161.0

    @classmethod
    def calculate_adaptive_tdee(
        cls,
        daily_calories_logged: List[float],
        smoothed_weight_series: List[float],
        window_days: int = 14,
    ) -> Optional[float]:
        """
        Thermodynamic Adaptive Expenditure Calculation.
        Inverts energy conservation over a rolling retrospective horizon:
        TDEE = Mean_Intake - (Delta_Smoothed_Weight * 7700 / Window_Days)
        """
        if len(daily_calories_logged) < window_days or len(smoothed_weight_series) < window_days:
            return None
            
        intake_window = daily_calories_logged[-window_days:]
        mean_intake = float(np.mean(intake_window))
        
        delta_weight_kg = smoothed_weight_series[-1] - smoothed_weight_series[-window_days]
        somatic_energy_flux_per_day = (delta_weight_kg * cls.KCAL_PER_KG_TISSUE) / float(window_days)
        
        empirical_tdee = mean_intake - somatic_energy_flux_per_day
        return float(np.clip(empirical_tdee, 1000.0, 5000.0))

    @classmethod
    def calculate_safe_deficit(
        cls,
        current_weight_kg: float,
        target_weight_kg: float,
        body_fat_pct: Optional[float] = None,
        max_deficit_ceiling: float = 750.0,
        min_deficit_floor: float = 350.0,
        alpert_safety_factor: float = 0.80,
    ) -> Tuple[float, float]:
        """
        Scales deficit based on goal proximity and clamps to Alpert's lipolytic ceiling.
        
        Returns:
            Tuple of (programmed_safe_deficit, theoretical_alpert_limit)
        """
        weight_ceiling_ref = target_weight_kg + 20.0
        gap_ratio = (current_weight_kg - target_weight_kg) / max(1.0, (weight_ceiling_ref - target_weight_kg))
        clamped_ratio = float(np.clip(gap_ratio, 0.0, 1.0))
        
        proximity_deficit = min_deficit_floor + (max_deficit_ceiling - min_deficit_floor) * clamped_ratio
        
        if body_fat_pct is not None and body_fat_pct > 0.0:
            fat_mass_kg = current_weight_kg * (body_fat_pct / 100.0)
            alpert_limit = fat_mass_kg * cls.ALPERT_KCAL_PER_KG_FAT
            safe_alpert_ceiling = alpert_limit * alpert_safety_factor
        else:
            fat_mass_kg = current_weight_kg * 0.20
            alpert_limit = fat_mass_kg * cls.ALPERT_KCAL_PER_KG_FAT
            safe_alpert_ceiling = alpert_limit * alpert_safety_factor
            
        final_deficit = min(proximity_deficit, safe_alpert_ceiling)
        return float(round(final_deficit, 1)), float(round(alpert_limit, 1))

    @classmethod
    def generate_daily_macros(
        cls,
        current_weight_kg: float,
        target_weight_kg: float,
        height_cm: float,
        age: int,
        gender: str,
        body_fat_pct: Optional[float],
        daily_calories_history: List[float],
        raw_weigh_in_history: List[Optional[float]],
        net_carbs_target_g: float = 60.0,
        protein_multiplier: float = 1.8,
        activity_multiplier: float = 1.375,
    ) -> MacroTargets:
        """
        Unified pipeline: Filters scale history via Kalman filtering, derives adaptive TDEE,
        computes Alpert-guarded deficit stepping, and deterministically allocates macros.
        """
        smoother = KalmanWeightSmoother(initial_weight=raw_weigh_in_history[0] or current_weight_kg)
        smoothed_weights: List[float] = []
        for w in raw_weigh_in_history:
            sw, _, _ = smoother.update(dt=1.0, measurement=w)
            smoothed_weights.append(sw)
            
        active_smoothed_weight = smoothed_weights[-1]

        bmr_baseline = cls.calculate_bmr(active_smoothed_weight, height_cm, age, gender, body_fat_pct)
        static_tdee_prior = bmr_baseline * activity_multiplier
        
        adaptive_tdee = cls.calculate_adaptive_tdee(
            daily_calories_history, smoothed_weights, window_days=14
        )
        
        if adaptive_tdee is None:
            active_tdee = static_tdee_prior
        else:
            data_points = min(len(daily_calories_history), len(raw_weigh_in_history))
            blend_weight = float(np.clip((data_points - 14) / 14.0, 0.0, 1.0))
            active_tdee = ((1.0 - blend_weight) * static_tdee_prior) + (blend_weight * adaptive_tdee)

        programmed_deficit, alpert_max = cls.calculate_safe_deficit(
            active_smoothed_weight, target_weight_kg, body_fat_pct
        )
        
        target_calories = max(1200.0, active_tdee - programmed_deficit)

        # Protein locked permanently to target body weight
        protein_g = target_weight_kg * protein_multiplier
        protein_kcal = protein_g * 4.0
        
        # Carbs allocated
        carbs_g = net_carbs_target_g
        carbs_kcal = carbs_g * 4.0
        
        # Fat derived as elastic buffer
        remaining_kcal = target_calories - (protein_kcal + carbs_kcal)
        fat_g = max(20.0, remaining_kcal / 9.0)
        
        reconciled_calories = round((protein_g * 4.0) + (carbs_g * 4.0) + (fat_g * 9.0), 1)

        return MacroTargets(
            calories=reconciled_calories,
            protein_g=round(protein_g, 1),
            net_carbs_g=round(carbs_g, 1),
            fat_g=round(fat_g, 1),
            dynamic_tdee=round(active_tdee, 1),
            applied_deficit=round(active_tdee - reconciled_calories, 1),
            alpert_deficit_ceiling=alpert_max,
        )


# ----------------------------------------------------------------------
# 4. Verification and Execution Pipeline
# ----------------------------------------------------------------------

if __name__ == "__main__":
    np.random.seed(42)
    days = 30
    
    # Simulate true somatic weight trajectory: 100 kg down to 97 kg over 30 days
    true_weight = 100.0 - (3.0 * (np.arange(days) / days))
    
    # Introduce stochastic fluid/sodium noise (+/- 0.75 kg) and missing logging days
    measured_weight: List[Optional[float]] = []
    for w in true_weight + np.random.normal(0, 0.75, days):
        measured_weight.append(float(w))
    measured_weight[4] = None
    measured_weight[11] = None
    measured_weight[18] = None
    measured_weight[25] = None

    # Simulate true maintenance of 2500 kcal with an actual intake of 1730 kcal/day
    simulated_intake = [1730.0 + float(np.random.normal(0, 50)) for _ in range(days)]

    targets = DynamicNutritionEngine.generate_daily_macros(
        current_weight_kg=measured_weight[-1] or 97.0,
        target_weight_kg=80.0,
        height_cm=178.0,
        age=55,
        gender="male",
        body_fat_pct=26.0,
        daily_calories_history=simulated_intake,
        raw_weigh_in_history=measured_weight,
        net_carbs_target_g=60.0,
        protein_multiplier=1.8,
    )

    print("=== DETERMINISTIC ADAPTIVE NUTRITION REPORT ===")
    print(f"Calculated Dynamic TDEE    : {targets.dynamic_tdee} kcal/day")
    print(f"Alpert Fat Loss Ceiling    : {targets.alpert_deficit_ceiling} kcal/day")
    print(f"Programmed Safe Deficit    : {targets.applied_deficit} kcal/day")
    print(f"Prescribed Daily Budget    : {targets.calories} kcal/day")
    print(f"Macro Breakdown (P / C / F): {targets.protein_g}g P | {targets.net_carbs_g}g C | {targets.fat_g}g F")