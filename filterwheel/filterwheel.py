import ctypes
import time
import json
from pathlib import Path

class FilterWheel:
    def __init__(self, library_path='libEFWFilter.so.1.7', config_path=None):
        # Preload libudev
        ctypes.CDLL("libudev.so.1", mode=ctypes.RTLD_GLOBAL)
        self.lib = ctypes.CDLL(library_path)
        
        # Initialize
        num = self.lib.EFWGetNum()
        if num == 0:
            raise RuntimeError("No filter wheel found")
        
        self.wheel_id = ctypes.c_int()
        self.lib.EFWGetID(0, ctypes.byref(self.wheel_id))
        self.lib.EFWOpen(self.wheel_id.value)
        time.sleep(0.5)  # Allow wheel to initialize
        
        # Get number of slots
        self.num_slots = self._get_slot_count()
        
        # Load filter mapping
        self.filters = {}
        if config_path:
            self.load_config(config_path)
    
    def _get_slot_count(self):
        # EFW_INFO struct: int ID, char Name[64], int slotNum
        class EFW_INFO(ctypes.Structure):
            _fields_ = [("ID", ctypes.c_int),
                        ("Name", ctypes.c_char * 64),
                        ("slotNum", ctypes.c_int)]
        
        info = EFW_INFO()
        self.lib.EFWGetProperty(self.wheel_id.value, ctypes.byref(info))
        return info.slotNum
    
    def load_config(self, config_path):
        """Load filter mapping from JSON file.
        
        Expected format:
        {
            "0": "Luminance",
            "1": "Red",
            "2": "Green",
            "3": "Blue",
            "4": "Ha",
            "5": "OIII",
            "6": "SII"
        }
        """
        with open(config_path) as f:
            config = json.load(f)
        self.filters = {int(k): v for k, v in config.items()}
    
    def save_config(self, config_path):
        """Save current filter mapping to JSON file."""
        with open(config_path, 'w') as f:
            json.dump({str(k): v for k, v in self.filters.items()}, f, indent=2)
    
    @property
    def position(self):
        """Get current position (0-indexed)."""
        pos = ctypes.c_int()
        self.lib.EFWGetPosition(self.wheel_id.value, ctypes.byref(pos))
        return pos.value
    
    @position.setter
    def position(self, pos):
        """Set position (0-indexed)."""
        if pos < 0 or pos >= self.num_slots:
            raise ValueError(f"Position must be 0-{self.num_slots - 1}")
        result = self.lib.EFWSetPosition(self.wheel_id.value, pos)
        if result != 0:
            raise RuntimeError(f"Failed to set position: error {result}")
    
    @property
    def filter(self):
        """Get current filter name."""
        pos = self.position
        return self.filters.get(pos, f"Position {pos}")
    
    @filter.setter
    def filter(self, name):
        """Set filter by name."""
        for pos, filter_name in self.filters.items():
            if filter_name.lower() == name.lower():
                self.position = pos
                return
        raise ValueError(f"Unknown filter: {name}. Available: {list(self.filters.values())}")
    
    def wait_for_move(self, timeout=30):
        """Wait for wheel to finish moving."""
        start = time.time()
        while time.time() - start < timeout:
            if self.position >= 0:  # -1 means moving
                return True
            time.sleep(0.1)
        raise TimeoutError("Filter wheel move timed out")
    
    def goto(self, filter_or_position):
        """Go to filter by name or position number."""
        if isinstance(filter_or_position, int):
            self.position = filter_or_position
        else:
            self.filter = filter_or_position
        self.wait_for_move()
    
    def close(self):
        """Close connection to filter wheel."""
        self.lib.EFWClose(self.wheel_id.value)
    
    def __repr__(self):
        return f"FilterWheel(position={self.position}, filter='{self.filter}', slots={self.num_slots})"
