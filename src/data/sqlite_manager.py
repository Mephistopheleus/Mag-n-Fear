"""
Data Module: SQLite Manager for Trade Snapshots and Logs.
Хранение снимков сделок, карточек анализов и логов автотюнера в SQLite.
Автоматическая ротация данных при превышении лимита (5MB для снимков, 2MB для логов).
"""
import sqlite3
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import threading

logger = logging.getLogger(__name__)


class SQLiteManager:
    """
    Менеджер базы данных SQLite для хранения:
    1. TradeSnapshot - полные снимки закрытых сделок
    2. AnalysisCard - карточки анализов
    3. TunerLog - логи изменений настроек автотюнером
    
    Автоматическая ротация:
    - Снимки сделок: макс. 5MB (старые записи удаляются)
    - Логи тюнера: макс. 2MB
    """
    
    def __init__(self, db_path: str, max_snapshots_size_mb: float = 5.0, max_logs_size_mb: float = 2.0):
        self.db_path = Path(db_path)
        self.max_snapshots_size_bytes = int(max_snapshots_size_mb * 1024 * 1024)
        self.max_logs_size_bytes = int(max_logs_size_mb * 1024 * 1024)
        
        # Блокировка для потокобезопасности
        self._lock = threading.Lock()
        
        # Создание таблиц при инициализации
        self._create_tables()
        
        logger.info(f"[SQLiteManager] Database initialized: {self.db_path}")
        logger.info(f"[SQLiteManager] Max snapshots size: {max_snapshots_size_mb}MB, Max logs size: {max_logs_size_mb}MB")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Получает соединение с БД."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _create_tables(self):
        """Создаёт таблицы если они не существуют."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Таблица снимков сделок
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trade_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        timestamp_open TEXT NOT NULL,
                        timestamp_close TEXT,
                        scenario_id TEXT,
                        risk_id TEXT,
                        order_id TEXT,
                        symbol TEXT,
                        direction TEXT,
                        entry_price REAL,
                        exit_price REAL,
                        highest_price REAL,
                        lowest_price REAL,
                        leverage REAL,
                        position_size_usd REAL,
                        quantity REAL,
                        pnl_usd REAL,
                        pnl_pct REAL,
                        commission_usd REAL,
                        slippage_usd REAL,
                        net_pnl_usd REAL,
                        duration_seconds INTEGER,
                        exit_reason TEXT,
                        mae_pct REAL,
                        mae_price REAL,
                        mfe_pct REAL,
                        mfe_price REAL,
                        trailing_stop_activated INTEGER,
                        trailing_stop_distance_pct REAL,
                        trailing_stop_activation_pct REAL,
                        analysis_cards TEXT,  -- JSON
                        scenario_card TEXT,   -- JSON
                        risk_card TEXT,       -- JSON
                        market_context TEXT,  -- JSON
                        config_snapshot TEXT, -- JSON
                        expected_profit_usd REAL,
                        profit_deviation_usd REAL,
                        profit_deviation_pct REAL,
                        tuner_analysis TEXT,  -- JSON
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Индексы для ускорения поиска
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_symbol ON trade_snapshots(symbol)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_open ON trade_snapshots(timestamp_open)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_pnl ON trade_snapshots(pnl_usd)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_scenario ON trade_snapshots(scenario_id)")
                
                # Таблица карточек анализов
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS analysis_cards (
                        card_id TEXT PRIMARY KEY,
                        timestamp TEXT NOT NULL,
                        analyzer_id TEXT,
                        symbol TEXT,
                        price REAL,
                        timeframe TEXT,
                        analysis_type TEXT,
                        value TEXT,  -- JSON для сложных значений
                        predicted_price REAL,
                        predicted_time TEXT,
                        horizon_seconds INTEGER,
                        confidence REAL,
                        trust_score REAL,
                        combined_probability REAL,
                        input_params TEXT,  -- JSON
                        calculation_method TEXT,
                        snapshot_id TEXT,  -- ссылка на снимок сделки (если применимо)
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_analyzer ON analysis_cards(analyzer_id)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_symbol ON analysis_cards(symbol)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_analysis_snapshot ON analysis_cards(snapshot_id)")
                
                # Таблица логов автотюнера
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tuner_logs (
                        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT,  -- "config_change", "trust_update", "analyzer_impact"
                        description TEXT,
                        old_value TEXT,   -- JSON
                        new_value TEXT,   -- JSON
                        impact_score REAL,
                        snapshot_id TEXT,  -- ссылка на снимок сделки
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tuner_timestamp ON tuner_logs(timestamp)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_tuner_event ON tuner_logs(event_type)")
                
                conn.commit()
                logger.debug("[SQLiteManager] Tables created successfully")
                
            finally:
                conn.close()
    
    def save_snapshot(self, snapshot_dict: Dict[str, Any]) -> bool:
        """
        Сохраняет снимок сделки в БД.
        
        :param snapshot_dict: Словарь из TradeSnapshot.to_dict()
        :return: True если успешно
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Преобразуем списки/словари в JSON строки
                analysis_cards_json = json.dumps(snapshot_dict.get('analysis_cards', []))
                scenario_card_json = json.dumps(snapshot_dict.get('scenario_card') or {})
                risk_card_json = json.dumps(snapshot_dict.get('risk_card') or {})
                market_context_json = json.dumps(snapshot_dict.get('market_context') or {})
                config_snapshot_json = json.dumps(snapshot_dict.get('config_snapshot') or {})
                tuner_analysis_json = json.dumps(snapshot_dict.get('tuner_analysis') or {})
                
                cursor.execute("""
                    INSERT OR REPLACE INTO trade_snapshots (
                        snapshot_id, timestamp_open, timestamp_close, scenario_id, risk_id, order_id,
                        symbol, direction, entry_price, exit_price, highest_price, lowest_price,
                        leverage, position_size_usd, quantity,
                        pnl_usd, pnl_pct, commission_usd, slippage_usd, net_pnl_usd,
                        duration_seconds, exit_reason,
                        mae_pct, mae_price, mfe_pct, mfe_price,
                        trailing_stop_activated, trailing_stop_distance_pct, trailing_stop_activation_pct,
                        analysis_cards, scenario_card, risk_card, market_context, config_snapshot,
                        expected_profit_usd, profit_deviation_usd, profit_deviation_pct, tuner_analysis
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot_dict.get('snapshot_id'),
                    snapshot_dict.get('timestamp_open'),
                    snapshot_dict.get('timestamp_close'),
                    snapshot_dict.get('scenario_id'),
                    snapshot_dict.get('risk_id'),
                    snapshot_dict.get('order_id'),
                    snapshot_dict.get('symbol'),
                    snapshot_dict.get('direction'),
                    snapshot_dict.get('entry_price'),
                    snapshot_dict.get('exit_price'),
                    snapshot_dict.get('highest_price'),
                    snapshot_dict.get('lowest_price'),
                    snapshot_dict.get('leverage'),
                    snapshot_dict.get('position_size_usd'),
                    snapshot_dict.get('quantity'),
                    snapshot_dict.get('pnl_usd'),
                    snapshot_dict.get('pnl_pct'),
                    snapshot_dict.get('commission_usd'),
                    snapshot_dict.get('slippage_usd'),
                    snapshot_dict.get('net_pnl_usd'),
                    snapshot_dict.get('duration_seconds'),
                    snapshot_dict.get('exit_reason'),
                    snapshot_dict.get('mae_pct'),
                    snapshot_dict.get('mae_price'),
                    snapshot_dict.get('mfe_pct'),
                    snapshot_dict.get('mfe_price'),
                    1 if snapshot_dict.get('trailing_stop_activated') else 0,
                    snapshot_dict.get('trailing_stop_distance_pct'),
                    snapshot_dict.get('trailing_stop_activation_pct'),
                    analysis_cards_json,
                    scenario_card_json,
                    risk_card_json,
                    market_context_json,
                    config_snapshot_json,
                    snapshot_dict.get('expected_profit_usd'),
                    snapshot_dict.get('profit_deviation_usd'),
                    snapshot_dict.get('profit_deviation_pct'),
                    tuner_analysis_json
                ))
                
                conn.commit()
                logger.debug(f"[SQLiteManager] Snapshot saved: {snapshot_dict.get('snapshot_id')}")
                
                # Проверка размера и ротация
                self._rotate_snapshots_if_needed(conn)
                
                return True
                
            except Exception as e:
                logger.error(f"[SQLiteManager] Error saving snapshot: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()
    
    def save_analysis_card(self, card_dict: Dict[str, Any], snapshot_id: Optional[str] = None) -> bool:
        """
        Сохраняет карточку анализа в БД.
        
        :param card_dict: Словарь из AnalysisCard.to_dict()
        :param snapshot_id: Опциональная ссылка на снимок сделки
        :return: True если успешно
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Преобразуем значение в JSON если это словарь/список
                value = card_dict.get('value')
                if isinstance(value, (dict, list)):
                    value_json = json.dumps(value)
                else:
                    value_json = str(value) if value is not None else None
                
                input_params_json = json.dumps(card_dict.get('input_params') or {})
                
                cursor.execute("""
                    INSERT OR REPLACE INTO analysis_cards (
                        card_id, timestamp, analyzer_id, symbol, price, timeframe,
                        analysis_type, value, predicted_price, predicted_time,
                        horizon_seconds, confidence, trust_score, combined_probability,
                        input_params, calculation_method, snapshot_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    card_dict.get('card_id'),
                    card_dict.get('timestamp'),
                    card_dict.get('analyzer_id'),
                    card_dict.get('symbol'),
                    card_dict.get('price'),
                    card_dict.get('timeframe'),
                    card_dict.get('analysis_type'),
                    value_json,
                    card_dict.get('predicted_price'),
                    card_dict.get('predicted_time'),
                    card_dict.get('horizon_seconds'),
                    card_dict.get('confidence'),
                    card_dict.get('trust_score'),
                    card_dict.get('combined_probability'),
                    input_params_json,
                    card_dict.get('calculation_method'),
                    snapshot_id
                ))
                
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"[SQLiteManager] Error saving analysis card: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()
    
    def log_tuner_event(
        self,
        event_type: str,
        description: str,
        old_value: Optional[Dict] = None,
        new_value: Optional[Dict] = None,
        impact_score: float = 0.0,
        snapshot_id: Optional[str] = None
    ) -> bool:
        """
        Записывает лог события автотюнера.
        
        :param event_type: "config_change", "trust_update", "analyzer_impact"
        :param description: Описание изменения
        :param old_value: Старое значение (словарь)
        :param new_value: Новое значение (словарь)
        :param impact_score: Оценка влияния на результат
        :param snapshot_id: Связанный снимок сделки
        :return: True если успешно
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                old_value_json = json.dumps(old_value or {})
                new_value_json = json.dumps(new_value or {})
                timestamp = datetime.utcnow().isoformat()
                
                cursor.execute("""
                    INSERT INTO tuner_logs (
                        timestamp, event_type, description, old_value, new_value,
                        impact_score, snapshot_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    event_type,
                    description,
                    old_value_json,
                    new_value_json,
                    impact_score,
                    snapshot_id
                ))
                
                conn.commit()
                logger.debug(f"[SQLiteManager] Tuner log saved: {event_type}")
                
                # Проверка размера и ротация
                self._rotate_logs_if_needed(conn)
                
                return True
                
            except Exception as e:
                logger.error(f"[SQLiteManager] Error logging tuner event: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()
    
    def get_snapshots(
        self,
        symbol: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        min_pnl: Optional[float] = None,
        max_pnl: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Получает снимки сделок с фильтрацией.
        
        :param symbol: Фильтр по символу
        :param limit: Максимальное количество записей
        :param offset: Смещение
        :param min_pnl: Минимальный PnL
        :param max_pnl: Максимальный PnL
        :return: Список словарей снимков
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                query = "SELECT * FROM trade_snapshots WHERE 1=1"
                params = []
                
                if symbol:
                    query += " AND symbol = ?"
                    params.append(symbol)
                
                if min_pnl is not None:
                    query += " AND pnl_usd >= ?"
                    params.append(min_pnl)
                
                if max_pnl is not None:
                    query += " AND pnl_usd <= ?"
                    params.append(max_pnl)
                
                query += " ORDER BY timestamp_open DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                snapshots = []
                for row in rows:
                    snapshot = dict(row)
                    # Парсим JSON поля обратно
                    snapshot['analysis_cards'] = json.loads(snapshot.get('analysis_cards', '[]'))
                    snapshot['scenario_card'] = json.loads(snapshot.get('scenario_card', '{}'))
                    snapshot['risk_card'] = json.loads(snapshot.get('risk_card', '{}'))
                    snapshot['market_context'] = json.loads(snapshot.get('market_context', '{}'))
                    snapshot['config_snapshot'] = json.loads(snapshot.get('config_snapshot', '{}'))
                    snapshot['tuner_analysis'] = json.loads(snapshot.get('tuner_analysis', '{}'))
                    snapshot['trailing_stop_activated'] = bool(snapshot.get('trailing_stop_activated', 0))
                    snapshots.append(snapshot)
                
                return snapshots
                
            finally:
                conn.close()
    
    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Получает снимок по ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM trade_snapshots WHERE snapshot_id = ?", (snapshot_id,))
                row = cursor.fetchone()
                
                if row:
                    snapshot = dict(row)
                    snapshot['analysis_cards'] = json.loads(snapshot.get('analysis_cards', '[]'))
                    snapshot['scenario_card'] = json.loads(snapshot.get('scenario_card', '{}'))
                    snapshot['risk_card'] = json.loads(snapshot.get('risk_card', '{}'))
                    snapshot['market_context'] = json.loads(snapshot.get('market_context', '{}'))
                    snapshot['config_snapshot'] = json.loads(snapshot.get('config_snapshot', '{}'))
                    snapshot['tuner_analysis'] = json.loads(snapshot.get('tuner_analysis', '{}'))
                    snapshot['trailing_stop_activated'] = bool(snapshot.get('trailing_stop_activated', 0))
                    return snapshot
                return None
                
            finally:
                conn.close()
    
    def get_tuner_logs(
        self,
        event_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Получает логи автотюнера."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                query = "SELECT * FROM tuner_logs WHERE 1=1"
                params = []
                
                if event_type:
                    query += " AND event_type = ?"
                    params.append(event_type)
                
                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                logs = []
                for row in rows:
                    log = dict(row)
                    log['old_value'] = json.loads(log.get('old_value', '{}'))
                    log['new_value'] = json.loads(log.get('new_value', '{}'))
                    logs.append(log)
                
                return logs
                
            finally:
                conn.close()
    
    def _get_table_size(self, conn: sqlite3.Connection, table_name: str) -> int:
        """Получает размер таблицы в байтах."""
        cursor = conn.cursor()
        cursor.execute(f"SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def _rotate_snapshots_if_needed(self, conn: sqlite3.Connection):
        """Удаляет старые снимки если размер превышает лимит."""
        current_size = self._get_table_size(conn, 'trade_snapshots')
        
        if current_size > self.max_snapshots_size_bytes:
            logger.warning(f"[SQLiteManager] Snapshots size ({current_size / 1024 / 1024:.2f}MB) exceeds limit. Rotating...")
            
            cursor = conn.cursor()
            # Удаляем oldest 10% записей
            cursor.execute("""
                DELETE FROM trade_snapshots 
                WHERE snapshot_id IN (
                    SELECT snapshot_id FROM trade_snapshots 
                    ORDER BY timestamp_open ASC 
                    LIMIT MAX(1, (SELECT COUNT(*) FROM trade_snapshots) / 10)
                )
            """)
            conn.commit()
            logger.info("[SQLiteManager] Snapshots rotation completed")
    
    def _rotate_logs_if_needed(self, conn: sqlite3.Connection):
        """Удаляет старые логи если размер превышает лимит."""
        current_size = self._get_table_size(conn, 'tuner_logs')
        
        if current_size > self.max_logs_size_bytes:
            logger.warning(f"[SQLiteManager] Logs size ({current_size / 1024 / 1024:.2f}MB) exceeds limit. Rotating...")
            
            cursor = conn.cursor()
            # Удаляем oldest 10% записей
            cursor.execute("""
                DELETE FROM tuner_logs 
                WHERE log_id IN (
                    SELECT log_id FROM tuner_logs 
                    ORDER BY timestamp ASC 
                    LIMIT MAX(1, (SELECT COUNT(*) FROM tuner_logs) / 10)
                )
            """)
            conn.commit()
            logger.info("[SQLiteManager] Logs rotation completed")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Возвращает статистику по базе данных."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                # Количество записей
                cursor.execute("SELECT COUNT(*) FROM trade_snapshots")
                snapshots_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM analysis_cards")
                analysis_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM tuner_logs")
                logs_count = cursor.fetchone()[0]
                
                # Размер БД
                db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
                
                # Статистика PnL
                cursor.execute("SELECT AVG(pnl_usd), SUM(pnl_usd), MAX(pnl_usd), MIN(pnl_usd) FROM trade_snapshots")
                row = cursor.fetchone()
                avg_pnl = row[0] or 0
                total_pnl = row[1] or 0
                max_pnl = row[2] or 0
                min_pnl = row[3] or 0
                
                return {
                    "db_path": str(self.db_path),
                    "db_size_mb": round(db_size / 1024 / 1024, 2),
                    "snapshots_count": snapshots_count,
                    "analysis_cards_count": analysis_count,
                    "tuner_logs_count": logs_count,
                    "total_pnl_usd": round(total_pnl, 2),
                    "avg_pnl_usd": round(avg_pnl, 2),
                    "best_trade_usd": round(max_pnl, 2),
                    "worst_trade_usd": round(min_pnl, 2)
                }
                
            finally:
                conn.close()
