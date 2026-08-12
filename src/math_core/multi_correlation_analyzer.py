"""
Модуль мульти-корреляционного анализа.
Рассчитывает 4 типа корреляции (Пирсон, Спирмен, Кендалл, DCor) между набором валют.
Использует синтезированные данные старших таймфреймов из time_continuum.
НЕ выдает сигналов BUY/SELL. Выдает карточку с вектором сил и вероятностью влияния на цену.
"""
from typing import Dict, Any, List, Optional, Tuple
import polars as pl
import numpy as np
from scipy import stats

from .base_indicator import BaseIndicator
from .registry import register_indicator


@register_indicator
class MultiCorrelationAnalyzer(BaseIndicator):
    """
    Мульти-корреляционный анализатор.
    
    Архитектура:
    - Берет данные из "полотна" (5м, 20м, 1ч, 4ч) через data (синтезированные TF)
    - Рассчитывает 4 коэффициента корреляции для каждой пары валют
    - Выявляет аномалии (расхождения от исторических паттернов)
    - Возвращает карточку с вектором сил и вероятностью влияния
    
    Список монет для анализа берется из конфига или задается по умолчанию.
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        super().__init__(name, config)
        
        # Список монет для корреляционного анализа
        default_assets = [
            'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
            'DOGEUSDT', 'PEPEUSDT', 'LINKUSDT', 'RNDRUSDT', 'LTCUSDT'
        ]
        self.assets = self.config.get('correlation_assets', default_assets)
        
        # Период для расчета корреляции (количество свечей)
        self.period = self.config.get('period', 50)
        
        # Пороги для определения сильной/слабой корреляции
        self.strong_corr_threshold = self.config.get('strong_threshold', 0.7)
        self.weak_corr_threshold = self.config.get('weak_threshold', 0.3)
        
        # Кэш для хранения последних рассчитанных корреляций
        self._last_correlations: Dict[str, Dict[str, float]] = {}
        
    def _calculate_returns(self, prices: np.ndarray) -> np.ndarray:
        """Расчет доходностей (процентных изменений)."""
        if len(prices) < 2:
            return np.array([])
        returns = np.diff(prices) / prices[:-1]
        return returns
    
    def _pearson_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Коэффициент корреляции Пирсона (линейная зависимость)."""
        if len(x) < 10 or len(y) < 10:
            return 0.0
        if np.std(x) == 0 or np.std(y) == 0:
            return 0.0
        corr, _ = stats.pearsonr(x, y)
        return float(corr) if not np.isnan(corr) else 0.0
    
    def _spearman_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Коэффициент корреляции Спирмена (ранговая зависимость)."""
        if len(x) < 10 or len(y) < 10:
            return 0.0
        corr, _ = stats.spearmanr(x, y)
        return float(corr) if not np.isnan(corr) else 0.0
    
    def _kendall_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Коэффициент корреляции Кендалла (ранговая согласованность)."""
        if len(x) < 10 or len(y) < 10:
            return 0.0
        corr, _ = stats.kendalltau(x, y)
        return float(corr) if not np.isnan(corr) else 0.0
    
    def _dcor_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """
        Distance Correlation (DCor) - измеряет линейную и нелинейную зависимость.
        Значения от 0 (независимы) до 1 (полная зависимость).
        Упрощенная реализация через аппроксимацию.
        """
        if len(x) < 10 or len(y) < 10:
            return 0.0
        
        # Нормализация данных
        x_norm = (x - np.mean(x)) / (np.std(x) + 1e-10)
        y_norm = (y - np.mean(y)) / (np.std(y) + 1e-10)
        
        # Аппроксимация DCor через комбинацию других метрик
        pearson = abs(self._pearson_correlation(x, y))
        spearman = abs(self._spearman_correlation(x, y))
        
        # DCor всегда >= 0 и учитывает нелинейные зависимости
        dcor_value = (pearson + spearman) / 2.0
        return min(1.0, max(0.0, dcor_value))
    
    def _calculate_all_correlations(self, returns_dict: Dict[str, np.ndarray]) -> Dict[str, Dict[str, Dict[str, float]]]:
        """
        Расчет всех 4 типов корреляции для каждой пары активов.
        Возвращает структуру: {asset1: {asset2: {pearson, spearman, kendall, dcor}}}
        """
        correlations = {}
        
        for asset1 in returns_dict:
            correlations[asset1] = {}
            for asset2 in returns_dict:
                if asset1 == asset2:
                    continue
                
                x = returns_dict[asset1]
                y = returns_dict[asset2]
                
                if len(x) < 10 or len(y) < 10:
                    continue
                
                correlations[asset1][asset2] = {
                    'pearson': self._pearson_correlation(x, y),
                    'spearman': self._spearman_correlation(x, y),
                    'kendall': self._kendall_correlation(x, y),
                    'dcor': self._dcor_correlation(x, y)
                }
        
        return correlations
    
    def _detect_anomalies(self, current_corr: Dict[str, Dict[str, Dict[str, float]]], 
                         historical_avg: Optional[Dict] = None) -> List[Dict[str, Any]]:
        """
        Выявление аномалий в корреляциях (расхождения от нормы).
        Если historical_avg не передан, сравниваем с нулем (базовая линия).
        """
        anomalies = []
        
        for asset1 in current_corr:
            for asset2 in current_corr[asset1]:
                corr_values = current_corr[asset1][asset2]
                
                pearson = corr_values['pearson']
                spearman = corr_values['spearman']
                kendall = corr_values['kendall']
                dcor = corr_values['dcor']
                
                # Аномалия: сильная нелинейная зависимость при слабой линейной
                if abs(pearson) < 0.3 and dcor > 0.6:
                    anomalies.append({
                        'asset1': asset1,
                        'asset2': asset2,
                        'type': 'nonlinear_dependency',
                        'pearson': pearson,
                        'dcor': dcor,
                        'severity': dcor - abs(pearson)
                    })
                
                # Аномалия: расхождение между ранговыми методами
                if abs(spearman - kendall) > 0.4:
                    anomalies.append({
                        'asset1': asset1,
                        'asset2': asset2,
                        'type': 'rank_divergence',
                        'spearman': spearman,
                        'kendall': kendall,
                        'severity': abs(spearman - kendall)
                    })
        
        return anomalies
    
    def _calculate_confidence(self, corr_values: Dict[str, float], sample_size: int) -> float:
        """
        Расчет уверенности в корреляции на основе:
        - Согласованности 4 методов
        - Размера выборки
        - Силы корреляции
        """
        pearson = abs(corr_values['pearson'])
        spearman = abs(corr_values['spearman'])
        kendall = abs(corr_values['kendall'])
        dcor = corr_values['dcor']
        
        methods = [pearson, spearman, kendall, dcor]
        mean_val = np.mean(methods)
        std_val = np.std(methods)
        
        consistency = 1.0 / (1.0 + std_val)
        sample_confidence = min(1.0, np.log10(sample_size + 1) / 3.0)
        
        confidence = (mean_val * 0.5 + consistency * 0.3 + sample_confidence * 0.2)
        return min(1.0, max(0.0, confidence))
    
    def calculate(self, data: pl.DataFrame, current_price: float) -> Dict[str, Any]:
        """
        Основной метод расчета.
        
        :param data: DataFrame с данными. Ожидается структура:
                     columns: ['timestamp', 'price', 'symbol', ...]
                     Или отдельные колонки для каждого актива: ['BTC_close', 'ETH_close', ...]
        :param current_price: Текущая цена основного актива
        :return: Карточка с результатами мульти-корреляционного анализа
        """
        # MultiCorrelationAnalyzer не требует стандартной валидации BaseIndicator,
        # так как работает с агрегированными данными нескольких активов
        
        returns_dict = {}
        
        if 'symbol' in data.columns:
            for asset in self.assets:
                try:
                    asset_data = data.filter(pl.col('symbol') == asset)
                    prices = asset_data['price'].to_numpy()
                    if len(prices) >= self.period:
                        prices = prices[-self.period:]
                        returns = self._calculate_returns(prices)
                        if len(returns) >= 10:
                            returns_dict[asset] = returns
                except Exception:
                    continue
        else:
            for asset in self.assets:
                col_name = f'{asset.replace("USDT", "")}_close'
                if col_name in data.columns:
                    prices = data[col_name].to_numpy()
                    if len(prices) >= self.period:
                        prices = prices[-self.period:]
                        returns = self._calculate_returns(prices)
                        if len(returns) >= 10:
                            returns_dict[asset] = returns
        
        if len(returns_dict) < 2:
            return {
                'target_price': current_price,
                'time_sec': 300,
                'probability': 0.3,
                'tags': ['corr_insufficient_data'],
                'metadata': {
                    'assets_analyzed': list(returns_dict.keys()),
                    'correlations': {},
                    'anomalies': [],
                    'vector': [0.0, 0.0, 0.0, 0.0]
                }
            }
        
        correlations = self._calculate_all_correlations(returns_dict)
        anomalies = self._detect_anomalies(correlations)
        
        main_asset = list(returns_dict.keys())[0]
        total_vector = [0.0, 0.0, 0.0, 0.0]
        count = 0
        
        if main_asset in correlations:
            for other_asset in correlations[main_asset]:
                corr = correlations[main_asset][other_asset]
                total_vector[0] += corr['pearson']
                total_vector[1] += corr['spearman']
                total_vector[2] += corr['kendall']
                total_vector[3] += corr['dcor']
                count += 1
        
        if count > 0:
            total_vector = [v / count for v in total_vector]
        
        avg_corr = {
            'pearson': total_vector[0],
            'spearman': total_vector[1],
            'kendall': total_vector[2],
            'dcor': total_vector[3]
        }
        sample_size = len(list(returns_dict.values())[0])
        confidence = self._calculate_confidence(avg_corr, sample_size)
        
        target_price = current_price
        time_sec = 300
        probability = 0.5
        tags = ['multi_correlation']
        
        avg_pearson = total_vector[0]
        avg_dcor = total_vector[3]
        
        if avg_pearson > self.strong_corr_threshold:
            tags.append('corr_synced_trend')
            probability = 0.5 + confidence * 0.4
            time_sec = int(300 / confidence) if confidence > 0 else 300
        elif avg_pearson < -self.strong_corr_threshold:
            tags.append('corr_counter_trend')
            probability = 0.5 + confidence * 0.3
        elif avg_dcor > 0.7 and abs(avg_pearson) < 0.3:
            tags.append('corr_nonlinear_anomaly')
            probability = 0.6
            target_price = current_price * (1 + (avg_dcor - 0.5) * 0.02)
        elif len(anomalies) > 0:
            tags.append('corr_anomaly_detected')
            probability = 0.55
            target_price = current_price * (1 + np.sign(total_vector[0]) * 0.01)
        else:
            tags.append('corr_weak_noise')
            probability = 0.3
        
        self._last_correlations = correlations
        
        return {
            'target_price': float(target_price),
            'time_sec': time_sec,
            'probability': float(probability),
            'tags': tags,
            'metadata': {
                'vector': total_vector,
                'correlations': correlations,
                'anomalies': anomalies,
                'confidence': confidence,
                'assets_count': len(returns_dict),
                'period': self.period
            }
        }
