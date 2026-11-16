# The AI watch image (ai-watch/README.md): the app's code + Python deps, Chrome for Testing at a pinned version (see
# aiwatch/browser.py for why not Google Chrome stable) + Xvfb for the headed browser, Noto CJK fonts. One image for the
# web and the worker; the command differs. Built by .github/workflows/ai-watch-image.yml as ghcr.io/de0ch/yyc-ai-watch:<sha>.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Asia/Hong_Kong
ARG CFT_VERSION=151.0.7922.34
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends ca-certificates curl unzip python3 python3-venv xvfb xauth fonts-noto-cjk tzdata \
 && curl -fsSL -o /tmp/cft.zip "https://storage.googleapis.com/chrome-for-testing-public/${CFT_VERSION}/linux64/chrome-linux64.zip" \
 && unzip -q /tmp/cft.zip -d /opt && mv /opt/chrome-linux64 /opt/chrome && rm /tmp/cft.zip \
 && rm -rf /var/lib/apt/lists/*
# the shared libraries Chrome links (Ubuntu 24.04 names), then a check that it starts
RUN apt-get update -q && apt-get install -yq --no-install-recommends libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
      libcups2t64 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
      libasound2t64 libxshmfence1 libdbus-1-3 libexpat1 libglib2.0-0t64 libx11-6 libxcb1 libxext6 libudev1 \
 && rm -rf /var/lib/apt/lists/* && /opt/chrome/chrome --version
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir -q patchright==1.63.0 curl_cffi==0.16.3 lxml==6.1.3 'psycopg[binary]==3.3.6' psycopg-pool==3.3.3 \
      json-repair==0.64.2 \
 && /opt/venv/bin/python -c "import patchright, curl_cffi, lxml, psycopg, psycopg_pool, json_repair"
ENV PATH=/opt/venv/bin:$PATH CHROME_PATH=/opt/chrome/chrome
WORKDIR /app
COPY aiwatch /app/aiwatch
COPY run-worker.sh /app/run-worker.sh
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA
RUN useradd -u 10001 -m app && chmod +x /app/run-worker.sh
USER 10001
CMD ["python", "-m", "aiwatch.web"]
