"""
Тест подключения к Binance Futures Testnet.
Проверяет:
1. Подключение по ключам из конфига.
2. Баланс аккаунта.
3. Стакан DOGEUSDT.
4. Цену BTCUSDT.
"""
import asyncio
import yaml
from binance import AsyncClient

async def test_binance_connection():
    # Загрузка конфига
    with open("configs/config.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    # Получение ключей API (предполагаем, что они есть в разделе api_keys)
    # Если ключей нет в конфиге, используем переменные окружения или заглушки
    api_key = config.get("api_keys", {}).get("binance_testnet_api_key", "")
    api_secret = config.get("api_keys", {}).get("binance_testnet_api_secret", "")
    
    if not api_key or not api_secret:
        print("⚠️ Ключи API не найдены в конфиге. Используем тестовые ключи Binance Testnet.")
        # Публичные тестовые ключи для Binance Futures Testnet (можно получить на https://testnet.binancefuture.com)
        # Для теста используем публичные методы без ключей
        api_key = None
        api_secret = None
    
    print("🔌 Подключение к Binance Futures Testnet...")
    
    # Инициализация клиента для фьючерсов
    client = await AsyncClient.create(
        api_key=api_key,
        api_secret=api_secret,
        testnet=True,
        requests_params={"timeout": 10}
    )
    
    try:
        # 1. Проверка баланса
        print("\n💰 Проверка баланса...")
        if api_key and api_secret:
            balance = await client.futures_account_balance()
            usdt_balance = [b for b in balance if b['asset'] == 'USDT']
            if usdt_balance:
                print(f"   USDT Balance: {usdt_balance[0]['availableBalance']} {usdt_balance[0]['asset']}")
            else:
                print("   USDT баланс не найден")
        else:
            print("   ⚠️ Пропущено (нет API ключей)")
        
        # 2. Получение стакана DOGEUSDT
        print("\n📊 Стакан DOGEUSDT...")
        orderbook = await client.get_order_book(symbol='DOGEUSDT', limit=10)
        best_bid = float(orderbook['bids'][0][0]) if orderbook['bids'] else 0
        best_ask = float(orderbook['asks'][0][0]) if orderbook['asks'] else 0
        print(f"   Best Bid: {best_bid}")
        print(f"   Best Ask: {best_ask}")
        print(f"   Spread: {best_ask - best_bid:.6f}")
        
        # 3. Получение цены BTCUSDT
        print("\n₿ Цена BTCUSDT...")
        ticker = await client.get_symbol_ticker(symbol='BTCUSDT')
        btc_price = float(ticker['price'])
        print(f"   BTC Price: ${btc_price:,.2f}")
        
        # 4. Дополнительная информация о DOGEUSDT
        print("\n🐕 DOGEUSDT Информация...")
        doge_ticker = await client.get_symbol_ticker(symbol='DOGEUSDT')
        doge_price = float(doge_ticker['price'])
        print(f"   DOGE Price: ${doge_price:.6f}")
        
        # 5. Проверка информации о фьючерсах
        print("\n📈 DOGEUSDT Futures Info...")
        exchange_info = await client.futures_exchange_info()
        doge_symbol_info = None
        for symbol in exchange_info['symbols']:
            if symbol['symbol'] == 'DOGEUSDT':
                doge_symbol_info = symbol
                break
        
        if doge_symbol_info:
            print(f"   Status: {doge_symbol_info['status']}")
            print(f"   Min Order Size: {doge_symbol_info['filters'][0].get('minQty', 'N/A')} DOGE")
            print(f"   Price Precision: {doge_symbol_info['pricePrecision']}")
            print(f"   Quantity Precision: {doge_symbol_info['quantityPrecision']}")
        
        print("\n✅ Тест успешно завершен!")
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return False
        
    finally:
        await client.close_connection()


if __name__ == "__main__":
    result = asyncio.run(test_binance_connection())
    exit(0 if result else 1)
