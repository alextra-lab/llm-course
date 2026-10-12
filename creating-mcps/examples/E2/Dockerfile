# E2: the local systems server's sandbox image. Build from creating-mcps/:
#
#     docker build -f examples/E2/Dockerfile -t creating-mcps-local .
#
# Python and the MCP SDK, the one server file, and nothing else: no shell tools are needed,
# because the server runs none. sandbox.py runs it with no network, a read-only filesystem,
# no capabilities and as nobody, with the host to examine mounted read-only at /host.
FROM python:3.13-slim

RUN pip install --no-cache-dir "mcp>=2,<3"
COPY examples/E2/local_server.py /app/local_server.py

USER 65534:65534
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-I", "/app/local_server.py", "--root", "/host"]
