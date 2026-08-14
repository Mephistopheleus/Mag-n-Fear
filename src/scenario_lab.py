"""
ScenarioLab Module
Performs "What-If" analysis on rejected scenarios to find optimal parameters.
Uses Gradient Descent to find ideal settings for a specific trade snapshot.
Reports ideal snapshots to AutoTuner.
"""
import numpy as np
import copy
from typing import Dict, List, Optional

class ScenarioLab:
    def __init__(self, config: dict):
        self.config = config
        self.max_iterations = config.get('lab_max_iterations', 50)
        self.learning_rate = config.get('lab_learning_rate', 0.05)
        self.target_metrics = config.get('tuner', {}).get('target_metrics', {
            'target_pnl_pct': 1.0,
            'target_wr': 1.0,
            'target_dd': 0.0
        })

    def analyze_scenario(self, scenario: Dict, market_context: Dict) -> List[Dict]:
        """
        Take a rejected scenario and simulate variations using Gradient Descent.
        Returns a list of "lab snapshots" with optimized parameters.
        """
        lab_results = []
        
        # Run optimization to find ideal parameters for this specific snapshot
        ideal_params = self.optimize_for_snapshot(scenario, market_context)
        
        if ideal_params:
            snapshot = self.generate_ideal_snapshot(scenario, ideal_params, market_context)
            lab_results.append(snapshot)
            
        return lab_results

    def _calculate_loss(self, params: dict, snapshot: dict, context: dict) -> float:
        """
        Loss function: Distance from Ideal (100% PnL, 100% WR, 0% DD).
        Lower is better.
        """
        estimated_pnl = self._estimate_pnl(params, snapshot, context)
        estimated_wr = self._estimate_wr(params, snapshot, context)
        estimated_dd = self._estimate_dd(params, snapshot, context)
        
        target_pnl = self.target_metrics.get('target_pnl_pct', 1.0)
        target_wr = self.target_metrics.get('target_wr', 1.0)
        target_dd = self.target_metrics.get('target_dd', 0.0)
        
        loss = (
            ((estimated_pnl - target_pnl) ** 2) +
            ((estimated_wr - target_wr) ** 2) +
            ((estimated_dd - target_dd) ** 2)
        )
        return loss

    def _estimate_pnl(self, params: dict, snapshot: dict, context: dict) -> float:
        tp = params.get('take_profit_pct', 0.04)
        sl = params.get('stop_loss_pct', 0.02)
        conf = snapshot.get('confidence', 0.5)
        rr = tp / sl if sl > 0 else 0
        return conf * rr * 0.5

    def _estimate_wr(self, params: dict, snapshot: dict, context: dict) -> float:
        sl = params.get('stop_loss_pct', 0.02)
        vol = context.get('volatility', 0.01)
        return min(1.0, sl / (vol * 2))

    def _estimate_dd(self, params: dict, snapshot: dict, context: dict) -> float:
        lev = params.get('leverage', 10)
        sl = params.get('stop_loss_pct', 0.02)
        return min(1.0, (lev * sl) / 0.5)

    def _compute_gradient(self, params: dict, snapshot: dict, context: dict) -> dict:
        """
        Numerically compute gradient for each parameter.
        """
        gradient = {}
        epsilon = 1e-5
        
        current_loss = self._calculate_loss(params, snapshot, context)
        
        for key in ['take_profit_pct', 'stop_loss_pct', 'leverage', 'confidence_threshold']:
            if key not in params:
                continue
                
            params_plus = params.copy()
            params_plus[key] += epsilon
            
            loss_plus = self._calculate_loss(params_plus, snapshot, context)
            
            gradient[key] = (loss_plus - current_loss) / epsilon
            
        return gradient

    def optimize_for_snapshot(self, snapshot: dict, context: dict) -> Optional[dict]:
        """
        Run Gradient Descent to find parameters that minimize loss.
        """
        current_params = snapshot.get('parameters', {}).copy()
        
        defaults = {'take_profit_pct': 0.04, 'stop_loss_pct': 0.02, 'leverage': 10, 'confidence_threshold': 0.5}
        for k, v in defaults.items():
            if k not in current_params:
                current_params[k] = v

        for i in range(self.max_iterations):
            grad = self._compute_gradient(current_params, snapshot, context)
            
            updated = False
            for key, value in grad.items():
                if key in current_params:
                    new_val = current_params[key] - (self.learning_rate * value)
                    if key == 'leverage':
                        new_val = max(1, min(125, new_val))
                    elif key in ['take_profit_pct', 'stop_loss_pct']:
                        new_val = max(0.001, min(0.2, new_val))
                    elif key == 'confidence_threshold':
                        new_val = max(0.1, min(0.99, new_val))
                        
                    if abs(new_val - current_params[key]) > 1e-6:
                        current_params[key] = new_val
                        updated = True
            
            if not updated:
                break
                
        return current_params

    def generate_ideal_snapshot(self, original_scenario: dict, ideal_params: dict, context: dict) -> dict:
        """
        Create a snapshot with ideal parameters found by optimization.
        """
        return {
            'type': 'LAB_IDEAL',
            'source_scenario_id': original_scenario.get('id'),
            'original_params': original_scenario.get('parameters', {}),
            'ideal_params': ideal_params,
            'market_context': context,
            'target_metrics': self.target_metrics,
            'timestamp': original_scenario.get('timestamp', 0)
        }

    def generate_optimization_report(self, snapshots: List[Dict]) -> Dict:
        """
        Aggregate lab results and provide recommendations for AutoTuner.
        """
        if not snapshots:
            return {'status': 'NO_DATA'}
        
        report = {
            'status': 'SUCCESS',
            'total_snapshots': len(snapshots),
            'recommendations': []
        }
        
        for snap in snapshots:
            if snap.get('type') == 'LAB_IDEAL':
                report['recommendations'].append({
                    'scenario_id': snap.get('source_scenario_id'),
                    'ideal_params': snap.get('ideal_params'),
                    'message': 'Optimized parameters found via Gradient Descent'
                })
                
        return report
