"""
Health Check - Мониторинг здоровья системы.
Проверяет активность коннекторов, задержки и состояние потоков.
"""
import asyncio
import time
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)

class HealthCheck:
    def __init__(self, config: Any):
        self.config = config
        # Config теперь Pydantic модель, доступ через атрибуты
        # Проверяем наличие секции health в конфиге
        try:
            health_cfg = getattr(config, 'health', None)
            if health_cfg:
                self.max_latency_ms = getattr(health_cfg, 'max_latency_ms', 500)
                self.check_interval_sec = getattr(health_cfg, 'check_interval_sec', 30)
            else:
                self.max_latency_ms = 500
                self.check_interval_sec = 30
        except Exception:
            self.max_latency_ms = 500
            self.check_interval_sec = 30
        
        self.components: Dict[str, float] = {}  # {name: last_heartbeat}
        self.is_healthy = True

    def heartbeat(self, component_name: str):
        """Регистрирует пульс компонента."""
        self.components[component_name] = time.time()

    def get_status(self) -> Dict:
        """Возвращает текущий статус всех компонентов."""
        now = time.time()
        status = {
            "overall": True,
            "components": {}
        }
        
        for name, last_beat in self.components.items():
            latency = (now - last_beat) * 1000  # ms
            is_ok = latency < self.max_latency_ms
            
            status["components"][name] = {
                "status": "OK" if is_ok else "LAGGING",
                "latency_ms": round(latency, 2),
                "last_seen": last_beat
            }
            
            if not is_ok:
                status["overall"] = False
                logger.warning(f"Component {name} is lagging: {latency:.0f}ms")

        self.is_healthy = status["overall"]
        return status

    async def start_monitoring(self):
        """Фоновый цикл мониторинга."""
        while True:
            status = self.get_status()
            if not status["overall"]:
                logger.error("System UNHEALTHY! Check components.")
                # TODO: Trigger alert via Notifier
            await asyncio.sleep(self.check_interval_sec)
