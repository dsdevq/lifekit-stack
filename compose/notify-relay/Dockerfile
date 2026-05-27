# notify-relay — tiny HTTP webhook that turns devclaw's notify_url POST into
# a Telegram message via the gateway's `openclaw message send` CLI.
#
# Why a separate container: the gateway image is third-party; we shouldn't
# bake our own listener into it. The relay sits next to devclaw + gateway on
# the same compose network, accepts devclaw's terminal-state POST, and uses
# `docker exec` against the running gateway container to invoke the CLI.
#
# Trade-off: the relay needs read access to the docker socket. Acceptable for
# a single-user single-host stack; revisit if multi-tenant.

FROM node:22-trixie

# docker CLI for exec'ing into the gateway container.
# Debian trixie's `docker.io` package only ships the daemon, not the CLI.
# Pull the static CLI binary directly — small, no deps, version-pinned.
ARG DOCKER_VERSION=27.3.1
ARG TARGETARCH=arm64
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && ARCH_TAR=$(case "$TARGETARCH" in \
        amd64) echo "x86_64" ;; \
        arm64) echo "aarch64" ;; \
        *) echo "$TARGETARCH" ;; \
    esac) \
 && curl -fsSL "https://download.docker.com/linux/static/stable/${ARCH_TAR}/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /tmp \
 && mv /tmp/docker/docker /usr/local/bin/docker \
 && chmod +x /usr/local/bin/docker \
 && rm -rf /tmp/docker \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && docker --version

WORKDIR /app
COPY server.js /app/server.js

ENV NOTIFY_RELAY_PORT=8090 \
    GATEWAY_CONTAINER=compose-openclaw-gateway-1

EXPOSE 8090

USER node
CMD ["node", "server.js"]
