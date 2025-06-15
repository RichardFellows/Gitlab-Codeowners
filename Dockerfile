FROM registry.access.redhat.com/ubi8/python-39
USER 0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5001
USER 1001
CMD ["./start.sh"]