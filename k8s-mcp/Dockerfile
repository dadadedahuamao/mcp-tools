FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
WORKDIR /app
COPY pyproject.toml README.md ./
COPY k8s_mcp ./k8s_mcp
COPY main.py ./
RUN pip install . && groupadd --system --gid 10001 k8smcp && useradd --system --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin k8smcp && mkdir -p /etc/k8s-mcp && chown -R k8smcp:k8smcp /app /etc/k8s-mcp
USER 10001:10001
EXPOSE 18002
ENTRYPOINT ["python", "main.py"]
CMD ["--transport", "streamable-http", "--env-file", "/etc/k8s-mcp/env.yaml", "--kubeconfig-dir", "/etc/k8s-mcp", "--host", "0.0.0.0", "--port", "18002"]
