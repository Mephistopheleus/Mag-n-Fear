"""
Модуль ротации логов и базы данных.
Ограничивает размер логов до 2 МБ и БД до 5 МБ.
"""
import os
import sqlite3
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class DataRotator:
    """Управляет ротацией логов и базы данных с ограничениями по размеру."""
    
    MAX_LOG_SIZE_MB = 2
    MAX_DB_SIZE_MB = 5
    MAX_TRADES_ROWS = 1000  # Максимум записей в trades
    MAX_LAB_SNAPSHOTS_ROWS = 500  # Максимум записей в lab_snapshots
    
    def __init__(self, log_file: Optional[str] = None, db_path: Optional[str] = None):
        """
        Инициализация ротатора.
        
        Args:
            log_file: Путь к файлу логов
            db_path: Путь к файлу базы данных
        """
        self.log_file = Path(log_file) if log_file else None
        self.db_path = Path(db_path) if db_path else None
        
    def rotate_logs(self) -> bool:
        """
        Ротирует логи, если они превышают MAX_LOG_SIZE_MB.
        Старые логи удаляются, остается только текущий файл.
        
        Returns:
            True если ротация выполнена, False если не требовалась
        """
        if not self.log_file or not self.log_file.exists():
            return False
            
        log_size_mb = self.log_file.stat().st_size / (1024 * 1024)
        
        if log_size_mb >= self.MAX_LOG_SIZE_MB:
            logger.info(f"Log file {self.log_file} size ({log_size_mb:.2f} MB) exceeds limit ({self.MAX_LOG_SIZE_MB} MB). Rotating...")
            
            # Создаем резервную копию с timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{self.log_file.stem}_{timestamp}.log"
            backup_path = self.log_file.parent / backup_name
            
            try:
                # Перемещаем текущий лог в backup
                shutil.move(str(self.log_file), str(backup_path))
                
                # Создаем новый пустой лог
                self.log_file.touch()
                
                # Удаляем старые backup'ы, если их больше 3
                old_backups = sorted(
                    self.log_file.parent.glob(f"{self.log_file.stem}_*.log"),
                    key=lambda p: p.stat().st_mtime
                )
                
                while len(old_backups) > 3:
                    oldest = old_backups.pop(0)
                    oldest.unlink()
                    logger.debug(f"Deleted old log backup: {oldest}")
                
                logger.info(f"Log rotation completed. New log file created.")
                return True
                
            except Exception as e:
                logger.error(f"Error rotating logs: {e}")
                return False
        
        return False
    
    def rotate_database(self) -> bool:
        """
        Ротирует базу данных, если она превышает MAX_DB_SIZE_MB.
        Удаляет старые записи, оставляя только последние N записей в каждой таблице.
        
        Returns:
            True если ротация выполнена, False если не требовалась
        """
        if not self.db_path or not self.db_path.exists():
            return False
            
        db_size_mb = self.db_path.stat().st_size / (1024 * 1024)
        
        if db_size_mb >= self.MAX_DB_SIZE_MB:
            logger.info(f"Database {self.db_path} size ({db_size_mb:.2f} MB) exceeds limit ({self.MAX_DB_SIZE_MB} MB). Rotating...")
            
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                
                # Получаем количество записей перед чисткой
                cursor.execute("SELECT COUNT(*) FROM trades")
                trades_count_before = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM lab_snapshots")
                snapshots_count_before = cursor.fetchone()[0]
                
                # Удаляем старые записи из trades, оставляя только последние MAX_TRADES_ROWS
                if trades_count_before > self.MAX_TRADES_ROWS:
                    cursor.execute("""
                        DELETE FROM trades 
                        WHERE id NOT IN (
                            SELECT id FROM trades 
                            ORDER BY timestamp_close DESC 
                            LIMIT ?
                        )
                    """, (self.MAX_TRADES_ROWS,))
                    trades_deleted = trades_count_before - self.MAX_TRADES_ROWS
                    logger.info(f"Deleted {trades_deleted} old trade records")
                
                # Удаляем старые записи из lab_snapshots, оставляя только последние MAX_LAB_SNAPSHOTS_ROWS
                if snapshots_count_before > self.MAX_LAB_SNAPSHOTS_ROWS:
                    cursor.execute("""
                        DELETE FROM lab_snapshots 
                        WHERE id NOT IN (
                            SELECT id FROM lab_snapshots 
                            ORDER BY timestamp DESC 
                            LIMIT ?
                        )
                    """, (self.MAX_LAB_SNAPSHOTS_ROWS,))
                    snapshots_deleted = snapshots_count_before - self.MAX_LAB_SNAPSHOTS_ROWS
                    logger.info(f"Deleted {snapshots_deleted} old lab snapshot records")
                
                conn.commit()
                
                # Выполняем VACUUM для уменьшения размера файла БД
                cursor.execute("VACUUM")
                conn.commit()
                conn.close()
                
                # Проверяем новый размер
                new_size_mb = self.db_path.stat().st_size / (1024 * 1024)
                logger.info(f"Database rotation completed. New size: {new_size_mb:.2f} MB")
                
                return True
                
            except Exception as e:
                logger.error(f"Error rotating database: {e}")
                return False
        
        return False
    
    def check_and_rotate(self) -> dict:
        """
        Проверяет и ротирует логи и БД при необходимости.
        
        Returns:
            Dict с результатами проверки
        """
        results = {
            "log_rotated": False,
            "db_rotated": False,
            "log_size_mb": 0.0,
            "db_size_mb": 0.0
        }
        
        if self.log_file and self.log_file.exists():
            results["log_size_mb"] = round(self.log_file.stat().st_size / (1024 * 1024), 2)
            results["log_rotated"] = self.rotate_logs()
        
        if self.db_path and self.db_path.exists():
            results["db_size_mb"] = round(self.db_path.stat().st_size / (1024 * 1024), 2)
            results["db_rotated"] = self.rotate_database()
        
        return results


def setup_rotating_logger(log_file: str, level: int = logging.INFO) -> logging.Logger:
    """
    Настраивает логгер с автоматической ротацией.
    
    Args:
        log_file: Путь к файлу логов
        level: Уровень логирования
        
    Returns:
        Настроенный logger
    """
    # Настраиваем корневой логгер, чтобы все модули писали в файл
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Очищаем существующие handlers
    root_logger.handlers.clear()
    
    # File handler без ротации (ротацию делаем вручную через DataRotator)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(level)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    root_logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    return root_logger
