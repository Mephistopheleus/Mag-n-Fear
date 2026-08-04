"""
Главный оркестратор (Main Entry Point).
Собирает все модули в единый асинхронный цикл.
"""
import asyncio
import logging
import sys
import os
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импорт конфигурации и утилит
from src.core.config_loader import load_config, get_config
from src.utils.health_check import HealthCheck
from src.utils.notifier import Notifier
from src.utils.data_rotator import DataRotator, setup_rotating_logger

# Импорт модулей данных
from src.data.feed import DataFeed
from src.data.news_aggregator import NewsAggregator

# Импорт математического ядра
from src.math_core.time_continuum import TimeContinuum
from src.math_core.classic_tf import ClassicTF
from src.math_core.market_regime import MarketRegimeDetector
from src.math_core.order_book_sr import OrderBookAnalyzer

# Импорт логики и ядра
from src.core.field import ProbabilityField
from src.logic.scenario_writer import ScenarioWriter
from src.risk.manager import RiskManager
from src.tuner.auto_tuner import AutoTuner
from src.executor import Executor
from src.logic.matrix_analyzer import MatrixAnalyzer
from src.logic.market_synthesizer import MarketSynthesizer, MarketTrend
from src.executor.shadow_dealer import ShadowDealer
from src.correlation_engine import CorrelationEngine
from src.harmonic_analyzer import HarmonicAnalyzer
from src.scenario_lab import ScenarioLab

# Пути к данным
LOG_FILE = "data_storage/bot.log"
DB_PATH = "data_storage/trading_history.db"

# Настраиваем логгер с ротацией
os.makedirs("data_storage", exist_ok=True)
logger = setup_rotating_logger(LOG_FILE, level=logging.DEBUG)

