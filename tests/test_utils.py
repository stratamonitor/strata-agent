import sys
import os
from unittest.mock import MagicMock

# --- MOCKING ---
# 1. Мокаем streamlit
mock_streamlit = MagicMock()
sys.modules["streamlit"] = mock_streamlit

# 2. Мокаем strata
# ВАЖНО: Создаем объект и явно задаем ему __VERSION__, 
# иначе gui.py упадет при попытке прочитать версию для заголовка.
mock_strata = MagicMock()
mock_strata.__VERSION__ = "0.0.0_TEST" 
sys.modules["strata"] = mock_strata

# 3. Мокаем plotly
sys.modules["plotly"] = MagicMock()
sys.modules["plotly.express"] = MagicMock()
# ---------------

# Добавляем путь к gui.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui

def test_format_bytes_zero():
    assert gui.format_bytes(0) == "0 B"
    assert gui.format_bytes(None) == "0 B"

def test_format_bytes_simple():
    # 500 байт должны остаться 500 B
    assert gui.format_bytes(500) == "500.00 B"

def test_format_bytes_kb():
    # 1024 байта = 1 КБ
    assert gui.format_bytes(1024) == "1.00 KB"
    # 1536 байт = 1.5 КБ
    assert gui.format_bytes(1536) == "1.50 KB"

def test_format_bytes_mb():
    # Ровно 1 МБ
    assert gui.format_bytes(1024 * 1024) == "1.00 MB"

def test_format_bytes_gb():
    # 2.5 ГБ
    val = (1024 ** 3) * 2.5
    assert gui.format_bytes(val) == "2.50 GB"
