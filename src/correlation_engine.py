"""
CorrelationEngine Module - Мега-коррелятор
Calculates correlation matrices (Pearson, Spearman, Kendall, Distance) and Beta coefficients
for a basket of assets against base markers (BTC, ETH).
Использует все доступные методы корреляции для повышения точности прогнозов.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from scipy.stats import pearsonr, spearmanr, kendalltau
from scipy.spatial.distance import cdist

class CorrelationEngine:
    def __init__(self, config: dict):
        self.config = config
        # Пул монет согласно требованиям
        self.base_assets = config.get('correlation_base_assets', ['BTCUSDT', 'ETHUSDT'])
        self.target_assets = config.get('correlation_target_assets', [
            'SOLUSDT', 'BNBUSDT', 'DOGEUSDT', 'LINKUSDT', 'ETHUSDT'
        ])
        self.history_depth = config.get('history_depth', 500)
        
        # Storage for price data with timestamps
        self.price_history: Dict[str, pd.Series] = {}
        self.timestamp_history: Dict[str, pd.Series] = {}
        
    def update_price(self, symbol: str, price: float, timestamp: int):
        """Update price history for a specific symbol with timestamp synchronization."""
        if symbol not in self.price_history:
            self.price_history[symbol] = pd.Series(dtype=float)
            self.timestamp_history[symbol] = pd.Series(dtype=int)
        
        # Добавляем цену и timestamp
        self.price_history[symbol] = pd.concat([
            self.price_history[symbol], 
            pd.Series([price], index=[timestamp])
        ]).tail(self.history_depth)
        
        self.timestamp_history[symbol] = pd.concat([
            self.timestamp_history[symbol],
            pd.Series([timestamp], index=[timestamp])
        ]).tail(self.history_depth)

    def _get_returns(self, symbol: str) -> Optional[pd.Series]:
        """Calculate log returns for a symbol."""
        if symbol not in self.price_history or len(self.price_history[symbol]) < 2:
            return None
        prices = self.price_history[symbol]
        return np.log(prices / prices.shift(1)).dropna()

    def _synchronize_series(self, symbol1: str, symbol2: str) -> Tuple[pd.Series, pd.Series]:
        """Синхронизация двух временных рядов по timestamp для точной корреляции."""
        if symbol1 not in self.price_history or symbol2 not in self.price_history:
            return pd.Series(), pd.Series()
        
        # Получаем общие timestamp
        ts1 = set(self.timestamp_history[symbol1].index)
        ts2 = set(self.timestamp_history[symbol2].index)
        common_ts = sorted(ts1.intersection(ts2))
        
        if len(common_ts) < 10:
            # Если мало общих timestamp, используем интерполяцию
            prices1 = self.price_history[symbol1]
            prices2 = self.price_history[symbol2]
            
            # Создаем общий индекс
            all_ts = sorted(set(prices1.index).union(set(prices2.index)))
            if len(all_ts) < 10:
                return pd.Series(), pd.Series()
            
            # Интерполяция
            p1_interp = prices1.reindex(all_ts).interpolate(method='time').fillna(method='bfill').fillna(method='ffill')
            p2_interp = prices2.reindex(all_ts).interpolate(method='time').fillna(method='bfill').fillna(method='ffill')
            
            return p1_interp, p2_interp
        
        # Используем общие timestamp
        p1 = self.price_history[symbol1].loc[common_ts]
        p2 = self.price_history[symbol2].loc[common_ts]
        
        return p1, p2

    def calculate_correlation_matrix(self, method: str = 'pearson') -> pd.DataFrame:
        """
        Calculate correlation matrix for all tracked assets.
        Methods: 'pearson', 'spearman', 'kendall', 'distance'
        """
        assets = self.base_assets + self.target_assets
        valid_assets = []
        returns_data = {}

        for asset in assets:
            rets = self._get_returns(asset)
            if rets is not None and len(rets) > 10:
                valid_assets.append(asset)
                returns_data[asset] = rets.values

        if len(valid_assets) < 2:
            return pd.DataFrame()

        n = len(valid_assets)
        matrix = np.zeros((n, n))
        
        for i, asset_i in enumerate(valid_assets):
            for j, asset_j in enumerate(valid_assets):
                if i == j:
                    matrix[i, j] = 1.0
                else:
                    try:
                        ret_i = returns_data[asset_i]
                        ret_j = returns_data[asset_j]
                        
                        # Синхронизация данных
                        min_len = min(len(ret_i), len(ret_j))
                        if min_len < 10:
                            matrix[i, j] = 0.0
                            continue
                        
                        ret_i = ret_i[-min_len:]
                        ret_j = ret_j[-min_len:]
                        
                        if method == 'pearson':
                            corr, _ = pearsonr(ret_i, ret_j)
                        elif method == 'spearman':
                            corr, _ = spearmanr(ret_i, ret_j)
                        elif method == 'kendall':
                            corr, _ = kendalltau(ret_i, ret_j)
                        elif method == 'distance':
                            # Дистанционная корреляция
                            corr = self._distance_correlation(ret_i, ret_j)
                        else:
                            corr = 0.0
                        matrix[i, j] = corr
                    except Exception:
                        matrix[i, j] = 0.0

        return pd.DataFrame(matrix, index=valid_assets, columns=valid_assets)
    
    def _distance_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """
        Вычисление дистанционной корреляции (Distance Correlation).
        Обнаруживает нелинейные зависимости между переменными.
        """
        if len(x) != len(y) or len(x) < 2:
            return 0.0
        
        # Центрирование
        x = x - np.mean(x)
        y = y - np.mean(y)
        
        # Матрицы расстояний
        n = len(x)
        A = np.abs(x[:, np.newaxis] - x[np.newaxis, :])
        B = np.abs(y[:, np.newaxis] - y[np.newaxis, :])
        
        # Центрирование матриц расстояний
        A_row_mean = A.mean(axis=0)
        A_col_mean = A.mean(axis=1)
        A_total_mean = A.mean()
        
        B_row_mean = B.mean(axis=0)
        B_col_mean = B.mean(axis=1)
        B_total_mean = B.mean()
        
        A_centered = A - A_row_mean[np.newaxis, :] - A_col_mean[:, np.newaxis] + A_total_mean
        B_centered = B - B_row_mean[np.newaxis, :] - B_col_mean[:, np.newaxis] + B_total_mean
        
        # Статистика V^2
        V_squared = np.sum(A_centered * B_centered) / (n * n)
        
        # Статистики T^2
        T2_x = np.sum(A_centered ** 2) / (n * n)
        T2_y = np.sum(B_centered ** 2) / (n * n)
        
        if T2_x <= 0 or T2_y <= 0:
            return 0.0
        
        # Distance correlation
        dcorr = np.sqrt(V_squared / np.sqrt(T2_x * T2_y))
        
        return max(0.0, min(1.0, dcorr))  # Ограничение [0, 1]

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
        Returns a dictionary with signal strength and direction using ALL correlation methods.
        Интегрирует Pearson, Spearman, Kendall и Distance корреляции для максимальной точности.
        """
        signals = {}
        
        # Calculate all correlation matrices
        pearson_mat = self.calculate_correlation_matrix('pearson')
        spearman_mat = self.calculate_correlation_matrix('spearman')
        kendall_mat = self.calculate_correlation_matrix('kendall')
        distance_mat = self.calculate_correlation_matrix('distance')
        
        if pearson_mat.empty:
            return signals

        for target in self.target_assets:
            if target not in pearson_mat.index:
                continue
            
            # BTC correlations (all methods)
            btc_pearson = pearson_mat.loc[target, 'BTCUSDT'] if 'BTCUSDT' in pearson_mat.columns else 0
            btc_spearman = spearman_mat.loc[target, 'BTCUSDT'] if 'BTCUSDT' in spearman_mat.columns else 0
            btc_kendall = kendall_mat.loc[target, 'BTCUSDT'] if 'BTCUSDT' in kendall_mat.columns else 0
            btc_distance = distance_mat.loc[target, 'BTCUSDT'] if 'BTCUSDT' in distance_mat.columns else 0
            
            # ETH correlations (all methods)
            eth_pearson = pearson_mat.loc[target, 'ETHUSDT'] if 'ETHUSDT' in pearson_mat.columns else 0
            eth_spearman = spearman_mat.loc[target, 'ETHUSDT'] if 'ETHUSDT' in spearman_mat.columns else 0
            eth_kendall = kendall_mat.loc[target, 'ETHUSDT'] if 'ETHUSDT' in kendall_mat.columns else 0
            eth_distance = distance_mat.loc[target, 'ETHUSDT'] if 'ETHUSDT' in distance_mat.columns else 0
            
            # Beta coefficient
            beta_btc = self.calculate_beta(target, 'BTCUSDT') or 0
            beta_eth = self.calculate_beta(target, 'ETHUSDT') or 0
            
            # Integrated correlation score (weighted average of all methods)
            # Pearson: линейная зависимость, Spearman: ранговая, Kendall: согласованность, Distance: нелинейная
            btc_integrated = (
                btc_pearson * 0.3 +      # Линейная корреляция
                btc_spearman * 0.25 +    # Ранговая корреляция
                btc_kendall * 0.2 +      # Согласованность пар
                btc_distance * 0.25      # Нелинейная зависимость
            )
            
            eth_integrated = (
                eth_pearson * 0.3 +
                eth_spearman * 0.25 +
                eth_kendall * 0.2 +
                eth_distance * 0.25
            )
            
            # Confidence based on agreement between methods
            btc_agreement = 1.0 - (abs(btc_pearson - btc_spearman) + abs(btc_pearson - btc_kendall) + abs(btc_pearson - btc_distance)) / 4
            eth_agreement = 1.0 - (abs(eth_pearson - eth_spearman) + abs(eth_pearson - eth_kendall) + abs(eth_pearson - eth_distance)) / 4
            
            signals[target] = {
                # Individual method results
                'btc_pearson': btc_pearson,
                'btc_spearman': btc_spearman,
                'btc_kendall': btc_kendall,
                'btc_distance': btc_distance,
                'eth_pearson': eth_pearson,
                'eth_spearman': eth_spearman,
                'eth_kendall': eth_kendall,
                'eth_distance': eth_distance,
                
                # Integrated scores
                'btc_integrated_correlation': btc_integrated,
                'eth_integrated_correlation': eth_integrated,
                
                # Beta coefficients
                'beta_btc': beta_btc,
                'beta_eth': beta_eth,
                
                # Agreement metrics (how much methods agree)
                'btc_method_agreement': max(0, btc_agreement),
                'eth_method_agreement': max(0, eth_agreement),
                
                # Divergence score (sector rotation indicator)
                'divergence_score': abs(btc_integrated - eth_integrated),
                
                # Overall signal strength
                'signal_strength': abs(btc_integrated) * btc_agreement,
                'signal_direction': 'POSITIVE' if btc_integrated > 0 else 'NEGATIVE'
            }
            
        return signals
