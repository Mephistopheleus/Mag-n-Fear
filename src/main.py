"""
Главный оркестратор (Main Entry Point).
Собирает все модули в единый асинхронный цикл.
"""
import asyncio
import logging
import sys
import os
import time
from typing import Optional

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импорт конфигурации и утилит
from src.core.config_loader import load_config, get_config
from src.utils.health_check import HealthCheck
from src.utils.notifier import Notifier

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

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, config_path: str = "configs/config.yaml"):
        logger.info("Initializing Trading Bot...")
        
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
        
        # Символы для торговли
        self.symbols = self.config.data.symbols
        
        # Для отслеживания карточек сделок (ШАГ 3: AutoTuner Loop)
        self.last_card_count = 0
        self.last_tuner_run = 0
        
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
            asyncio.create_task(self._balance_check_loop())  # ШАГ 4: Сверка балансов
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            await self.stop()

    async def stop(self):
        """Корректная остановка."""
        logger.info("Stopping components...")
        await self.news.stop()
        await self.executor.stop()
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
                    
                    market_data = {
                        'current_price': current_price,
                        'candles_short': candles_short,
                        'candles_mid': candles_mid,
                        'candles_long': candles_long,
                        'history': candles_short + candles_mid,  # Объединяем для расчета уровней
                        'order_book': order_book,  # Передаем стакан в синтезатор
                        'order_flow_imbalance': imbalance,
                        'news_impact': 0.0
                    }
                    
                    # Синтезируем единую модель рынка
                    market_model_obj = await self.synthesizer.synthesize(
                        current_price=current_price,
                        analysis_points=[],  # TODO: точки из матрицы
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
        """Мониторинг здоровья системы."""
        delay = 10
        while True:
            try:
                self.health.heartbeat("health_monitor")
                # check_connectivity не существует, используем start_monitoring или просто heartbeat
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
        import time
        
        check_interval_sec = 60  # Проверка каждую минуту
        
        while True:
            try:
                # Запрос баланса через API Binance
                balance_real = await self.executor.get_balance()
                balance_real_usdt = balance_real.get('available', 0) + balance_real.get('total', 0) / 2
                
                # Расчетный баланс бота (упрощенно: начальный баланс + PnL всех закрытых сделок)
                # Для точного расчета нужно суммировать PnL из карточек
                stats = self.shadow_dealer.get_statistics()
                total_shadow_pnl = stats.get('total_pnl', 0)
                
                # Берем начальный баланс из конфига
                initial_balance = self.config.risk.max_position_size_usd * 10  # Условно 10x от макс позиции
                
                balance_bot = initial_balance + total_shadow_pnl
                
                diff = balance_bot - balance_real_usdt
                diff_pct = (diff / balance_real_usdt * 100) if balance_real_usdt > 0 else 0
                
                logger.info(
                    f"[CHECK] Real: ${balance_real_usdt:.2f} | "
                    f"Bot: ${balance_bot:.2f} | "
                    f"Diff: ${diff:.2f} ({diff_pct:.2f}%) | "
                    f"Shadow PnL: ${total_shadow_pnl:.2f}"
                )
                
                # Если расхождение больше 5%, предупреждаем
                if abs(diff_pct) > 5:
                    logger.warning(f"[CHECK] Large balance discrepancy detected! {diff_pct:.2f}%")
                    await self.notifier.notify_error(f"Balance mismatch: {diff_pct:.2f}%")
                
                await asyncio.sleep(check_interval_sec)
                
            except Exception as e:
                logger.error(f"Error in balance check loop: {e}", exc_info=True)
                await asyncio.sleep(check_interval_sec)

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())
