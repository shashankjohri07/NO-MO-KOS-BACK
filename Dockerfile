FROM node:22-slim

# System deps: Python3, Tesseract OCR, and libs required by PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    tesseract-ocr \
    tesseract-ocr-eng \
    libmupdf-dev \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip3 install --no-cache-dir --break-system-packages \
    pymupdf \
    pytesseract \
    pillow

WORKDIR /app

COPY package*.json ./
RUN npm install --omit=dev

COPY . .

EXPOSE 3001

CMD ["npx", "tsx", "server.ts"]
