"""Run locally: python run.py (first start creates isolated demo data)."""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('app.main:create_app', factory=True, host=os.getenv('HOST', '127.0.0.1'),
                port=int(os.getenv('PORT', '8000')), reload=False, log_level='info')
