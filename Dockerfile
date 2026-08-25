FROM python:3.11-slim

WORKDIR /app

COPY credits_server.py ./
COPY v3.html fomo.html admin.html v1.html v2.html ./
COPY testimonials.json ./
COPY photo-campus-aerial.webp facility.webp racemind.webp janus.webp ./

ENV BASE_URL=http://10.220.0.2:8090
ENV PORT=8090

EXPOSE 8090

CMD ["python", "credits_server.py"]
