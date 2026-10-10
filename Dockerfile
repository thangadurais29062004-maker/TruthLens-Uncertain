FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home truthlens && mkdir -p /app/data && chown -R truthlens:truthlens /app
USER truthlens
ENV TRUTHLENS_BIND=0.0.0.0 PORT=8000
EXPOSE 8000
CMD ["python", "server.py"]
