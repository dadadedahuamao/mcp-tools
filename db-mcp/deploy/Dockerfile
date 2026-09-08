FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

WORKDIR /app

COPY pyproject.toml ./
COPY db_mcp ./db_mcp
COPY main.py ./

RUN pip install . \
    && groupadd --system --gid 10001 dbmcp \
    && useradd --system --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin dbmcp \
    && mkdir -p /etc/db-mcp \
    && chown -R dbmcp:dbmcp /app /etc/db-mcp

USER 10001:10001

EXPOSE 18000

ENTRYPOINT ["python", "main.py"]
CMD ["--config-file", "/etc/db-mcp/env.yaml", "--transport", "streamable-http"]
