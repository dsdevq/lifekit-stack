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

# docker CLI for exec'ing into the gateway container
RUN apt-get update \
 && apt-get install -y --no-install-recommends docker.io ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.js /app/server.js

ENV NOTIFY_RELAY_PORT=8090 \
    GATEWAY_CONTAINER=compose-openclaw-gateway-1

EXPOSE 8090

USER node
CMD ["node", "server.js"]
