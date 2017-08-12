FROM postgres:9.6.3
ENV POSTGRES_USER rowboat
COPY postgres-healthcheck.sh /usr/local/bin/
COPY initdb.sh /docker-entrypoint-initdb.d/
HEALTHCHECK CMD ["postgres-healthcheck.sh"]
