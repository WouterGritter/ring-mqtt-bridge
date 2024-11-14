FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt requirements.txt

RUN pip3 install -r requirements.txt

COPY static/* ./static/
COPY *.py ./

ENV TOKEN_CACHE_FILE /etc/ring-mqtt-bridge/token.cache
ENV CREDENTIALS_CACHE_FILE /etc/ring-mqtt-bridge/credentials.cache
ENV DEVICES_CONFIG_FILE /etc/ring-mqtt-bridge/devices.yml

ARG IMAGE_VERSION=Unknown
ENV IMAGE_VERSION=${IMAGE_VERSION}

CMD [ "python3", "-u", "main.py" ]
