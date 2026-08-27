#!/usr/bin/env python
"""
Entrypoint para rodar o painel web FastAPI.
Uso: python run_web.py
Ou: uvicorn app.main:app --reload
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8088, reload=True)
