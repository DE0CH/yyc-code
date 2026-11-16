# The YYC news stream image (news-stream/ARCHITECTURE.md): the app's code + Python deps, Google Chrome + Patchright +
# Xvfb for the browser jobs (channel="chrome"), Noto CJK fonts for the report charts and PDFs. One image for every
# workload in news-stream/deploy/ (web, poller, CronJobs, kmatrix); the command differs per workload.
# Built by .github/workflows/news-stream-image.yml as ghcr.io/de0ch/yyc-news-stream:<sha>. Build context: news-stream/.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends ca-certificates curl python3 python3-venv xvfb xauth fonts-noto-cjk tzdata \
 && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -yq /tmp/chrome.deb && rm -f /tmp/chrome.deb \
 && rm -rf /var/lib/apt/lists/* \
 && google-chrome --version
# the versions the old server's /opt/yyc-venv runs (pip freeze, 2026-10-13)
RUN python3 -m venv /opt/yyc-venv \
 && /opt/yyc-venv/bin/pip install --no-cache-dir -q patchright==1.63.0 curl_cffi==0.16.3 pillow==12.3.0 openpyxl==3.1.5 \
      python-docx==1.2.0 matplotlib==3.11.2 lxml==6.1.3 'gunicorn==23.0.0' 'psycopg[binary]==3.3.6' psycopg-pool==3.3.3 openfeature-sdk==0.10.0 openfeature-provider-flagd==0.5.2 \
 && /opt/yyc-venv/bin/python -c "import patchright, curl_cffi, PIL, openpyxl, docx, matplotlib, lxml, gunicorn, psycopg, psycopg_pool, openfeature.contrib.provider.flagd"
# media.py and broadcast_fetch.py (transcripts of articles' clips and of TV/radio news): ffmpeg reads the audio
# (HLS/MP4/MP3) and cuts it into pieces for the model, yt-dlp fetches YouTube / Facebook / geo-restricted audio through
# the residential proxy; the speech-to-text model itself is on OpenRouter (media.MODEL)
RUN apt-get update -q && apt-get install -yq --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
 && /opt/yyc-venv/bin/pip install --no-cache-dir -q yt-dlp==2026.8.19 \
 && ffmpeg -version | head -1 && /opt/yyc-venv/bin/yt-dlp --version
# Chinese search (searchtext.py): OpenCC (the C++ library's own Python package: hk2s for the search copy, t2hk for
# transcripts) and jieba, whose dictionary model is built once here (JIEBA_CACHE_DIR) so a process loads it in
# under a second instead of rebuilding it
RUN /opt/yyc-venv/bin/pip install --no-cache-dir -q opencc==1.4.2 jieba==0.42.1 \
 && mkdir -p /opt/jieba \
 && /opt/yyc-venv/bin/python -c "import jieba; t = jieba.Tokenizer(); t.tmp_dir = '/opt/jieba'; t.initialize(); import opencc; assert opencc.OpenCC('hk2s').convert('裡綫選舉') == '里线选举'" \
 && chmod -R a+rX /opt/jieba && ls /opt/jieba
# cache_fetch.py reads PDFs (醫院管理局's press releases, 行政公職局's tender results, 中國融資's issues): PyMuPDF
RUN /opt/yyc-venv/bin/pip install --no-cache-dir -q pymupdf==1.28.2 && /opt/yyc-venv/bin/python -c "import pymupdf"
# whatsapp/gateway.js (WhatsApp alerts, whatsapp_alerts.py): Baileys on Node.js 22, nodejs.org's own build (its
# checksum checked against the release's SHASUMS256.txt)
ARG NODE_VERSION=v22.23.3
RUN curl -fsSLO https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-x64.tar.gz \
 && curl -fsSL https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt | grep " node-$NODE_VERSION-linux-x64.tar.gz\$" | sha256sum -c - \
 && tar -xzf node-$NODE_VERSION-linux-x64.tar.gz -C /opt && rm node-$NODE_VERSION-linux-x64.tar.gz \
 && ln -s /opt/node-$NODE_VERSION-linux-x64/bin/node /usr/local/bin/node && ln -s /opt/node-$NODE_VERSION-linux-x64/bin/npm /usr/local/bin/npm \
 && node --version
COPY whatsapp/package.json whatsapp/package-lock.json /app/whatsapp/
RUN cd /app/whatsapp && npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
# uid/gid 10001 own the data volumes (pods run with fsGroup 10001); HOME is the pod's scratch dir
RUN groupadd -g 10001 yyc && useradd -u 10001 -g 10001 -d /home/yyc -m -s /bin/bash yyc
ENV PATH=/opt/yyc-venv/bin:$PATH HOME=/home/yyc DATA_DIR=/var/lib/yyc/data KMATRIX_DIR=/var/lib/yyc/kmatrix \
    CHROME=/usr/bin/google-chrome JIEBA_CACHE_DIR=/opt/jieba
COPY --chown=10001:10001 . /app
WORKDIR /app
USER 10001
ARG GIT_SHA=dev
ENV GIT_SHA=$GIT_SHA
EXPOSE 8080
CMD ["gunicorn", "--chdir", "/app", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "8", "--timeout", "300", \
     "--graceful-timeout", "300", "--keep-alive", "5", "app:application"]
