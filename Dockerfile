FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation . \
    && useradd --create-home --uid 1000 trader \
    && mkdir /state && chown trader:trader /state
COPY config ./config
USER trader
VOLUME ["/state"]
ENTRYPOINT ["compounder"]
CMD ["run", "--config", "config/paper.toml", "--state-dir", "/state"]
