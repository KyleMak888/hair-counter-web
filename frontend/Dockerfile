ARG DOCKER_REGISTRY=docker.m.daocloud.io
FROM ${DOCKER_REGISTRY}/library/nginx:1.27-alpine
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY frontend/index.html /usr/share/nginx/html/index.html
COPY frontend/app.js /usr/share/nginx/html/app.js
COPY frontend/batch-export.js /usr/share/nginx/html/batch-export.js
COPY frontend/zip.js /usr/share/nginx/html/zip.js
COPY frontend/xlsx.js /usr/share/nginx/html/xlsx.js
COPY frontend/styles.css /usr/share/nginx/html/styles.css
EXPOSE 80
