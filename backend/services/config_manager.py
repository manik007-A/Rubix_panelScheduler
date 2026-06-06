"""
RUBIX PANEL SCHEDULER - Configuration Manager
Manages application configuration with caching and validation.
"""

import threading
from typing import Any, Dict, List, Optional
from datetime import datetime, time
from .sheets_service import get_sheets_service, SheetsService

_logger = __import__('logging').getLogger(__name__)


class ConfigManager:
    """Thread-safe configuration manager with caching."""
    
    _instance = None
    _lock = threading.Lock()
    _cache = {}
    _cache_timestamp = None
    _cache_ttl = 30  # seconds
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self._sheets_service = get_sheets_service()
    
    def _is_cache_valid(self) -> bool:
        """Check if the cache is still valid."""
        if self._cache_timestamp is None:
            return False
        elapsed = (datetime.now() - self._cache_timestamp).total_seconds()
        return elapsed < self._cache_ttl
    
    def _refresh_cache(self):
        """Refresh the configuration cache from Google Sheets."""
        try:
            self._cache = self._sheets_service.get_all_config()
            self._cache_timestamp = datetime.now()
        except Exception as e:
            _logger.error(f"Failed to refresh config cache: {e}")
            if not self._cache:
                raise
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        if not self._is_cache_valid():
            with self._lock:
                self._refresh_cache()
        
        value = self._cache.get(key)
        if value is None:
            return default
        return value
    
    def set(self, key: str, value: str) -> bool:
        """Set a configuration value and invalidate cache."""
        try:
            result = self._sheets_service.update_config(key, value)
            if result:
                with self._lock:
                    self._cache[key] = value
                    self._cache_timestamp = datetime.now()
            return result
        except Exception as e:
            _logger.error(f"Failed to set config {key}: {e}")
            return False
    
    def get_all(self) -> Dict[str, str]:
        """Get all configuration values."""
        if not self._is_cache_valid():
            with self._lock:
                self._refresh_cache()
        return self._cache.copy()
    
    def invalidate_cache(self):
        """Force cache invalidation."""
        with self._lock:
            self._cache = {}
            self._cache_timestamp = None
    
    # Specific configuration getters with type conversion
    def get_working_hours_start(self) -> time:
        """Get working hours start time."""
        value = self.get('working_hours_start', '09:00')
        parts = value.split(':')
        return time(int(parts[0]), int(parts[1]))
    
    def get_working_hours_end(self) -> time:
        """Get working hours end time."""
        value = self.get('working_hours_end', '18:00')
        parts = value.split(':')
        return time(int(parts[0]), int(parts[1]))
    
    def get_slot_duration(self) -> int:
        """Get slot duration in minutes."""
        return int(self.get('slot_duration', '30'))
    
    def get_max_duration(self) -> int:
        """Get maximum booking duration in minutes."""
        return int(self.get('max_duration', '90'))
    
    def get_min_duration(self) -> int:
        """Get minimum booking duration in minutes."""
        return int(self.get('min_duration', '30'))
    
    def is_auto_promote_enabled(self) -> bool:
        """Check if auto-promotion from waitlist is enabled."""
        return self.get('auto_promote_waitlist', 'TRUE').upper() == 'TRUE'
    
    def get_panels(self) -> List[str]:
        """Get list of enabled panels."""
        panels_str = self.get('panels', 'Panel1,Panel2,Panel3')
        return [p.strip() for p in panels_str.split(',') if p.strip()]
    
    def get_blocked_slots(self) -> Dict[str, List[str]]:
        """Get blocked slots as a dictionary of panel -> list of time slots."""
        blocked_str = self.get('blocked_slots', '')
        if not blocked_str:
            return {}
        
        blocked = {}
        for item in blocked_str.split(';'):
            if ':' in item:
                panel, slot = item.split(':', 1)
                panel = panel.strip()
                if panel not in blocked:
                    blocked[panel] = []
                blocked[panel].append(slot.strip())
        return blocked
    
    def get_last_booking_counter(self) -> int:
        """Get the last used booking counter."""
        return int(self.get('last_booking_counter', '0'))
    
    def generate_time_slots(self, date_str: str = None) -> List[str]:
        """Generate time slots for a given date based on configuration."""
        start_time = self.get_working_hours_start()
        end_time = self.get_working_hours_end()
        slot_duration = self.get_slot_duration()
        
        slots = []
        current = datetime.combine(
            datetime.now().date() if date_str is None else datetime.strptime(date_str, '%Y-%m-%d').date(),
            start_time
        )
        end = datetime.combine(
            datetime.now().date() if date_str is None else datetime.strptime(date_str, '%Y-%m-%d').date(),
            end_time
        )
        
        from datetime import timedelta
        while current < end:
            slot_end = current + timedelta(minutes=slot_duration)
            if slot_end <= end:
                slots.append(f"{current.strftime('%H:%M')}-{slot_end.strftime('%H:%M')}")
            current = slot_end
        
        return slots
    
    def add_blocked_slot(self, panel: str, time_slot: str) -> bool:
        """Add a blocked slot."""
        blocked = self.get_blocked_slots()
        if panel not in blocked:
            blocked[panel] = []
        if time_slot not in blocked[panel]:
            blocked[panel].append(time_slot)
        
        # Serialize back to string
        blocked_str = ';'.join(
            f"{p}:{s}" for p, slots in blocked.items() for s in slots
        )
        return self.set('blocked_slots', blocked_str)
    
    def remove_blocked_slot(self, panel: str, time_slot: str) -> bool:
        """Remove a blocked slot."""
        blocked = self.get_blocked_slots()
        if panel in blocked and time_slot in blocked[panel]:
            blocked[panel].remove(time_slot)
            if not blocked[panel]:
                del blocked[panel]
            
            blocked_str = ';'.join(
                f"{p}:{s}" for p, slots in blocked.items() for s in slots
            )
            return self.set('blocked_slots', blocked_str)
        return False
    
    def is_slot_blocked(self, panel: str, time_slot: str) -> bool:
        """Check if a slot is blocked for a panel."""
        blocked = self.get_blocked_slots()
        return panel in blocked and time_slot in blocked[panel]
    
    def enable_panel(self, panel: str) -> bool:
        """Enable a panel."""
        panels = self.get_panels()
        if panel not in panels:
            panels.append(panel)
            return self.set('panels', ','.join(panels))
        return False
    
    def disable_panel(self, panel: str) -> bool:
        """Disable a panel (remove from active panels)."""
        panels = self.get_panels()
        if panel in panels:
            panels.remove(panel)
            return self.set('panels', ','.join(panels))
        return False
    
    def is_panel_enabled(self, panel: str) -> bool:
        """Check if a panel is enabled."""
        return panel in self.get_panels()


# Convenience function
def get_config() -> ConfigManager:
    """Get the singleton ConfigManager instance."""
    return ConfigManager()