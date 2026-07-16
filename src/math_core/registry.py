"""
Реестр индикаторов.
Управляет подключением и загрузкой индикаторов через конфиг.
Позволяет добавлять новые индикаторы без изменения ядра.
"""
from typing import Dict, Type, List, Any
import importlib
import logging

from .base_indicator import BaseIndicator

logger = logging.getLogger(__name__)


class IndicatorRegistry:
    """
    Реестр для динамической регистрации и загрузки индикаторов.
    """
    
    _registry: Dict[str, Type[BaseIndicator]] = {}

    @classmethod
    def register(cls, indicator_class: Type[BaseIndicator]):
        """Регистрация класса индикатора."""
        cls._registry[indicator_class.__name__] = indicator_class
        logger.info(f"Зарегистрирован индикатор: {indicator_class.__name__}")

    @classmethod
    def get(cls, name: str) -> Type[BaseIndicator]:
        """Получение класса индикатора по имени."""
        if name not in cls._registry:
            raise ValueError(f"Индикатор '{name}' не найден в реестре.")
        return cls._registry[name]

    @classmethod
    def list_indicators(cls) -> List[str]:
        """Список всех зарегистрированных индикаторов."""
        return list(cls._registry.keys())

    @classmethod
    def load_from_config(cls, config: Dict[str, Any]) -> List[BaseIndicator]:
        """
        Загрузка активных индикаторов из конфига.
        
        Args:
            config: Словарь из config.yaml, секция 'indicators'.
                    Пример:
                    {
                        "enabled": ["RSI", "G_TrendChannel"],
                        "params": {
                            "RSI": {"period": 14},
                            "G_TrendChannel": {"std_dev": 2.0}
                        }
                    }
        
        Returns:
            Список экземпляров индикаторов.
        """
        indicators = []
        enabled_names = config.get('enabled', [])
        params_map = config.get('params', {})

        for name in enabled_names:
            try:
                # Динамический импорт модуля (предполагаем, что класс лежит в файле с именем класса в нижнем регистре)
                module_name = f"src.math_core.{name.lower()}"
                module = importlib.import_module(module_name)
                indicator_class = getattr(module, name)
                
                # Получаем параметры для этого индикатора
                indicator_params = params_map.get(name, {})
                
                # Создаем экземпляр
                instance = indicator_class(name=name, config=indicator_params)
                indicators.append(instance)
                
                logger.info(f"Загружен индикатор: {name} с параметрами {indicator_params}")
                
            except ModuleNotFoundError:
                logger.error(f"Модуль для индикатора '{name}' не найден (ожидался src/math_core/{name.lower()}.py)")
            except AttributeError:
                logger.error(f"Класс '{name}' не найден в модуле src/math_core/{name.lower()}.py")
            except Exception as e:
                logger.error(f"Ошибка при загрузке индикатора '{name}': {e}")

        return indicators

# Декоратор для удобной регистрации
def register_indicator(cls):
    """Декоратор для автоматической регистрации индикатора."""
    IndicatorRegistry.register(cls)
    return cls
