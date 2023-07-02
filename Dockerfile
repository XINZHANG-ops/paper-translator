FROM python:3.9-slim-buster
WORKDIR /app
COPY . .
RUN apt-get -y update
RUN apt-get -y upgrade
RUN pip3 install -r requirements.txt
RUN pip3 install --force-reinstall charset-normalizer==3.1.0

WORKDIR /app
COPY . .
ENTRYPOINT ["python3"]
CMD ["app.py"]



