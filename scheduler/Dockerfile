ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/scheduler

COPY pyproject.toml uv.lock ./

RUN pip install --upgrade pip \
    && pip install uv==0.11.20 \
    && uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock \
    && pip install --requirement requirements.lock

COPY app ./app

RUN pip install --no-deps .

RUN useradd --create-home --uid 10001 scheduler \
    && chown -R scheduler:scheduler /srv/scheduler

USER scheduler

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
