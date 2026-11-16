# oramasearch/methodology-runtime: the sandbox runtime for a Lake workspace's own methodologies.
#
# The stage and collect pods run this image as it is; a methodology's own image is built FROM it
# (see docs/cli.md#a-methodologys-image) and the run pod runs that. The harness is the standard
# library only, so the image carries no third-party package of its own.
#
# python:3.12.12-slim-bookworm, pinned by its multi-platform index digest.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

LABEL org.opencontainers.image.title="methodology-runtime" \
      org.opencontainers.image.source="https://github.com/oramasearch/methodology-runtime" \
      org.opencontainers.image.description="Sandbox runtime for Lake workspace methodologies"

# PYTHONSAFEPATH keeps the working directory (/work, which the run pod writes) off sys.path for any
# python3 started in the image; bin/methodology also passes -P (methodology-runtime#4).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONSAFEPATH=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1 \
    PATH=/opt/orama/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

COPY orama_methodology /opt/orama/lib/orama_methodology
COPY bin/methodology bin/install-locked /opt/orama/bin/

RUN groupadd --system --gid 10001 methodology \
 && useradd --system --uid 10001 --gid 10001 --home-dir /work --no-create-home --shell /usr/sbin/nologin methodology \
 && mkdir -p /work/in /work/out /methodology \
 && chown -R 10001:10001 /work \
 && chmod 0755 /opt/orama/bin/methodology /opt/orama/bin/install-locked \
 && python3 -m compileall -q /opt/orama/lib

WORKDIR /work
USER 10001:10001
ENTRYPOINT ["/opt/orama/bin/methodology"]
CMD ["--help"]
