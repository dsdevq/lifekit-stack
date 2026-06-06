# devclaw-sandbox + .NET SDK — the per-task box for .NET projects
# (lifekit-dashboard, finance-sentry). Layered on the base devclaw-sandbox so
# python + node + claude + claude-agent-acp + openhands-sdk + the runner + the
# agent user are all REUSED unchanged; this only adds the .NET toolchain so the
# agent can build/run/test C# and the verify gate can run `dotnet test` INSIDE
# the sandbox.
#
# Mirrors devclaw's own .sandcastle/Dockerfile.dotnet. devclaw-mcp selects it
# per deployment via DEVCLAW_SANDBOX_IMAGE=devclaw-sandbox-dotnet:local.
# Build order matters: devclaw-sandbox:local must exist first (this FROMs it).
FROM devclaw-sandbox:local

# Install as root, then drop back to the inherited non-root agent user.
USER root

ARG DOTNET_CHANNEL=10.0
ENV DOTNET_ROOT=/usr/share/dotnet \
    PATH=/usr/share/dotnet:/usr/local/bin:/usr/bin:/bin \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
    NUGET_XMLDOC_MODE=skip

# .NET's runtime needs ICU + OpenSSL + Kerberos libs that the slim base omits;
# libicu-dev is version-agnostic (apt resolves the right libicuNN as a dep).
# Then install the SDK via Microsoft's script into a world-readable dir so the
# agent user can run it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libicu-dev libssl3 libgssapi-krb5-2 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
 && bash /tmp/dotnet-install.sh --channel "${DOTNET_CHANNEL}" --install-dir "${DOTNET_ROOT}" \
 && rm /tmp/dotnet-install.sh \
 && chmod -R a+rX "${DOTNET_ROOT}" \
 && ln -sf "${DOTNET_ROOT}/dotnet" /usr/local/bin/dotnet \
 && dotnet --info

USER agent
# dotnet writes to $HOME/.nuget + $HOME/.dotnet (the agent's writable home) —
# not the read-only claude config dir — so restores/builds work without extra
# mounts.
