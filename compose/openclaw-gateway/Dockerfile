# Extends upstream OpenClaw with ffmpeg + openai-whisper for voice transcription.
# Without this, OpenClaw's tools.media.audio config has nothing to shell out to
# and Telegram voice notes silently fail to transcribe.
ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:latest
FROM ${OPENCLAW_BASE_IMAGE}

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        python3 \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv keeps us out of PEP 668 territory and away from any
# system-python conflicts inside the upstream image.
RUN python3 -m venv /opt/whisper \
    && /opt/whisper/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/whisper/bin/pip install --no-cache-dir openai-whisper \
    && ln -s /opt/whisper/bin/whisper /usr/local/bin/whisper

# Pre-fetch the `base` model into the runtime user's cache so first
# invocation is instant and works without network. Mirrors PC config.
RUN mkdir -p /home/node/.cache \
    && XDG_CACHE_HOME=/home/node/.cache /opt/whisper/bin/python -c "import whisper; whisper.load_model('base')" \
    && chown -R node:node /home/node/.cache

# Video transcription wrapper: ffmpeg strips audio → whisper transcribes.
# Used by tools.media.video CLI fallback since Claude Pro doesn't cover
# video understanding (matrix is Google/Qwen/Moonshot only).
COPY video-transcribe /usr/local/bin/video-transcribe
RUN chmod 0755 /usr/local/bin/video-transcribe

USER node
