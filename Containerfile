FROM registry.fedoraproject.org/fedora:41

USER root

RUN dnf update -y && dnf install -y python3-pip git make diffutils && dnf clean all

ENV GO_VERSION=1.25.9
RUN curl -Ls https://golang.org/dl/go${GO_VERSION}.linux-amd64.tar.gz | \
    tar -C /usr/local -zxvf -
ENV PATH="/usr/local/go/bin:$PATH"

WORKDIR /src
COPY . .
RUN python -m pip install .

WORKDIR /working

ENTRYPOINT [ "/usr/local/bin/merge-bot" ]
