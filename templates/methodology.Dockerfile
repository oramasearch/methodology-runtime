# A methodology's own image: what Lake's build path generates for a workspace methodology
# (Lake passes the runtime image as the METHODOLOGY_RUNTIME_IMAGE build argument and builds from
# the methodology's directory in its repository). The locked packages are installed at build time,
# hashes required, wheels only; the run pod has no network to install anything later.
ARG METHODOLOGY_RUNTIME_IMAGE
FROM ${METHODOLOGY_RUNTIME_IMAGE}
ARG METHODOLOGY_PACKAGES=requirements.lock
USER root
COPY . /methodology
RUN /opt/orama/bin/install-locked /methodology "$METHODOLOGY_PACKAGES" \
 && chown -R 0:0 /methodology && chmod -R a+rX,go-w /methodology
USER 10001:10001
