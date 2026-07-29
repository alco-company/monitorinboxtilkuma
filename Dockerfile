FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml setup.cfg setup.py README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

VOLUME ["/data"]

CMD ["python", "-m", "monitorinbox2kuma.main"]
