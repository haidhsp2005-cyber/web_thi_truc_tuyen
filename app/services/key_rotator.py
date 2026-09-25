"""
Rotating API Key Manager - Xoay vòng API Key thông minh
Hỗ trợ: Round-robin, Automatic Failover, Cooldown, Health Dashboard
"""
import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class KeyStatus(str, Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    ERROR = "error"

@dataclass
class APIKeyInfo:
    key: str
    status: KeyStatus = KeyStatus.ACTIVE
    success_count: int = 0
    fail_count: int = 0
    cooldown_until: float = 0.0
    last_used: float = 0.0
    label: str = ""

    def is_available(self) -> bool:
        if self.status == KeyStatus.COOLDOWN:
            if time.time() > self.cooldown_until:
                self.status = KeyStatus.ACTIVE
                return True
            return False
        return self.status == KeyStatus.ACTIVE

    def to_dict(self) -> dict:
        remaining_cooldown = max(0, self.cooldown_until - time.time())
        return {
            "label": self.label or f"Key ...{self.key[-6:]}",
            "status": self.status,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "remaining_cooldown_seconds": round(remaining_cooldown),
            "last_used": self.last_used,
        }


class RotatingKeyManager:
    """
    Quản lý danh sách API key với thuật toán xoay vòng Round-robin + Failover tự động.
    Thread-safe cho môi trường asyncio.
    """

    def __init__(self, api_keys: List[str], cooldown_seconds: int = 120):
        if not api_keys:
            raise ValueError("Cần ít nhất 1 API key!")
        
        self._keys: List[APIKeyInfo] = []
        for i, key in enumerate(api_keys):
            key = key.strip()
            if key:
                self._keys.append(APIKeyInfo(
                    key=key,
                    label=f"Key #{i+1} ...{key[-6:] if len(key) > 6 else key}"
                ))
        
        self._current_index = 0
        self._lock = asyncio.Lock()
        self._cooldown_seconds = cooldown_seconds
        logger.info(f"KeyRotator khởi tạo với {len(self._keys)} key(s).")

    async def get_next_key(self) -> Optional[str]:
        """Lấy key khả dụng tiếp theo theo Round-robin."""
        async with self._lock:
            total = len(self._keys)
            for _ in range(total):
                idx = self._current_index % total
                self._current_index = (self._current_index + 1) % total
                key_info = self._keys[idx]
                if key_info.is_available():
                    key_info.last_used = time.time()
                    return key_info.key
            logger.warning("Tất cả API keys đang trong trạng thái Cooldown!")
            return None

    async def report_success(self, key: str):
        """Báo cáo key gọi API thành công."""
        async with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.success_count += 1
                    break

    async def report_failure(self, key: str, error_code: int = 429):
        """
        Báo cáo key gặp lỗi.
        Lỗi 429 (Rate Limit) -> Cooldown
        Lỗi 403 (Forbidden) -> Đánh dấu Error
        """
        async with self._lock:
            for k in self._keys:
                if k.key == key:
                    k.fail_count += 1
                    if error_code == 429:
                        k.status = KeyStatus.COOLDOWN
                        k.cooldown_until = time.time() + self._cooldown_seconds
                        logger.warning(
                            f"{k.label}: Bị rate-limit (429). "
                            f"Cooldown {self._cooldown_seconds}s, chuyển sang key khác."
                        )
                    elif error_code in (400, 401, 403):
                        k.status = KeyStatus.ERROR
                        logger.error(f"{k.label}: Lỗi {error_code} - Key có thể không hợp lệ.")
                    break

    def get_status(self) -> List[dict]:
        """Trả về trạng thái tất cả các key cho dashboard."""
        return [k.to_dict() for k in self._keys]

    def add_key(self, new_key: str):
        """Thêm key mới vào pool."""
        new_key = new_key.strip()
        if not any(k.key == new_key for k in self._keys):
            self._keys.append(APIKeyInfo(
                key=new_key,
                label=f"Key #{len(self._keys)+1} ...{new_key[-6:]}"
            ))
            logger.info(f"Đã thêm key mới: ...{new_key[-6:]}")

    def remove_key(self, key_partial: str):
        """Xóa key khỏi pool dựa trên 6 ký tự cuối."""
        self._keys = [k for k in self._keys if not k.key.endswith(key_partial)]

    @property
    def active_key_count(self) -> int:
        return sum(1 for k in self._keys if k.is_available())

    @property 
    def total_key_count(self) -> int:
        return len(self._keys)


# Global instance, được khởi tạo trong main.py
_key_manager: Optional[RotatingKeyManager] = None

def get_key_manager() -> RotatingKeyManager:
    global _key_manager
    if _key_manager is None:
        raise RuntimeError("KeyManager chưa được khởi tạo! Gọi init_key_manager() trước.")
    return _key_manager

def init_key_manager(api_keys: List[str], cooldown_seconds: int = 120):
    global _key_manager
    _key_manager = RotatingKeyManager(api_keys, cooldown_seconds)
    return _key_manager
