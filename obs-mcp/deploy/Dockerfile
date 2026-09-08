FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

WORKDIR /app
COPY pyproject.toml ./
COPY obs_mcp ./obs_mcp
COPY main.py ./
RUN pip install . \
    && groupadd --system --gid 10001 obsmcp \
    && useradd --system --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin obsmcp \
    && mkdir -p /etc/obs-mcp \
    && chown -R obsmcp:obsmcp /app /etc/obs-mcp

USER 10001:10001
EXPOSE 18004
ENTRYPOINT ["python", "main.py"]
CMD ["--transport", "streamable-http", "--env-file", "/etc/obs-mcp/env.yaml", "--host", "0.0.0.0", "--port", "18004"]
