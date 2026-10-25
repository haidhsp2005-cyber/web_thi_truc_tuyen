import sys
import os

# Đưa thư mục gốc của dự án vào sys.path để module app được tìm thấy
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.main import app
