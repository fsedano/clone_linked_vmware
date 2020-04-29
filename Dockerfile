FROM ubuntu:18.04
# docker run -ti  -v `pwd`/app:/app:delegated pyvmomi bash
# Install base packages
RUN apt-get update && apt-get -y install python3 python3-pip git

RUN pip3 install --upgrade pip
RUN pip3 install --upgrade virtualenv

COPY app/* /app/
WORKDIR /app
RUN pip3 install -r requirements.txt
RUN git clone https://github.com/vmware/pyvmomi-community-samples.git /pyvmomi-examples
ENV PYTHONPATH="${PYTHONPATH}:/pyvmomi-examples/samples"

