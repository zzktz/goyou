FROM python:3.12-alpine

RUN apk add --no-cache ca-certificates
COPY --from=ghcr.io/sagernet/sing-box:latest /usr/local/bin/sing-box /usr/local/bin/sing-box
COPY metering_adapter.py /usr/local/bin/goyou-metering

RUN chmod 0755 /usr/local/bin/goyou-metering \
    && addgroup -S goyou \
    && adduser -S -G goyou goyou \
    && mkdir -p /var/lib/goyou \
    && chown -R goyou:goyou /var/lib/goyou

USER goyou
ENTRYPOINT ["/usr/local/bin/goyou-metering"]
