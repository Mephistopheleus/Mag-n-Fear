"""
Главный оркестратор (Main Entry Point).
Собирает все модули в единый асинхронный цикл.
"""
import asyncio
import logging
from typing import Optional

# Импорт конфигурации и утилит
from src.core.config_loader import ConfigLoader
from src.utils.health_check import HealthCheck
from src.utils.notifier import Notifier

# Импорт модулей данных
# from src.data.feed import DataFeed
from src.data.news_aggregator import NewsAggregator

# Импорт математического ядра
from src.math_core.time_continuum import TimeContinuum
from src.math_core.classic_tf import ClassicTF
from src.math_core.market_regime import MarketRegimeDetector
from src.math_core.order_book_sr import OrderBookAnalyzer
# from src.math_core.indicator_registry import IndicatorRegistry

# Импорт логики
from src.matrix.probability_field import ProbabilityField
from src.logic.matrix_analyzer import MatrixAnalyzer
from src.logic.scenario_writer import ScenarioWriter
from src.risk.manager import RiskManager
from src.tuner.auto_tuner import AutoTuner
# from src.executor.orders import Executor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self, config_path: str = "configs/config.yaml"):
        logger.info("Initializing Trading Bot...")
        
        # 1. Загрузка конфига
        self.config = ConfigLoader(config_path).config
        logger.info("Config loaded.")

        # 2. Инициализация утилит
        self.health = HealthCheck(self.config)
        self.notifier = Notifier(self.config)
        
        # 3. Инициализация данных
        self.news = NewsAggregator(self.config)
        # self.feed = DataFeed(self.config) # TODO
        
        # 4. Инициализация математики
        self.continuum = TimeContinuum(self.config)
        self.classic_tf = ClassicTF(self.config)
        self.regime_detector = MarketRegimeDetector(self.config)
        self.ob_analyzer = OrderBookAnalyzer(self.config)
        # self.indicators = IndicatorRegistry() # TODO
        
        # 5. Инициализация логики
        self.matrix = ProbabilityField(self.config)
        self.matrix_analyzer = MatrixAnalyzer(self.config)
        self.scenario_writer = ScenarioWriter(self.config)
        self.risk_manager = RiskManager(self.config)
        self.tuner = AutoTuner(self.config)
        # self.executor = Executor(self.config) # TODO

    async def start(self):
        """Запуск основного цикла."""
        logger.info("Starting Trading Bot loop...")
        await self.notifier.notify_status("Bot Started")
        
        # Запуск фоновых задач
        tasks = [
            asyncio.create_task(self.news.start()),
            asyncio.create_task(self.health.start_monitoring()),
            asyncio.create_task(self._main_loop())
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
        await self.notifier.notify_status("Bot Stopped")

    async def _main_loop(self):
        """
        Основной цикл обработки данных.
        Здесь происходит магия: Сбор -> Анализ -> Матрица -> Сценарий -> Риск -> Исполнение.
        """
        loop_delay = self.config.get("core", {}).get("loop_delay_sec", 1)
        
        while True:
            try:
                # 1. Heartbeat
                self.health.heartbeat("main_loop")
                
                # 2. Получение данных (заглушка, пока нет реального фида)
                # ticks = await self.feed.get_latest_ticks()
                # orderbook = await self.feed.get_orderbook()
                
                # Эмуляция данных для теста
                current_price = 50000.0
                ticks = [(current_price, 1.0)] * 100 
                bids = [(current_price - i*0.5, 10.0) for i in range(20)]
                asks = [(current_price + i*0.5, 10.0) for i in range(20)]
                
                # 3. Обновление полотен и таймфреймов
                self.continuum.update(ticks)
                self.classic_tf.update(ticks)
                
                # 4. Анализ режима рынка
                prices = [t[0] for t in ticks]
                volumes = [t[1] for t in ticks]
                regime = self.regime_detector.analyze(prices, volumes)
                
                # 5. Анализ стакана
                sr_levels = self.ob_analyzer.analyze_snapshot(bids, asks, current_price)
                
                # 6. Сбор факторов в Матрицу
                # (Здесь должен быть вызов индикаторов и запись в матрицу)
                # self.matrix.add_data(...)
                
                # 7. Анализ матрицы
                clusters = self.matrix_analyzer.analyze(self.matrix)
                
                # 8. Генерация сценария
                sentiment = self.news.get_current_sentiment_factor()
                scenario = self.scenario_writer.generate_scenario(
                    clusters=clusters,
                    regime=regime,
                    sr_levels=sr_levels,
                    current_price=current_price,
                    sentiment=sentiment
                )
                
                if scenario:
                    # 9. Проверка риска
                    risk_approved = self.risk_manager.validate(scenario)
                    
                    if risk_approved:
                        logger.info(f"Scenario Approved: {scenario.scenario_id}")
                        # 10. Исполнение (TODO)
                        # await self.executor.execute(scenario)
                        
                        # 11. Уведомление
                        await self.notifier.notify_trade(scenario.__dict__, "OPEN")
                        
                        # 12. Сохранение карточки для Тюнера
                        # self.tuner.record_card(scenario, ...)
                    else:
                        logger.warning(f"Scenario Rejected by Risk: {scenario.scenario_id}")
                
                await asyncio.sleep(loop_delay)
                
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await self.notifier.notify_error(str(e))
                await asyncio.sleep(loop_delay)

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())
