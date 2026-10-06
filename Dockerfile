FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# не от root
RUN useradd --create-home --uid 1000 app
USER app

CMD ["uvicorn", "ralwallet.main:app", "--host", "0.0.0.0", "--port", "8000"]
