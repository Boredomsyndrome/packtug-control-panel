FROM python:3.14-slim

LABEL org.opencontainers.image.title="packtug webctl hub" \
      org.opencontainers.image.description="Fused FastAPI hub: robot plane + arm/vision bridge + combined SPA (v4.1.0)"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# opencv-headless runtime libs (no libGL needed for headless wheels)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# deps first for layer caching
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

# the app: ONE FastAPI backend (robot plane + arm/vision bridge) + BUILT
# frontend (dist/ is gitignored — run `npm run build` before docker build;
# see docker.md). robot/ is NOT in the image: the robot is never containerized.
COPY VERSION /app/VERSION
COPY backend/ /app/backend/
COPY dist/ /app/dist/

# waypoints + lock live outside the image
ENV WEBCTL_DATA_DIR=/data
RUN mkdir -p /data
VOLUME /data

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8090/api/health', timeout=3).read()"

# same flags the live tmux hub runs with (ws keepalive tuned for robot VPNs)
CMD ["python3", "-m", "uvicorn", "webctl.asgi:application", \
     "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8090", \
     "--ws-ping-interval", "30", "--ws-ping-timeout", "120"]
