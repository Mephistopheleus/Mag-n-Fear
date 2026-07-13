"""
Utility: Trading History Summary Generator
Генерирует сводный файл trading_history_summary.txt из данных карточек сделок.
"""
import os
from pathlib import Path
from datetime import datetime


def generate_summary(cards_dir: str = "data_storage/cards", output_file: str = "trading_history_summary.txt"):
    """
    Генерирует текстовый файл с краткой сводкой по всем сделкам.
    
    Args:
        cards_dir: Директория с карточками сделок
        output_file: Имя выходного файла
    """
    cards_path = Path(cards_dir)
    db_path = cards_path.parent / "trading_history.db"
    output_path = cards_path.parent / output_file
    
    if not db_path.exists():
        print(f"[Summary] No database file found at {db_path}")
        return False
    
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Получаем все сделки
        cursor.execute("SELECT * FROM trades ORDER BY id DESC")
        all_cards = [dict(row) for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        print(f"[Summary] Error reading database: {e}")
        return False
    
    if not all_cards:
        print("[Summary] No cards to summarize")
        return False
    
    # Статистика
    total_trades = len(all_cards)
    profitable = sum(1 for c in all_cards if c.get("pnl_usd", 0) > 0)
    losing = total_trades - profitable
    win_rate = profitable / total_trades if total_trades > 0 else 0
    
    total_pnl = sum(c.get("pnl_usd", 0) for c in all_cards)
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
    
    max_profit = max((c.get("pnl_usd", 0) for c in all_cards), default=0)
    max_loss = min((c.get("pnl_usd", 0) for c in all_cards), default=0)
    
    # Группировка по стратегиям
    strategies = {}
    for card in all_cards:
        strategy = card.get("strategy_type", "unknown")
        if strategy not in strategies:
            strategies[strategy] = {"count": 0, "profitable": 0, "total_pnl": 0}
        
        strategies[strategy]["count"] += 1
        pnl = card.get("pnl_usd", 0)
        if pnl > 0:
            strategies[strategy]["profitable"] += 1
        strategies[strategy]["total_pnl"] += pnl
    
    # Группировка по направлениям
    directions = {"LONG": {"count": 0, "profitable": 0}, "SHORT": {"count": 0, "profitable": 0}}
    for card in all_cards:
        direction = card.get("direction", "UNKNOWN")
        if direction not in directions:
            directions[direction] = {"count": 0, "profitable": 0}
        
        directions[direction]["count"] += 1
        pnl = card.get("pnl_usd", 0)
        if pnl > 0:
            directions[direction]["profitable"] += 1
    
    # Группировка по символам
    symbols = {}
    for card in all_cards:
        symbol = card.get("symbol", "UNKNOWN")
        if symbol not in symbols:
            symbols[symbol] = {"count": 0, "profitable": 0, "total_pnl": 0}
        
        symbols[symbol]["count"] += 1
        pnl = card.get("pnl_usd", 0)
        if pnl > 0:
            symbols[symbol]["profitable"] += 1
        symbols[symbol]["total_pnl"] += pnl
    
    # Генерация отчета
    report_lines = [
        "=" * 80,
        "TRADING HISTORY SUMMARY",
        f"Generated: {datetime.utcnow().isoformat()}",
        "=" * 80,
        "",
        "OVERVIEW:",
        f"  Total Trades: {total_trades}",
        f"  Profitable: {profitable} ({win_rate:.2%})",
        f"  Losing: {losing} ({1-win_rate:.2%})",
        f"  Total PnL: ${total_pnl:.4f}",
        f"  Average PnL: ${avg_pnl:.6f}",
        f"  Max Profit: ${max_profit:.4f}",
        f"  Max Loss: ${max_loss:.4f}",
        "",
        "BY STRATEGY:",
    ]
    
    for strategy, stats in sorted(strategies.items()):
        strat_win_rate = stats["profitable"] / stats["count"] if stats["count"] > 0 else 0
        report_lines.append(f"  {strategy}:")
        report_lines.append(f"    Trades: {stats['count']}, WinRate: {strat_win_rate:.2%}, PnL: ${stats['total_pnl']:.4f}")
    
    report_lines.extend([
        "",
        "BY DIRECTION:",
    ])
    
    for direction, stats in directions.items():
        dir_win_rate = stats["profitable"] / stats["count"] if stats["count"] > 0 else 0
        report_lines.append(f"  {direction}: {stats['count']} trades, {dir_win_rate:.2%} win rate")
    
    report_lines.extend([
        "",
        "BY SYMBOL:",
    ])
    
    for symbol, stats in sorted(symbols.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
        sym_win_rate = stats["profitable"] / stats["count"] if stats["count"] > 0 else 0
        report_lines.append(f"  {symbol}: {stats['count']} trades, {sym_win_rate:.2%} win rate, PnL: ${stats['total_pnl']:.4f}")
    
    report_lines.extend([
        "",
        "=" * 80,
        "DETAILED TRADES (Last 50):",
        "=" * 80,
    ])
    
    # Последние 50 сделок
    recent_trades = all_cards[-50:] if len(all_cards) > 50 else all_cards
    for i, card in enumerate(reversed(recent_trades), 1):
        pnl = card.get("pnl_usd", 0)
        symbol = card.get("symbol", "N/A")
        direction = card.get("direction", "N/A")
        strategy = card.get("strategy_type", "N/A")
        exit_reason = card.get("exit_reason", "N/A")
        timestamp = card.get("timestamp_open", 0)
        
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp)
        else:
            dt = timestamp
        
        sign = "+" if pnl >= 0 else ""
        report_lines.append(f"{i}. {dt.strftime('%Y-%m-%d %H:%M:%S')} | {symbol} | {direction} {strategy} | {exit_reason} | {sign}${pnl:.6f}")
    
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("END OF REPORT")
    report_lines.append("=" * 80)
    
    # Сохранение отчета
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"[Summary] Generated summary: {output_path}")
    print(f"[Summary] Total trades: {total_trades}, WinRate: {win_rate:.2%}, Total PnL: ${total_pnl:.4f}")
    return True


if __name__ == "__main__":
    generate_summary()
