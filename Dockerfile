FROM python:3.11-slim

WORKDIR /app

COPY credits_server.py ./
COPY schema.sql ./
COPY v3.html fomo.html admin.html v1.html v2.html ./
COPY photo-campus-aerial.webp facility.webp racemind.webp janus.webp ./

ENV PORT=8090

EXPOSE 8090

CMD ["python", "credits_server.py"]
