FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pulse_simulator.py .

CMD ["python", "-u", "pulse_simulator.py"]
