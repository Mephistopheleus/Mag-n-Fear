"""
Тест новых модулей: DataCard, ProbabilityField, RiskManager, NewsAggregator.
Проверка архитектурной целостности перед интеграцией в main.py.
"""
import asyncio
import time
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.models import DataCard, RiskMetrics, NewsVector
from src.core.field import ProbabilityField
from src.risk.manager import RiskManager
from src.data.news_aggregator import NewsAggregator


async def test_data_models():
    """Тест структур данных."""
    print("\n=== ТЕСТ 1: Структуры данных ===")
    
    # Создание DataCard
    card = DataCard(
        symbol="DOGEUSDT",
        timestamp=time.time(),
        price=0.08234,
        volume_24h=1234567.89
    )
    print(f"✓ DataCard создан: {card.symbol} @ {card.price}")
    
    # Добавление новости
    news = NewsVector(
        direction=0.8,
        strength=0.6,
        duration_sec=300,
        probability=0.75,
        source_id="test",
        headline="Test bullish news"
    )
    card.add_news_vector(news)
    sentiment = card.get_aggregated_sentiment()
    print(f"✓ Новость добавлена, сентимент: {sentiment:.2f}")
    
    # Добавление метрик риска
    risk = RiskMetrics(
        max_leverage=3.5,
        liquidity_risk=0.2,
        drawdown_prob=0.15,
        volatility_index=0.03,
        exposure_limit=0.04,
        is_emergency=False
    )
    card.update_risk(risk)
    print(f"✓ Метрики риска: leverage={risk.max_leverage}, liq_risk={risk.liquidity_risk}")
    
    return True


async def test_probability_field():
    """Тест ProbabilityField."""
    print("\n=== ТЕСТ 2: ProbabilityField ===")
    
    field = ProbabilityField()
    
    # Инициализация символа
    await field.initialize_symbol("DOGEUSDT", 0.08234)
    card = await field.get_card("DOGEUSDT")
    print(f"✓ Символ инициализирован: {card.symbol}")
    
    # Обновление рыночных данных
    await field.update_market_data(
        "DOGEUSDT",
        price=0.08250,
        volume=2000000,
        orderbook={"bids": [[0.08249, 1000], [0.08248, 2000]], "asks": [[0.08251, 1500]]},
        trades=[{"price": 0.08250, "qty": 100}]
    )
    card = await field.get_card("DOGEUSDT")
    print(f"✓ Данные обновлены: цена={card.price}, объем={card.volume_24h}")
    
    # Запись метрик риска
    risk = RiskMetrics(
        max_leverage=4.0,
        liquidity_risk=0.1,
        drawdown_prob=0.1,
        volatility_index=0.02,
        exposure_limit=0.05,
        is_emergency=False
    )
    await field.update_risk_metrics("DOGEUSDT", risk)
    card = await field.get_card("DOGEUSDT")
    print(f"✓ Риск записан: max_leverage={card.risk_metrics.max_leverage}")
    
    # Запись новости
    news = NewsVector(
        direction=0.5,
        strength=0.7,
        duration_sec=300,
        probability=0.8,
        source_id="test_feed",
        headline="Dogecoin partnership announced"
    )
    await field.update_news_vector("DOGEUSDT", news)
    card = await field.get_card("DOGEUSDT")
    print(f"✓ Новость записана: {len(card.news_vectors)} векторов, сентимент={card.get_aggregated_sentiment():.2f}")
    
    return True


async def test_risk_manager():
    """Тест RiskManager."""
    print("\n=== ТЕСТ 3: RiskManager ===")
    
    field = ProbabilityField()
    config = {
        "risk": {
            "min_leverage": 1.0,
            "max_leverage": 5.0,
            "max_exposure_pct": 0.05,
            "volatility_threshold": 0.05
        }
    }
    
    rm = RiskManager(config, field)
    
    # Инициализация данных
    await field.initialize_symbol("DOGEUSDT", 0.08234)
    await field.update_market_data(
        "DOGEUSDT",
        price=0.08234,
        volume=1000000,
        orderbook={"bids": [[0.08230, 50000], [0.08220, 100000]], "asks": [[0.08240, 50000]]},
        trades=[{"price": 0.08234 + i*0.00001, "qty": 100} for i in range(-5, 5)]
    )
    
    # Анализ
    await rm.analyze_and_update("DOGEUSDT")
    card = await field.get_card("DOGEUSDT")
    
    if card.risk_metrics:
        m = card.risk_metrics
        print(f"✓ Анализ выполнен:")
        print(f"  - Volatility: {m.volatility_index:.4f}")
        print(f"  - Liquidity Risk: {m.liquidity_risk:.2f}")
        print(f"  - Max Leverage: {m.max_leverage:.2f}")
        print(f"  - Exposure Limit: {m.exposure_limit:.2%}")
        print(f"  - Emergency: {m.is_emergency}")
    else:
        print("✗ Ошибка: метрики риска не записаны")
        return False
    
    # Валидация сценария
    scenario = {
        "leverage": 3.0,
        "quantity": 10000,
        "price": 0.08234,
        "stop_loss": 0.08000
    }
    valid, reason = await rm.validate_scenario("DOGEUSDT", scenario)
    print(f"✓ Валидация сценария: {'OK' if valid else reason}")
    
    return True


async def test_news_aggregator():
    """Тест NewsAggregator (без реального парсинга, проверка структуры)."""
    print("\n=== ТЕСТ 4: NewsAggregator (структура) ===")
    
    field = ProbabilityField()
    await field.initialize_symbol("DOGEUSDT", 0.08234)
    
    config = {}
    na = NewsAggregator(config, field)
    
    # Проверка ключевых слов
    text_bullish = "Dogecoin surges on major partnership announcement and adoption"
    text_bearish = "SEC investigation into crypto exchanges causes market crash"
    
    dir_bull, str_bull = na._analyze_sentiment(text_bullish)
    dir_bear, str_bear = na._analyze_sentiment(text_bearish)
    
    print(f"✓ Бычья новость: direction={dir_bull:.2f}, strength={str_bull:.2f}")
    print(f"✓ Медвежья новость: direction={dir_bear:.2f}, strength={str_bear:.2f}")
    
    # Определение актива
    asset1 = na._detect_asset("DOGE moon soon!")
    asset2 = na._detect_asset("Bitcoin futures trading")
    asset3 = na._detect_asset("Random stock market news")
    
    print(f"✓ Детекция активов: DOGE={asset1}, BTC={asset2}, Other={asset3}")
    
    return True


async def main():
    print("=" * 60)
    print("ТЕСТИРОВАНИЕ МОДУЛЕЙ MAG-N-FEAR")
    print("=" * 60)
    
    tests = [
        ("Data Models", test_data_models),
        ("Probability Field", test_probability_field),
        ("Risk Manager", test_risk_manager),
        ("News Aggregator", test_news_aggregator),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ {name}: ОШИБКА - {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    print("\n" + "=" * 60)
    print("ИТОГИ:")
    all_passed = True
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    if all_passed:
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Модули готовы к интеграции.")
    else:
        print("❌ ЕСТЬ ОШИБКИ. Требуется доработка.")
    
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
