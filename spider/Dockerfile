FROM alpine:3.20

RUN apk add --no-cache sqlite bash findutils

COPY scripts/setup-spider-dbs.sh /usr/local/bin/setup-spider-dbs.sh

RUN chmod +x /usr/local/bin/setup-spider-dbs.sh

WORKDIR /workspace