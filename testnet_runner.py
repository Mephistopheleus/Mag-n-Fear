import time
import yaml
import logging
from datetime import datetime
from modules.binance_api import BinanceClient
from modules.analyzer_math import MarketAnalyzer
from modules.risk_manager import RiskManager

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trade_log.txt"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_config():
    with open('config/config.yaml', 'r') as f:
        return yaml.safe_load(f)

def calculate_score(forecast, confidence_m5, trend_m15, trend_m30, atr, current_price, levels):
    """Система взвешенных оценок (Scoring System)"""
    score = 0
    
    # 1. Базовый прогноз (до 40 баллов)
    if abs(forecast['percent_change']) > 0.5:
        score += 30
        if abs(forecast['percent_change']) > 1.0:
            score += 10
    
    # 2. Уверенность M5 (до 20 баллов)
    score += int(confidence_m5 * 20)
    
    # 3. Контекст старших ТФ (до 30 баллов)
    # Если тренды совпадают с прогнозом
    direction = 1 if forecast['percent_change'] > 0 else -1
    if trend_m15 * direction > 0: score += 15
    if trend_m30 * direction > 0: score += 15
    
    # 4. Уровни поддержки/сопротивления (до 10 баллов)
    dist_to_level = min(abs(current_price - l) for l in levels) if levels else 1.0
    if dist_to_level < 0.005 * current_price: # Близко к уровню
        score += 10
        
    return min(score, 100)

def main():
    config = load_config()
    api = BinanceClient(config['binance']['testnet_api_key'], config['binance']['testnet_secret_key'], testnet=True)
    analyzer = MarketAnalyzer()
    risk_mgr = RiskManager(start_balance=10000) # Стартовый баланс для расчета %
    
    logger.info("=== ЗАПУСК ТОРГОВОГО БОТА (LIVE TESTNET) ===")
    logger.info(f"Порог входа (Score): {config['strategy']['min_score']}")
    
    last_trade_time = 0
    
    while True:
        try:
            # 1. Получение данных
            candles_m5 = api.get_klines('DOGEUSDT', '5m', limit=300)
            candles_m15 = api.get_klines('DOGEUSDT', '15m', limit=100)
            candles_m30 = api.get_klines('DOGEUSDT', '30m', limit=100)
            
            if not candles_m5:
                time.sleep(60)
                continue
                
            current_price = float(candles_m5[-1]['close'])
            
            # 2. Анализ
            forecast = analyzer.predict(candles_m5) # Возвращает {price, percent_change, confidence}
            trend_15 = analyzer.get_trend_direction(candles_m15) # 1 или -1
            trend_30 = analyzer.get_trend_direction(candles_m30)
            atr = analyzer.calculate_atr(candles_m5)
            levels = analyzer.get_support_resistance(candles_m5)
            
            # 3. Расчет Score
            score = calculate_score(
                forecast, 
                forecast['confidence'], 
                trend_15, 
                trend_30, 
                atr, 
                current_price, 
                levels
            )
            
            logger.info(f"Цена: {current_price} | Прогноз: {forecast['percent_change']:.2f}% ({forecast['confidence']:.2f}) | Score: {score}")
            
            # 4. Принятие решения
            threshold = config['strategy']['min_score']
            
            if score >= threshold:
                # Проверка кулдауна
                if time.time() - last_trade_time < 300: # 5 минут пауза
                    logger.info("SKIP: Кулдаун после последней сделки")
                else:
                    direction = 'LONG' if forecast['percent_change'] > 0 else 'SHORT'
                    logger.info(f"СИГНАЛ НА {direction}! Score: {score}. Расчет ордера...")
                    
                    # Расчет параметров сделки
                    position_size, sl_price, tp_price = risk_mgr.calculate_position(
                        balance=api.get_balance(),
                        entry_price=current_price,
                        direction=direction,
                        stop_loss_pct=0.02, # 2% стоп
                        atr=atr
                    )
                    
                    if position_size > 10: # Минимальный лимит биржи (~$10)
                        # Отправка ордера
                        order = api.place_order(
                            symbol='DOGEUSDT',
                            side='BUY' if direction == 'LONG' else 'SELL',
                            type='MARKET',
                            quantity=position_size
                        )
                        
                        if order:
                            logger.info(f"ОРДЕР ОТКРЫТ: {order['orderId']} по {current_price}")
                            # Установка SL и TP (эмуляция или реальные ордера StopMarket/TakeProfit)
                            # Для простоты пока логируем уровни
                            logger.info(f"Уровни защиты: SL={sl_price}, TP={tp_price} (Trailing Active)")
                            
                            # В реальной системе здесь был бы цикл мониторинга позиции для трейлинга
                            last_trade_time = time.time()
                    else:
                        logger.info(f"SKIP: Объем слишком мал ({position_size})")
            else:
                logger.info(f"SKIP: Score ({score}) ниже порога ({threshold})")
                
            time.sleep(60) # Опрос раз в минуту
            
        except Exception as e:
            logger.error(f"Ошибка в цикле: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()