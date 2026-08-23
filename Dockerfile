FROM python:3.11-slim

WORKDIR /app

COPY credits_server.py ./
COPY v3.html fomo.html admin.html careers-v1.html careers-v2.html careers-admin.html submissions.json ./

ENV BASE_URL=http://10.220.3.168:8090
ENV PORT=8090

EXPOSE 8090

CMD ["python", "credits_server.py"]
