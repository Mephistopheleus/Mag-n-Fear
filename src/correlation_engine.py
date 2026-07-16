"""
CorrelationEngine Module
Calculates correlation matrices (Pearson, Spearman, Kendall) and Beta coefficients
for a basket of assets against base markers (BTC, ETH).
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from scipy.stats import pearsonr, spearmanr, kendalltau

class CorrelationEngine:
    def __init__(self, config: dict):
        self.config = config
        self.base_assets = config.get('correlation_base_assets', ['BTCUSDT', 'ETHUSDT'])
        self.target_assets = config.get('correlation_target_assets', [
            'SOLUSDT', 'ARBUSDT', 'FETUSDT', 'ONDOUSDT', 'DOGEUSDT'
        ])
        self.history_depth = config.get('history_depth', 500)
        
        # Storage for price data
        self.price_history: Dict[str, pd.Series] = {}
        
    def update_price(self, symbol: str, price: float, timestamp: int):
        """Update price history for a specific symbol."""
        if symbol not in self.price_history:
            self.price_history[symbol] = pd.Series(dtype=float)
        
        self.price_history[symbol] = pd.concat([
            self.price_history[symbol], 
            pd.Series([price], index=[timestamp])
        ]).tail(self.history_depth)

    def _get_returns(self, symbol: str) -> Optional[pd.Series]:
        """Calculate log returns for a symbol."""
        if symbol not in self.price_history or len(self.price_history[symbol]) < 2:
            return None
        prices = self.price_history[symbol]
        return np.log(prices / prices.shift(1)).dropna()

    def calculate_correlation_matrix(self, method: str = 'pearson') -> pd.DataFrame:
        """
        Calculate correlation matrix for all tracked assets.
        Methods: 'pearson', 'spearman', 'kendall'
        """
        assets = self.base_assets + self.target_assets
        valid_assets = []
        returns_data = []

        for asset in assets:
            rets = self._get_returns(asset)
            if rets is not None and len(rets) > 10:
                valid_assets.append(asset)
                returns_data.append(rets.values)

        if len(returns_data) < 2:
            return pd.DataFrame()

        matrix = np.zeros((len(valid_assets), len(valid_assets)))
        
        for i, ret_i in enumerate(returns_data):
            for j, ret_j in enumerate(returns_data):
                if i == j:
                    matrix[i, j] = 1.0
                else:
                    try:
                        if method == 'pearson':
                            corr, _ = pearsonr(ret_i, ret_j)
                        elif method == 'spearman':
                            corr, _ = spearmanr(ret_i, ret_j)
                        elif method == 'kendall':
                            corr, _ = kendalltau(ret_i, ret_j)
                        else:
                            corr = 0.0
                        matrix[i, j] = corr
                    except Exception:
                        matrix[i, j] = 0.0

        return pd.DataFrame(matrix, index=valid_assets, columns=valid_assets)

    def calculate_beta(self, asset: str, benchmark: str = 'BTCUSDT') -> Optional[float]:
        """
        Calculate Beta coefficient of an asset relative to a benchmark.
        Beta = Covariance(Asset, Benchmark) / Variance(Benchmark)
        """
        asset_ret = self._get_returns(asset)
        bench_ret = self._get_returns(benchmark)

        if asset_ret is None or bench_ret is None:
            return None
        
        # Align indices
        common_idx = asset_ret.index.intersection(bench_ret.index)
        if len(common_idx) < 10:
            return None
            
        a = asset_ret.loc[common_idx]
        b = bench_ret.loc[common_idx]
        
        covariance = np.cov(a, b)[0, 1]
        variance = np.var(b)
        
        if variance == 0:
            return 0.0
            
        return covariance / variance

    def get_correlation_signals(self) -> Dict[str, Dict[str, float]]:
        """
        Analyze correlations and generate signals.
        Returns a dictionary with signal strength and direction.
        """
        signals = {}
        
        # Calculate Pearson for general trend
        pearson_mat = self.calculate_correlation_matrix('pearson')
        if pearson_mat.empty:
            return signals

        for target in self.target_assets:
            if target not in pearson_mat.index:
                continue
                
            btc_corr = pearson_mat.loc[target, 'BTCUSDT'] if 'BTCUSDT' in pearson_mat.columns else 0
            eth_corr = pearson_mat.loc[target, 'ETHUSDT'] if 'ETHUSDT' in pearson_mat.columns else 0
            
            beta_btc = self.calculate_beta(target, 'BTCUSDT') or 0
            
            signals[target] = {
                'btc_correlation': btc_corr,
                'eth_correlation': eth_corr,
                'beta_btc': beta_btc,
                'divergence_score': abs(btc_corr - eth_corr) # High score means potential sector rotation
            }
            
        return signals
