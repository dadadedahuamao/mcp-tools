FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
WORKDIR /app
COPY pyproject.toml README.md ./
COPY redis_mcp ./redis_mcp
COPY main.py ./
RUN pip install . && groupadd --system --gid 10001 redismcp && useradd --system --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin redismcp && mkdir -p /etc/redis-mcp && chown -R redismcp:redismcp /app /etc/redis-mcp
USER 10001:10001
EXPOSE 18003
ENTRYPOINT ["python", "main.py"]
CMD ["--transport", "streamable-http", "--env-file", "/etc/redis-mcp/env.yaml", "--host", "0.0.0.0", "--port", "18003"]
