#!/bin/bash
cd /home/user/TradingAV

/home/user/TradingAV/venv/bin/python /home/user/TradingAV/main_valores.py &

/home/user/TradingAV/venv/bin/streamlit run /home/user/TradingAV/streamlit_app.py --server.port 8501 --server.headless true &

wait
