FROM alpine:3.20

RUN apk add --no-cache \
      python3 \
      coreutils findutils grep sed gawk less vim nano ncurses \
      file tree vim nano \
      procps htop \
      mandoc man-pages \
 && adduser -D -u 1000 sandboxuser

COPY shell/app /opt/myshell/app/

ENV HOME=/home/sandboxuser \
    TERM=xterm-256color \
    PYTHONPATH=/opt/myshell \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/sandboxuser
USER sandboxuser

CMD ["python3", "-m", "app.main"]
