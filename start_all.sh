#!/bin/bash
cd /root/TradingAV

/root/TradingAV/venv/bin/python /root/TradingAV/main_valores.py &

/root/TradingAV/venv/bin/streamlit run /root/TradingAV/streamlit_app.py --server.port 8501 --server.headless true &

wait
