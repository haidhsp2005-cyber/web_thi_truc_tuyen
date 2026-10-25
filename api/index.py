import sys
import os

# Dua thu muc goc cua du an vao sys.path de module app duoc tim thay
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.main import app

# Vercel ho tro ca app lan handler
handler = app
