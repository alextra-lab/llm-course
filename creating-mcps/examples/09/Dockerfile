# Unit 9: one image for both gateway servers. Build from creating-mcps/:
#
#     docker build -f examples/09/Dockerfile -t creating-mcps-gateway .
#
# Configuration and secrets come from the environment at run time, never from the image
# (.dockerignore keeps env/.env and env/keys/ out). The issuer's PUBLIC key is mounted.
FROM python:3.13-slim

WORKDIR /app
# Only what the servers import: no marimo, no model client.
RUN pip install --no-cache-dir "mcp>=2,<3" "psycopg[binary,pool]>=3.2" "opensearch-py>=3.0" \
        "sqlglot>=30" "pyjwt[crypto]>=2.8" "redis>=5"
COPY examples/ examples/

# Not root: the process can read its code and the mounted key, and write nothing else.
RUN useradd --uid 10001 --no-create-home mcp
USER mcp

ENV PYTHONUNBUFFERED=1 MCP_ROUND_NOW=60
EXPOSE 8727
HEALTHCHECK --interval=10s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8727/healthz')"
ENTRYPOINT ["python", "examples/09/scaled_server.py", "--host", "0.0.0.0", "--port", "8727"]
CMD ["--kind", "cmdb"]
