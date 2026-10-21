# Screenshot renderer for the documentation: Chromium headless plus the free
# fonts the demo site uses. Built and run by tools/make-screenshots.sh.
FROM debian:trixie-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      chromium fonts-urw-base35 fonts-dejavu fonts-liberation fonts-noto-color-emoji python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
ENTRYPOINT ["python3", "/work/tools/screenshots/compose.py"]
