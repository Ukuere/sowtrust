"""
AgriHub Global — Application Entry Point
Development: python run.py
Production:  gunicorn -w 4 -b 0.0.0.0:5000 "run:app"
"""
from app import create_app
from config.settings import config

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG)
