"""
ScenarioLab Module
Performs "What-If" analysis on rejected scenarios to find optimal parameters.
Simulates variations of entry/exit conditions and reports results to AutoTuner.
"""
import numpy as np
import copy
from typing import Dict, List, Optional

class ScenarioLab:
    def __init__(self, config: dict):
        self.config = config
        self.max_iterations = config.get('lab_max_iterations', 50)
        self.variation_step = config.get('lab_variation_step', 0.1) # 10% variation
        
    def analyze_scenario(self, scenario: Dict, market_context: Dict) -> List[Dict]:
        """
        Take a rejected scenario and simulate variations.
        Returns a list of "lab snapshots" with modified parameters.
        """
        lab_results = []
        
        base_params = scenario.get('parameters', {})
        entry_price = scenario.get('entry_price', 0)
        direction = scenario.get('direction', 'LONG')
        
        # Define parameters to vary
        params_to_vary = ['stop_loss_pct', 'take_profit_pct', 'leverage', 'confidence_threshold']
        
        for param in params_to_vary:
            if param not in base_params:
                continue
                
            base_val = base_params[param]
            
            # Generate variations: -10%, +10%
            variations = [
                base_val * (1 - self.variation_step),
                base_val * (1 + self.variation_step)
            ]
            
            for var_val in variations:
                # Create modified scenario
                modified_scenario = copy.deepcopy(scenario)
                modified_scenario['parameters'][param] = var_val
                modified_scenario['source'] = 'LAB'
                modified_scenario['variation_of'] = param
                
                # Simulate outcome (simplified logic, real logic uses Executor simulation)
                simulated_pnl = self._quick_simulate(modified_scenario, market_context)
                
                snapshot = {
                    'scenario_id': f"lab_{scenario.get('id')}_{param}_{var_val}",
                    'original_pnl': scenario.get('simulated_pnl', 0),
                    'modified_pnl': simulated_pnl,
                    'improvement': simulated_pnl - scenario.get('simulated_pnl', 0),
                    'changed_param': param,
                    'new_value': var_val,
                    'old_value': base_val,
                    'market_context': market_context.get('volatility', 0),
                    'recommendation': 'INCREASE' if simulated_pnl > scenario.get('simulated_pnl', 0) else 'DECREASE'
                }
                
                lab_results.append(snapshot)
                
        return lab_results

    def _quick_simulate(self, scenario: Dict, context: Dict) -> float:
        """
        Fast approximation of PnL based on parameter changes.
        In production, this would call the full Executor simulation.
        """
        # Simplified logic: 
        # Wider SL -> lower chance of hit but larger loss
        # Wider TP -> lower chance of hit but larger gain
        # This is a placeholder for the real simulation engine
        
        base_pnl = scenario.get('simulated_pnl', 0)
        volatility = context.get('volatility', 0.01)
        
        sl_pct = scenario['parameters'].get('stop_loss_pct', 0.02)
        tp_pct = scenario['parameters'].get('take_profit_pct', 0.04)
        
        # Heuristic: if volatility is high, wider stops perform better
        vol_factor = volatility / 0.02 # Normalize around 2% vol
        
        if sl_pct > 0.02 * vol_factor:
            # Wider stop avoided premature exit
            adjustment = base_pnl * 0.1 
        else:
            # Tighter stop got hit by noise
            adjustment = -abs(base_pnl) * 0.2
            
        return base_pnl + adjustment

    def generate_optimization_report(self, snapshots: List[Dict]) -> Dict:
        """
        Aggregate lab results and provide recommendations for AutoTuner.
        """
        if not snapshots:
            return {'status': 'NO_DATA'}
            
        best_improvement = max(snapshots, key=lambda x: x['improvement'])
        worst_decline = min(snapshots, key=lambda x: x['improvement'])
        
        report = {
            'total_simulations': len(snapshots),
            'best_adjustment': {
                'param': best_improvement['changed_param'],
                'direction': best_improvement['recommendation'],
                'value': best_improvement['new_value'],
                'expected_gain': best_improvement['improvement']
            },
            'worst_adjustment': {
                'param': worst_decline['changed_param'],
                'direction': worst_decline['recommendation'],
                'value': worst_decline['new_value'],
                'expected_loss': worst_decline['improvement']
            },
            'confidence': 0.75 # Base confidence in lab data
        }
        
        return report
