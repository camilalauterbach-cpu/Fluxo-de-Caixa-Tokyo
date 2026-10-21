# Official CPython 3.13.16 image, resolved and tested on 2026-10-21.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
FROM ${PYTHON_IMAGE} AS protected-payload
WORKDIR /build
COPY downloads/Fluxo-de-Caixa-Tokyo-Trabalho.zip /build/tokyo.zip
RUN python -I -B -c "from pathlib import Path; from zipfile import ZipFile; z = ZipFile('/build/tokyo.zip'); Path('/build/bootstrap.enc').write_bytes(z.read('Tokyo-Trabalho/pacote-tokyo.enc'))"

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.source="https://github.com/camilalauterbach-cpu/Fluxo-de-Caixa-Tokyo"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOKYO_PRODUCTION=1 \
    TOKYO_HOST=0.0.0.0 \
    TOKYO_DB_PATH=/var/data/private/tokyo.sqlite3 \
    PORT=10000
WORKDIR /opt/tokyo/app
COPY deploy/requirements.txt /opt/tokyo/deploy/requirements.txt
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python -m pip install --no-cache-dir --disable-pip-version-check -r /opt/tokyo/deploy/requirements.txt \
    && groupadd --gid 10001 tokyo \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin tokyo \
    && install -d -o 10001 -g 10001 -m 0700 /var/data/private
COPY demo/server/ /opt/tokyo/app/server/
COPY demo/dist/ /opt/tokyo/app/dist/
COPY demo/package.json /opt/tokyo/app/package.json
COPY tools/working/desbloquear_tokyo.py /opt/tokyo/tools/working/desbloquear_tokyo.py
COPY deploy/manage.py deploy/healthcheck.py /opt/tokyo/deploy/
COPY --from=protected-payload /build/bootstrap.enc /opt/tokyo/bootstrap.enc
RUN chmod -R u=rwX,go=rX /opt/tokyo
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-I", "-B", "/opt/tokyo/deploy/healthcheck.py"]
# manage.py prepares only the mounted private directory as root, then drops to
# UID/GID 10001 before reading financial data or starting the HTTP server.
ENTRYPOINT ["python", "-I", "-B", "-u", "/opt/tokyo/deploy/manage.py"]
CMD ["serve"]