class TradingBot:
    def __init__(self, config_path: str = "configs/config.yaml"):
        logger.info("Initializing Trading Bot...")
        
        # Инициализация ротатора данных
        self.data_rotator = DataRotator(
            log_file=LOG_FILE,
            db_path=DB_PATH
        )
        logger.info("DataRotator initialized with limits: logs=2MB, DB=5MB")
        
        # 1. Загрузка конфига
        self.config = load_config(config_path)
        logger.info("Config loaded.")

        # 2. Инициализация утилит
        self.health = HealthCheck(self.config)
        self.notifier = Notifier(self.config)
        
        # 3. Инициализация общего поля данных (Матрица)
        self.prob_field = ProbabilityField()
        
        # 4. Инициализация модулей данных
        self.feed = DataFeed(self.config, self.prob_field)
        self.news = NewsAggregator(self.config, self.prob_field)
        
        # 5. Инициализация математики
        self.continuum = TimeContinuum(self.config)
        self.classic_tf = ClassicTF(self.config)
        self.regime_detector = MarketRegimeDetector(self.config)
        self.ob_analyzer = OrderBookAnalyzer(self.config)
        
        # 6. Инициализация синтезатора рынка (Мозг системы)
        self.synthesizer = MarketSynthesizer(self.config)
        
        # 7. Инициализация риск-менеджера
        self.risk_manager = RiskManager(self.config, self.prob_field)
        
        # 8. Инициализация матричного анализатора (кросс-валидация)
        self.matrix_analyzer = MatrixAnalyzer()
        
        # 9. Инициализация сценариста
        self.scenario_writer = ScenarioWriter(self.config, self.prob_field, self.risk_manager, self.matrix_analyzer)
        
        # 10. Инициализация тюнера
        self.tuner = AutoTuner(self.config, self.prob_field)
        
        # 11. Инициализация исполнителя
        self.executor = Executor(self.config, self.prob_field, self.risk_manager)
        
        # 12. Инициализация ShadowDealer для теневого просчета
        self.shadow_dealer = ShadowDealer(self.config)
        
        # 13. Инициализация корреляционного движка (для кросс-маркет анализа)
        self.corr_engine = CorrelationEngine(self.config)
        
        # 14. Инициализация гармонического анализатора
        self.harmonic_analyzer = HarmonicAnalyzer(self.config)
        
        # 15. Инициализация Лаборатории сценариев (ScenarioLab)
        self.lab = ScenarioLab(self.config)
        
        # Символы для торговли
        self.symbols = self.config.data.symbols
        
        # Для отслеживания карточек сделок (ШАГ 3: AutoTuner Loop)
        self.last_card_count = 0
        self.last_tuner_run = 0
        self.last_lab_run = 0  # Для отслеживания запуска Лаборатории
        
        logger.info(f"Trading Bot initialized. Symbols: {self.symbols}")

    async def start(self):
        """Запуск основного цикла."""
        logger.info("Starting Trading Bot loop...")
        await self.notifier.notify_status("Bot Started")
        
        # Инициализация DataFeed (клиент Binance)
        await self.feed.start()
        
        # Инициализация клиента Binance в Executor
        await self.executor.start()
        await self.news.start()
        
        # Инициализация поля данных для каждого символа
        for symbol in self.symbols:
            # Получаем начальную цену из фида
            initial_price = await self.feed.get_initial_price(symbol)
            if initial_price:
                await self.prob_field.initialize_symbol(symbol, initial_price)
                logger.info(f"Initialized {symbol} with price {initial_price}")
            else:
                logger.error(f"Failed to get initial price for {symbol}")
        
        # Запуск фоновых задач
        tasks = [
            asyncio.create_task(self._data_feed_loop()),
            asyncio.create_task(self._news_loop()),
            asyncio.create_task(self._risk_analysis_loop()),
            asyncio.create_task(self._trading_decision_loop()),
            asyncio.create_task(self._health_monitor_loop()),
            asyncio.create_task(self._autotuner_loop()),  # ШАГ 3: Автономное обучение
            asyncio.create_task(self._balance_check_loop()),  # ШАГ 4: Сверка балансов
            asyncio.create_task(self._laboratory_loop())  # ШАГ 5: Лаборатория сценариев
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            await self.stop()

    async def stop(self):
        """Корректная остановка с закрытием всех позиций и выставлением стоп-лоссов."""
        logger.info("Stopping components...")
        
        # Сначала останавливаем Executor - он выставит стоп-лоссы по всем активным позициям
        await self.executor.stop()
        
        # Затем останавливаем остальные компоненты
        await self.news.stop()
        await self.notifier.notify_status("Bot Stopped")

    async def _data_feed_loop(self):
        """Цикл получения рыночных данных (WebSocket работает автоматически)."""
        # DataFeed уже запустил WebSocket в background через start()
        # Этот цикл только для периодического обновления REST данных если нужно
        delay = self.config.get('core', {}).get('loop_delay_sec', 5)
        while True:
            try:
                # WebSocket потоки работают автоматически, просто ждем
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error in data feed loop: {e}", exc_info=True)
                await asyncio.sleep(delay)

    async def _news_loop(self):
        """Цикл обновления новостей."""
        delay = 60  # Обновление каждые 60 секунд
        while True:
            try:
                await self.news.run_cycle(self.symbols)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error in news loop: {e}", exc_info=True)
                await asyncio.sleep(delay)

    async def _risk_analysis_loop(self):
        """Цикл анализа рисков."""
        delay = self.config.get('core', {}).get('loop_delay_sec', 1)
        while True:
            try:
                for symbol in self.symbols:
                    await self.risk_manager.analyze_and_update(symbol)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error in risk analysis loop: {e}", exc_info=True)
                await asyncio.sleep(delay)

    async def _trading_decision_loop(self):
        """Основной цикл принятия решений."""
        delay = self.config.get('core', {}).get('loop_delay_sec', 1)
        while True:
            try:
                self.health.heartbeat("trading_decision")
                
                for symbol in self.symbols:
                    # 1. Получаем данные и синтезируем модель рынка
                    ticker = self.feed.get_ticker(symbol)
                    if not ticker:
                        continue
                    current_price = ticker.get('price')
                    if not current_price:
                        continue
                    
                    # Собираем данные для синтезатора
                    matrix_snapshot = self.prob_field.get_matrix_snapshot(symbol)
                    clusters = self.matrix_analyzer.find_clusters(matrix_snapshot, current_price)
                    
                    # Извлекаем analysis_points из кластеров матрицы
                    analysis_points = []
                    for cluster in clusters:
                        # Каждый кластер содержит точки с target_price, probability, time_sec
                        analysis_points.append({
                            'target_price': cluster.get('target_price', current_price),
                            'probability': cluster.get('probability', 0.5),
                            'time_sec': cluster.get('target_time_sec', 300),
                            'pattern_type': cluster.get('pattern_type', 'unknown'),
                            'confidence': cluster.get('confidence', 0.5)
                        })
                    
                    # Обновляем данные в CorrelationEngine для мега-коррелятора
                    timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
                    self.corr_engine.update_price(symbol, current_price, timestamp_ms)
                    
                    # Получаем свежие данные от анализаторов
                    # Берем историю свечей и стакан из DataFeed
                    candles_short = await self.feed.get_candles(symbol, '1m', limit=50) or []
                    candles_mid = await self.feed.get_candles(symbol, '15m', limit=50) or []
                    candles_long = await self.feed.get_candles(symbol, '1h', limit=50) or []
                    
                    # Стакан (если есть в фиде)
                    order_book = self.feed.get_order_book(symbol)
                    
                    # Дисбаланс потока ордеров (если есть)
                    imbalance = 0.0
                    if order_book and 'bids' in order_book and 'asks' in order_book:
                        imbalance = self.ob_analyzer.get_imbalance(
                            order_book['bids'], 
                            order_book['asks'], 
                            depth=10
                        )
                    
                    # Получаем корреляционные сигналы от Мега-коррелятора
                    corr_signals = self.corr_engine.get_correlation_signals()
                    symbol_corr = corr_signals.get(symbol, {})
                    
                    market_data = {
                        'current_price': current_price,
                        'candles_short': candles_short,
                        'candles_mid': candles_mid,
                        'candles_long': candles_long,
                        'history': candles_short + candles_mid,  # Объединяем для расчета уровней
                        'order_book': order_book,  # Передаем стакан в синтезатор
                        'order_flow_imbalance': imbalance,
                        'news_impact': 0.0,
                        # Данные от Мега-коррелятора
                        'correlation_signals': symbol_corr,
                        'btc_integrated_correlation': symbol_corr.get('btc_integrated_correlation', 0),
                        'eth_integrated_correlation': symbol_corr.get('eth_integrated_correlation', 0),
                        'beta_btc': symbol_corr.get('beta_btc', 0),
                        'correlation_agreement': symbol_corr.get('btc_method_agreement', 0)
                    }
                    
                    # Синтезируем единую модель рынка
                    market_model_obj = await self.synthesizer.synthesize(
                        current_price=current_price,
                        analysis_points=analysis_points,  # Теперь передаем точки из матрицы
                        market_data=market_data
                    )
                    
                    # Преобразуем в формат для ScenarioWriter
                    dominant_trend = market_model_obj.get_dominant_trend()
                    trend_map = {
                        MarketTrend.BULLISH: 'BULLISH',
                        MarketTrend.BEARISH: 'BEARISH',
                        MarketTrend.SIDEWAYS: 'SIDEWAYS',
                        MarketTrend.UNKNOWN: 'NEUTRAL'
                    }
                    
                    # Вычисляем силу тренда (на основе согласованности таймфреймов)
                    trends = [market_model_obj.trend_short, market_model_obj.trend_mid, market_model_obj.trend_long]
                    trend_counts = {t: trends.count(t) for t in set(trends)}
                    strength = trend_counts.get(dominant_trend, 0) / len(trends) if trends else 0.5
                    
                    # Ключевые уровни
                    levels = {
                        'support': [l.price for l in market_model_obj.levels if l.type == 'support'],
                        'resistance': [l.price for l in market_model_obj.levels if l.type == 'resistance']
                    }
                    
                    market_model = {
                        'symbol': symbol,
                        'current_price': current_price,
                        'trend': trend_map.get(dominant_trend, 'NEUTRAL'),
                        'strength': strength,
                        'key_levels': levels,
                        'sentiment': market_model_obj.sentiment.aggression,
                        'volatility': market_model_obj.volatility,
                        'clusters': clusters
                    }
                    
                    logger.debug(f"Market Model for {symbol}: Trend={market_model['trend']}, Strength={strength:.2f}, Vol={market_model_obj.volatility:.4f}")
                    
                    # 2. Генерация сценариев на основе модели
                    scenarios = self.scenario_writer.generate_scenarios(market_model, current_price)
                    
                    if scenarios:
                        # ВАЖНО: Проверяем ВСЕ сценарии, не только лучший!
                        # Каждый сценарий проходит валидацию и попадает в тень или на исполнение
                        for scenario in scenarios:
                            # 3. Валидация риск-менеджером (async метод)
                            # is_shadow=True - чтобы в режиме обучения все сценарии шли в тень для статистики
                            is_valid, reason = await self.risk_manager.validate_scenario(symbol, scenario.to_dict(), is_shadow=False)  # БОЕВОЙ РЕЖИМ
                            
                            if is_valid:
                                # 4. Отправка в Executor (реальная сделка или тень)
                                logger.info(f"Scenario ACCEPTED for {symbol}: {scenario.strategy_type} {scenario.direction} - {reason}")
                                await self.executor.execute_scenario(scenario)
                                await self.notifier.notify_trade(scenario.to_dict(), "OPEN")
                                
                                # ВАЖНО: Даже принятые сценарии идут в ShadowDealer для обучения!
                                # Это нужно для сбора полной статистики по всем исходам
                                shadow_trade = await self.shadow_dealer.execute_scenario(scenario.to_dict())
                                await self.risk_manager.add_to_shadow_learning(symbol, scenario.to_dict(), "accepted_real_trade", shadow_trade)
                            else:
                                # Даже отклоненные сценарии идут в ShadowDealer для обучения!
                                logger.debug(f"Scenario REJECTED for {symbol}: {scenario.strategy_type} {scenario.direction} - {reason}")
                                # Отправляем в ShadowDealer для сбора статистики
                                shadow_trade = await self.shadow_dealer.execute_scenario(scenario.to_dict())
                                await self.risk_manager.add_to_shadow_learning(symbol, scenario.to_dict(), reason, shadow_trade)
                    else:
                        logger.debug(f"No valid scenarios for {symbol}")
                    
                    # 5. Обновление трейлинг-стопов для активных позиций
                    # Обновляем цены в ShadowDealer для проверки TP/SL
                    await self.shadow_dealer.update_prices(current_price)
                    
                    # Получаем закрытые сделки из ShadowDealer и сохраняем карточки
                    closed_trades = self.shadow_dealer.get_closed_trades(limit=10)
                    for trade in closed_trades:
                        if not getattr(trade, '_card_saved', False):
                            # Сохраняем карточку сделки с данными анализаторов из оригинального сценария
                            # Данные теперь берутся напрямую из объекта ShadowTrade
                            scenario_data = {
                                'symbol': trade.symbol,
                                'direction': trade.direction,
                                'entry_price': trade.entry_price,
                                'stop_loss': getattr(trade, 'stop_loss', 0),
                                'target_price': getattr(trade, 'take_profit', 0),
                                'quantity': trade.quantity,
                                'leverage': trade.leverage,
                                'strategy_type': getattr(trade, 'strategy_type', 'shadow'),
                                'confidence': getattr(trade, 'confidence', 0.5),
                                'risk_reward_ratio': getattr(trade, 'risk_reward_ratio', 0),
                                'timestamp': trade.timestamp_open,
                                'is_real': getattr(trade, 'is_real', False),  # Флаг реальной сделки
                                # Данные анализаторов - теперь берем из ShadowTrade (заполнены из сценария)
                                'analyzer_trend_useful': getattr(trade, 'analyzer_trend_useful', False),
                                'analyzer_mean_reversion_useful': getattr(trade, 'analyzer_mean_reversion_useful', False),
                                'analyzer_order_flow_useful': getattr(trade, 'analyzer_order_flow_useful', False),
                                'analyzer_volatility_useful': getattr(trade, 'analyzer_volatility_useful', False),
                                'analyzer_matrix_useful': getattr(trade, 'analyzer_matrix_useful', False),
                                'analyzer_trend_confidence': getattr(trade, 'analyzer_trend_confidence', 0.0),
                                'analyzer_mean_reversion_confidence': getattr(trade, 'analyzer_mean_reversion_confidence', 0.0),
                                'analyzer_order_flow_confidence': getattr(trade, 'analyzer_order_flow_confidence', 0.0),
                                'analyzer_volatility_confidence': getattr(trade, 'analyzer_volatility_confidence', 0.0),
                                'analyzer_matrix_confidence': getattr(trade, 'analyzer_matrix_confidence', 0.0),
                                'market_trend': getattr(trade, 'market_trend', 'NEUTRAL'),
                                'market_volatility': getattr(trade, 'market_volatility', 0.0),
                                'market_volume': getattr(trade, 'market_volume', 0.0)
                            }
                            result_data = {
                                'pnl': trade.pnl or 0,
                                'pnl_percent': trade.pnl_percent or 0,
                                'exit_price': trade.exit_price or 0,
                                'duration': trade.duration_sec or 0,
                                'reason': trade.reason or 'unknown',
                                'max_drawdown': trade.max_drawdown,
                                'max_profit': trade.max_profit
                            }
                            await self.executor.save_trade_card(trade.symbol, scenario_data, result_data)
                            trade._card_saved = True  # Помечаем что карточка сохранена
                
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"Error in trading decision loop: {e}", exc_info=True)
                await self.notifier.notify_error(str(e))
                await asyncio.sleep(delay)

    async def _health_monitor_loop(self):
        """Мониторинг здоровья системы и ротация данных."""
        delay = 10
        rotation_check_interval = 300  # Проверка ротации каждые 5 минут
        last_rotation_check = 0
        
        while True:
            try:
                self.health.heartbeat("health_monitor")
                
                # Проверка и ротация данных
                current_time = time.time()
                if current_time - last_rotation_check >= rotation_check_interval:
                    rotation_results = self.data_rotator.check_and_rotate()
                    if rotation_results["log_rotated"]:
                        logger.info(f"Log rotation performed. Current log size: {rotation_results['log_size_mb']:.2f} MB")
                    if rotation_results["db_rotated"]:
                        logger.info(f"Database rotation performed. Current DB size: {rotation_results['db_size_mb']:.2f} MB")
                    last_rotation_check = current_time
                
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error in health monitor: {e}", exc_info=True)
                await asyncio.sleep(delay)

    async def _autotuner_loop(self):
        """
        ШАГ 3: Автономное обучение (AutoTuner Loop).
        Периодически запускает AutoTuner для обновления весов на основе новых карточек сделок.
        """
        import os
        from pathlib import Path
        
        cards_path = Path("data_storage/cards")
        tuner_interval_sec = 300  # Запускать тюнер каждые 5 минут
        check_interval_sec = 30   # Проверять наличие новых карточек каждые 30 секунд
        
        while True:
            try:
                current_time = time.time()
                
                # Подсчет количества карточек в SQLite базе данных
                card_count = 0
                db_path = cards_path.parent / "trading_history.db"
                if db_path.exists():
                    try:
                        import sqlite3
                        conn = sqlite3.connect(str(db_path))
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM trades")
                        card_count = cursor.fetchone()[0]
                        conn.close()
                    except Exception as e:
                        logger.error(f"AutoTuner: Error counting cards in DB: {e}")
                        card_count = 0
                
                # Проверяем появились ли новые карточки
                new_cards = card_count - self.last_card_count
                
                # Запускаем тюнер если:
                # 1. Появились новые карточки ИЛИ
                # 2. Прошло достаточно времени с последнего запуска
                time_since_last_run = current_time - self.last_tuner_run
                should_run_tuner = (new_cards > 0 and time_since_last_run > 60) or (time_since_last_run >= tuner_interval_sec and card_count > 0)
                
                if should_run_tuner:
                    logger.info(f"AutoTuner: Starting learning cycle ({new_cards} new cards, {card_count} total)")
                    
                    result = self.tuner.run_full_cycle()
                    
                    if result.get("status") == "success":
                        cards_analyzed = result.get("cards_analyzed", 0)
                        report = result.get("report", {})
                        
                        # Логгируем результаты
                        if "analyzers" in report:
                            for analyzer_type, metrics in report["analyzers"].items():
                                logger.info(
                                    f"AutoTuner updated weights: {analyzer_type} -> "
                                    f"win_rate={metrics.get('win_rate', 0):.2f}, "
                                    f"impact_score={metrics.get('impact_score', 0):.3f}"
                                )
                        
                        logger.info(f"AutoTuner updated weights based on {cards_analyzed} trades")
                        self.last_tuner_run = current_time
                    
                    self.last_card_count = card_count
                
                await asyncio.sleep(check_interval_sec)
                
            except Exception as e:
                logger.error(f"Error in autotuner loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval_sec)

    async def _balance_check_loop(self):
        """
        ШАГ 4: Сверка балансов.
        Периодически сравнивает расчетный баланс бота с реальным балансом на бирже.
        """
        
        check_interval_sec = 60  # Проверка каждую минуту
        
        while True:
            try:
                # Запрос реального баланса через API Binance
                balance_real = await self.executor.get_balance()
                balance_real_usdt = balance_real.get('total', 0)  # walletBalance включает нереализованный PnL

                # Получаем PnL всех закрытых теневых сделок для статистики
                stats = self.shadow_dealer.get_statistics()
                total_shadow_pnl = stats.get('total_pnl', 0)

                # Логгируем только реальный баланс и статистику - никаких выдуманных балансов
                logger.info(
                    f"[CHECK] Real Balance: ${balance_real_usdt:.2f} | "
                    f"Shadow PnL (closed trades): ${total_shadow_pnl:.2f}"
                )

                await asyncio.sleep(check_interval_sec)
            except Exception as e:
                logger.error(f"Error in balance check loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval_sec)

    async def _laboratory_loop(self):
        """
        ШАГ 5: Лаборатория сценариев (ScenarioLab Loop).
        Анализирует все закрытые сделки (реальные и теневые),
        подбирает оптимальные параметры для максимизации прибыли,
        передает результаты в AutoTuner.
        
        Целевые метрики: 100% PnL/день, 100% WinRate, 0% Drawdown
        """
        from pathlib import Path
        
        cards_path = Path("data_storage/cards")
        lab_interval_sec = 600  # Запускать лабораторию каждые 10 минут
        check_interval_sec = 60  # Проверять наличие новых карточек каждые минуту
        
        while True:
            try:
                current_time = time.time()
                
                # Подсчет количества карточек в SQLite базе данных
                card_count = 0
                db_path = cards_path.parent / "trading_history.db"
                if db_path.exists():
                    try:
                        import sqlite3
                        conn = sqlite3.connect(str(db_path))
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM trades")
                        card_count = cursor.fetchone()[0]
                        conn.close()
                    except Exception as e:
                        logger.error(f"Laboratory: Error counting cards in DB: {e}")
                        card_count = 0
                
                # Проверяем появились ли новые карточки или прошло время
                new_cards = card_count - self.last_card_count
                time_since_last_run = current_time - self.last_lab_run
                
                # Запускаем лабораторию если:
                # 1. Появились новые карточки И прошло больше минуты с последнего запуска
                # 2. ИЛИ прошло достаточно времени с последнего запуска И есть карточки
                should_run_lab = (new_cards > 0 and time_since_last_run > 60) or (time_since_last_run >= lab_interval_sec and card_count > 0)
                
                if should_run_lab:
                    logger.info(f"Laboratory: Starting analysis cycle ({new_cards} new cards, {card_count} total)")
                    
                    # Загружаем карточки сделок из базы данных
                    cards = []
                    if db_path.exists():
                        try:
                            import sqlite3
                            conn = sqlite3.connect(str(db_path))
                            conn.row_factory = sqlite3.Row
                            cursor = conn.cursor()
                            
                            cursor.execute("""
                                SELECT * FROM trades ORDER BY id DESC LIMIT 100
                            """)
                            
                            rows = cursor.fetchall()
                            for row in rows:
                                card = {
                                    "symbol": row["symbol"],
                                    "timestamp_open": row["timestamp_open"],
                                    "timestamp_close": row["timestamp_close"],
                                    "strategy_type": row["strategy_type"],
                                    "direction": row["direction"],
                                    "entry_price": row["entry_price"],
                                    "stop_loss": row["stop_loss"],
                                    "target_price": row["target_price"],
                                    "confidence": row["confidence"],
                                    "risk_reward_ratio": row["risk_reward_ratio"],
                                    "leverage": row["leverage"],
                                    "quantity": row["quantity"],
                                    "trade_result": {
                                        "pnl_usd": row["pnl_usd"],
                                        "pnl_percent": row["pnl_percent"],
                                        "exit_price": row["exit_price"],
                                        "duration_sec": row["duration_sec"],
                                        "exit_reason": row["exit_reason"],
                                        "max_drawdown": row["max_drawdown"],
                                        "max_profit": row["max_profit"]
                                    },
                                    "tuner_notes": {
                                        "analyzer_trend_useful": bool(row["analyzer_trend_useful"]) if row["analyzer_trend_useful"] is not None else False,
                                        "analyzer_mean_reversion_useful": bool(row["analyzer_mean_reversion_useful"]) if row["analyzer_mean_reversion_useful"] is not None else False,
                                        "analyzer_order_flow_useful": bool(row["analyzer_order_flow_useful"]) if row["analyzer_order_flow_useful"] is not None else False,
                                        "analyzer_volatility_useful": bool(row["analyzer_volatility_useful"]) if row["analyzer_volatility_useful"] is not None else False,
                                        "analyzer_matrix_useful": bool(row["analyzer_matrix_useful"]) if row["analyzer_matrix_useful"] is not None else False,
                                        "analyzer_trend_confidence": row["analyzer_trend_confidence"],
                                        "analyzer_mean_reversion_confidence": row["analyzer_mean_reversion_confidence"],
                                        "analyzer_order_flow_confidence": row["analyzer_order_flow_confidence"],
                                        "analyzer_volatility_confidence": row["analyzer_volatility_confidence"],
                                        "analyzer_matrix_confidence": row["analyzer_matrix_confidence"]
                                    },
                                    "market_conditions": {
                                        "trend": row["market_trend"],
                                        "volatility": row["market_volatility"],
                                        "volume": row["market_volume"]
                                    },
                                    "is_real": row["is_real"] if "is_real" in row.keys() else False
                                }
                                cards.append(card)
                            
                            conn.close()
                        except Exception as e:
                            logger.error(f"Laboratory: Error loading cards from DB: {e}")
                            cards = []
                    
                    if not cards:
                        logger.warning("Laboratory: No cards to analyze")
                        self.last_lab_run = current_time
                        await asyncio.sleep(check_interval_sec)
                        continue
                    
                    # Анализируем каждую карточку через Лабораторию
                    all_snapshots = []
                    market_context = {
                        "volatility": sum(c.get("market_conditions", {}).get("volatility", 0.01) for c in cards) / len(cards) if cards else 0.01
                    }
                    
                    for card in cards:
                        # Создаем псевдо-сценарий из карточки для анализа
                        scenario = {
                            "id": f"lab_analysis_{int(card['timestamp_open'])}",
                            "strategy_type": card.get("strategy_type", "unknown"),
                            "direction": card.get("direction", "BUY"),
                            "entry_price": card.get("entry_price", 0),
                            "parameters": {
                                "stop_loss_pct": abs((card.get("entry_price", 1) - card.get("stop_loss", 0.99)) / card.get("entry_price", 1)),
                                "take_profit_pct": abs((card.get("target_price", 1.02) - card.get("entry_price", 1)) / card.get("entry_price", 1)),
                                "leverage": card.get("leverage", 1.0),
                                "confidence_threshold": card.get("confidence", 0.5)
                            },
                            "simulated_pnl": card.get("trade_result", {}).get("pnl_usd", 0)
                        }
                        
                        # Лаборатория анализирует сценарий и предлагает улучшения
                        snapshots = self.lab.analyze_scenario(scenario, market_context)
                        all_snapshots.extend(snapshots)
                    
                    if all_snapshots:
                        # Генерируем отчет оптимизации
                        report = self.lab.generate_optimization_report(all_snapshots)
                        
                        if report.get("status") != "NO_DATA":
                            logger.info(
                                f"Laboratory: Best adjustment found - "
                                f"param={report['best_adjustment']['param']}, "
                                f"direction={report['best_adjustment']['direction']}, "
                                f"value={report['best_adjustment']['value']:.4f}, "
                                f"expected_gain={report['best_adjustment']['expected_gain']:.2f}"
                            )
                            
                            # Передаем результаты в AutoTuner для обновления весов
                            # AutoTuner использует эти данные для калибровки confidence_factors
                            logger.info(f"Laboratory: Sending {len(all_snapshots)} snapshots to AutoTuner")
                            
                            # Сохраняем снимки лаборатории в базу данных для истории
                            try:
                                import sqlite3
                                conn = sqlite3.connect(str(db_path))
                                cursor = conn.cursor()
                                
                                for snapshot in all_snapshots:
                                    cursor.execute("""
                                        INSERT INTO lab_snapshots 
                                        (scenario_id, original_pnl, modified_pnl, improvement, 
                                         changed_param, new_value, old_value, recommendation, timestamp)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """, (
                                        snapshot.get("scenario_id", ""),
                                        snapshot.get("original_pnl", 0),
                                        snapshot.get("modified_pnl", 0),
                                        snapshot.get("improvement", 0),
                                        snapshot.get("changed_param", ""),
                                        snapshot.get("new_value", 0),
                                        snapshot.get("old_value", 0),
                                        snapshot.get("recommendation", ""),
                                        current_time
                                    ))
                                
                                conn.commit()
                                conn.close()
                                logger.info(f"Laboratory: Saved {len(all_snapshots)} snapshots to database")
                            except Exception as e:
                                logger.error(f"Laboratory: Error saving snapshots to DB: {e}")
                    
                    self.last_lab_run = current_time
                
                await asyncio.sleep(check_interval_sec)
                
            except Exception as e:
                logger.error(f"Error in laboratory loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval_sec)

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())
