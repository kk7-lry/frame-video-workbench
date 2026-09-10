FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRAME_PUBLIC=1 FRAME_DATA=/tmp/frame-public PORT=10000 FRAME_BROWSER=1 FRAME_CHROMIUM=/usr/bin/chromium
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr tesseract-ocr-chi-sim ca-certificates chromium && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 frame
COPY server.py link_resolver.py platform_auth.py public_access.py media_tools.py browser_resolver.py ./
COPY index.html app.js style.css favicon.svg ./
COPY tests/fixtures/chinese.png tests/fixtures/chinese.png
USER frame
EXPOSE 10000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','10000')+'/healthz',timeout=4)"
CMD ["python", "server.py"]
