#!/usr/bin/env python
"""
Entrypoint para rodar o collector como processo independente.
Uso: python run_collector.py
"""
import logging
from app.db import init_db
from app.collector import run_collector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == "__main__":
    init_db()
    run_collector()
