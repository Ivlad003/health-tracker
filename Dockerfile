FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY newrelic.ini .

EXPOSE 8000

ENV NEW_RELIC_CONFIG_FILE=newrelic.ini

CMD ["newrelic-admin", "run-program", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
