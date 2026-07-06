"""
Главный оркестратор (Main Entry Point).
Собирает все модули в единый асинхронный цикл.
"""
import asyncio
import logging
from typing import Optional

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

logging.basicConfig(
    level=logging.INFO,
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
        
        # 6. Инициализация риск-менеджера
        self.risk_manager = RiskManager(self.config, self.prob_field)
        
        # 7. Инициализация сценариста
        self.scenario_writer = ScenarioWriter(self.config, self.prob_field, self.risk_manager)
        
        # 8. Инициализация тюнера
        self.tuner = AutoTuner(self.config, self.prob_field)
        
        # 9. Инициализация исполнителя
        self.executor = Executor(self.config, self.prob_field, self.risk_manager)
        
        # Символы для торговли
        self.symbols = self.config.data.symbols
        
        logger.info(f"Trading Bot initialized. Symbols: {self.symbols}")

    async def start(self):
        """Запуск основного цикла."""
        logger.info("Starting Trading Bot loop...")
        await self.notifier.notify_status("Bot Started")
        
        # Инициализация клиента Binance
        await self.executor.start()
        await self.news.start()
        
        # Инициализация поля данных для каждого символа
        for symbol in self.symbols:
            # Получаем начальную цену из фида
            initial_price = await self.feed.get_initial_price(symbol)
            await self.prob_field.initialize_symbol(symbol, initial_price)
            logger.info(f"Initialized {symbol} with price {initial_price}")
        
        # Запуск фоновых задач
        tasks = [
            asyncio.create_task(self._data_feed_loop()),
            asyncio.create_task(self._news_loop()),
            asyncio.create_task(self._risk_analysis_loop()),
            asyncio.create_task(self._trading_decision_loop()),
            asyncio.create_task(self._health_monitor_loop())
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
        """Цикл получения рыночных данных."""
        delay = self.config.get('core', {}).get('loop_delay_sec', 1)
        while True:
            try:
                for symbol in self.symbols:
                    await self.feed.run_cycle(symbol)
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
                    # 1. Анализ рынка и генерация сценария
                    scenario = await self.scenario_writer.analyze_market(symbol)
                    
                    if scenario:
                        # 2. Валидация и отправка в Executor
                        success = await self.scenario_writer.validate_and_submit(
                            scenario, 
                            self.executor.submit_command
                        )
                        
                        if success:
                            await self.notifier.notify_trade(scenario.__dict__, "OPEN")
                        else:
                            logger.warning(f"Scenario rejected for {symbol}")
                    
                    # 3. Обновление трейлинг-стопов для активных позиций
                    card = await self.prob_field.get_card(symbol)
                    if card and self.scenario_writer.active_scenarios:
                        await self.scenario_writer.update_trail(
                            symbol, 
                            card.price, 
                            self.executor.submit_command
                        )
                
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
                await self.health.check_connectivity()
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"Error in health monitor: {e}", exc_info=True)
                await asyncio.sleep(delay)

if __name__ == "__main__":
    bot = TradingBot()
    asyncio.run(bot.start())
